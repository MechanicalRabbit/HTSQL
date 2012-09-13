#
# Copyright (c) 2006-2012, Prometheus Research, LLC
#


from ....core.util import listof
from ....core.adapter import Utility, adapt
from ....core.error import BadRequestError
from ....core.entity import TableEntity, ColumnEntity
from ....core.classify import localize, relabel
from ....core.connect import transaction, scramble, unscramble
from ....core.domain import IdentityDomain, RecordDomain, ListDomain
from ....core.cmd.retrieve import Product, build_retrieve
from ....core.cmd.act import Act, ProduceAction, produce
from ....core.tr.bind import BindingState, Select
from ....core.tr.syntax import VoidSyntax, IdentifierSyntax
from ....core.tr.binding import (VoidBinding, RootBinding, FormulaBinding,
        LocatorBinding, SelectionBinding, SieveBinding, AliasBinding,
        SegmentBinding, QueryBinding, FreeTableRecipe, ColumnRecipe)
from ....core.tr.signature import IsEqualSig, AndSig, PlaceholderSig
from ....core.tr.decorate import decorate
from ....core.tr.coerce import coerce
from ....core.tr.lookup import identify
from .command import MergeCmd
from .insert import (BuildExtractNode, BuildExtractTable, BuildExecuteInsert,
        BuildResolveIdentity, BuildResolveChain)
from ..tr.dump import serialize_update
import itertools


class ExtractIdentityPipe(object):

    def __init__(self, node, arcs, id_indices, other_indices):
        self.node = node
        self.arcs = arcs
        self.id_indices = id_indices
        self.other_indices = other_indices

    def __call__(self, row):
        return (tuple(row[idx] for idx in self.id_indices),
                tuple(row[idx] for idx in self.other_indices))


class BuildExtractIdentity(Utility):

    def __init__(self, node, arcs):
        self.node = node
        self.arcs = arcs

    def __call__(self):
        identity_arcs = localize(self.node)
        if identity_arcs is None:
            raise BadRequestError("a table with identity is expected")
        index_by_arc = dict((arc, index) for index, arc in enumerate(self.arcs))
        id_indices = []
        for arc in identity_arcs:
            if arc not in index_by_arc:
                labels = relabel(arc)
                if not labels:
                    raise BadRequestError("missing identity field")
                else:
                    label = labels[0]
                    raise BadRequestError("missing identity field %s"
                                          % label.name.encode('utf-8'))
            index = index_by_arc[arc]
            id_indices.append(index)
        other_indices = []
        arcs = []
        for idx, arc in enumerate(self.arcs):
            if arc in identity_arcs:
                continue
            other_indices.append(idx)
            arcs.append(arc)
        return ExtractIdentityPipe(self.node, arcs, id_indices, other_indices)


class ResolveKeyPipe(object):

    def __init__(self, columns, domain, pipe):
        self.columns = columns
        self.pipe = pipe
        self.leaves = domain.leaves

    def __call__(self, value):
        assert value is not None
        raw_values = []
        for leaf in self.leaves:
            raw_value = value
            for idx in leaf:
                raw_value = raw_value[idx]
            raw_values.append(raw_value)
        product = self.pipe(raw_values)
        data = product.data
        assert len(data) <= 1
        if data:
            return data[0]
        return None


class BuildResolveKey(Utility):

    def __init__(self, table):
        self.table = table

    def __call__(self):
        state = BindingState()
        syntax = VoidSyntax()
        scope = RootBinding(syntax)
        state.set_root(scope)
        seed = state.use(FreeTableRecipe(self.table), syntax)
        recipe = identify(seed)
        if recipe is None:
            raise BadRequestError("cannot determine identity of a link")
        identity = state.use(recipe, syntax, scope=seed)
        count = itertools.count()
        def make_value(domain):
            value = []
            for field in domain.fields:
                if isinstance(field, IdentityDomain):
                    item = make_value(field)
                else:
                    item = FormulaBinding(scope,
                                          PlaceholderSig(next(count)),
                                          field,
                                          syntax)
                value.append(item)
            return tuple(value)
        value = make_value(identity.domain)
        scope = LocatorBinding(scope, seed, identity, value, syntax)
        state.push_scope(scope)
        columns = []
        if self.table.primary_key is not None:
            columns = self.table.primary_key.origin_columns
        else:
            for key in self.table.unique_keys:
                if key.is_partial:
                    continue
                if all(not column.is_nullable
                       for column in key.origin_columns):
                    rcolumns = key.origin_columns
                    break
        if not columns:
            raise BadRequestError("table does not have a primary key")
        elements = []
        for column in columns:
            binding = state.use(ColumnRecipe(column), syntax)
            elements.append(binding)
        fields = [decorate(element) for element in elements]
        domain = RecordDomain(fields)
        scope = SelectionBinding(scope, elements, domain, syntax)
        binding = Select.__invoke__(scope, state)
        domain = ListDomain(binding.domain)
        binding = SegmentBinding(state.root, binding, domain, syntax)
        profile = decorate(binding)
        binding = QueryBinding(state.root, binding, profile, syntax)
        pipe =  build_retrieve(binding)
        domain = identity.domain
        return ResolveKeyPipe(columns, domain, pipe)


