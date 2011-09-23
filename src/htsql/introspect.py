#
# Copyright (c) 2006-2011, Prometheus Research, LLC
# See `LICENSE` for license information, `AUTHORS` for the list of authors.
#


"""
:mod:`htsql.introspect`
=======================

This module declares the database introspector adapter.
"""


from .context import context
from .adapter import Utility
import threading


class CatalogCache(object):

    def __init__(self):
        self.lock = threading.Lock()
        self.catalog = None

    def update(self, catalog):
        self.catalog = catalog


class Introspect(Utility):
    """
    Declares the introspection interface.

    An introspector analyzes the database meta-data and generates
    an HTSQL catalog.

    Usage::

        introspect = Introspect()
        catalog = introspect()

    Note that most implementations loads the meta-data information
    when the adapter is constructed so subsequent calls of
    :meth:`__call__` will always produce essentially the same catalog.
    To re-load the meta-data from the database, create a new instance
    of the :class:`Introspect` adapter.
    """

    def __call__(self):
        """
        Returns an HTSQL catalog.
        """
        # Override in implementations.
        raise NotImplementedError()


def introspect():
    catalog_cache = context.app.htsql.catalog_cache
    if catalog_cache.catalog is None:
        with catalog_cache.lock:
            if catalog_cache.catalog is None:
                introspect = Introspect()
                catalog = introspect()
                catalog_cache.update(catalog)
    return catalog_cache.catalog


