#
# Copyright (c) 2006-2011, Prometheus Research, LLC
# See `LICENSE` for license information, `AUTHORS` for the list of authors.
#


from . import act
from htsql.addon import Addon, Parameter
from htsql.validator import BoolVal


class TweakShellDefaultAddon(Addon):

    name = 'tweak.shell.default'
    hint = """make `/shell()` the default output format"""
    help = """
    This addon makes the default output format be the command
    `/shell()`.  With this addon, any query without explicit
    format indicator will show the output in the HTSQL shell.
    Use indicator `/:html` to get HTML output.

    Parameters `on_root`, `on_default` and `on_error` allows
    you to configure when `/shell()` is invoked.
    """

    parameters = [
            Parameter('on_root', BoolVal(), default=True,
                      value_name="TRUE|FALSE",
                      hint="""invoke on an empty query"""),
            Parameter('on_default', BoolVal(), default=True,
                      value_name="TRUE|FALSE",
                      hint="""invoke on a query without format"""),
            Parameter('on_error', BoolVal(), default=True,
                      value_name="TRUE|FALSE",
                      hint="""invoke on errors"""),
    ]