class ExecuteUpdatePipe(object):

    def __init__(self, table, input_columns, key_columns,
                 output_columns, sql):
        assert isinstance(table, TableEntity)
        assert isinstance(input_columns, listof(ColumnEntity))
        assert isinstance(key_columns, listof(ColumnEntity))
        assert isinstance(output_columns, listof(ColumnEntity))
        assert isinstance(sql, unicode)
        self.table = table
        self.input_columns = input_columns
        self.key_columns = key_columns
        self.output_columns = output_columns
        self.sql = sql
        self.input_converts = [scramble(column.domain)
                               for column in input_columns]
        self.key_converts = [scramble(column.domain)
                             for column in key_columns]
        self.output_converts = [unscramble(column.domain)
                                for column in output_columns]

    def __call__(self, key_row, row):
        key_row = tuple(convert(item)
                        for item, convert in zip(key_row, self.key_converts))
        row = tuple(convert(item)
                    for item, convert in zip(row, self.input_converts))
        with transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(self.sql.encode('utf-8'), row+key_row)
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise BadRequestError("unable to locate updated row")
            [row] = rows
        return row


class BuildExecuteUpdate(Utility):

    def __init__(self, table, columns):
        assert isinstance(table, TableEntity)
        assert isinstance(columns, listof(ColumnEntity))
        self.table = table
        self.columns = columns

    def __call__(self):
        table = self.table
        returning_columns = []
        if table.primary_key is not None:
            returning_columns = table.primary_key.origin_columns
        else:
            for key in table.unique_keys:
                if key.is_partial:
                    continue
                if all(not column.is_nullable
                       for column in key.origin_columns):
                    returning_columns = key.origin_columns
                    break
        if not returning_columns:
            raise BadRequestError("table does not have a primary key")
        sql = serialize_update(table, self.columns, returning_columns,
                               returning_columns)
        return ExecuteUpdatePipe(table, self.columns, returning_columns,
                                 returning_columns, sql)


class ProduceMerge(Act):

    adapt(MergeCmd, ProduceAction)

    def __call__(self):
        with transaction() as connection:
            product = produce(self.command.feed)
            extract_node = BuildExtractNode.__invoke__(product.meta)
            extract_table = BuildExtractTable.__invoke__(
                    extract_node.node, extract_node.arcs)
            extract_identity = BuildExtractIdentity.__invoke__(
                    extract_node.node, extract_node.arcs)
            resolve_key = BuildResolveKey.__invoke__(
                    extract_node.node.table)
            extract_table_for_update = BuildExtractTable.__invoke__(
                    extract_identity.node, extract_identity.arcs)
            execute_insert = BuildExecuteInsert.__invoke__(
                    extract_table.table, extract_table.columns)
            execute_update = BuildExecuteUpdate.__invoke__(
                    extract_table_for_update.table,
                    extract_table_for_update.columns)
            resolve_identity = BuildResolveIdentity.__invoke__(
                    execute_insert.table, execute_insert.output_columns)
            meta = resolve_identity.profile
            data = []
            for record in product.data:
                if record is None:
                    continue
                row = extract_node(record)
                update_id, update_row = extract_identity(row)
                key = resolve_key(update_id)
                if key is not None:
                    row = extract_table_for_update(update_row)
                    key = execute_update(key, row)
                else:
                    row = extract_table(row)
                    key = execute_insert(row)
                row = resolve_identity(key)
                data.append(row)
            return Product(meta, data)

