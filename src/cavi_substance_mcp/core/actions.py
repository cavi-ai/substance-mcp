"""Per-action argument tables for flat MCP tools."""

from cavi_mcp_core.server import need, reject_foreign


def checked(action, arguments, table, defaults=None):
    """The arguments ``action`` accepts, after refusing missing required and foreign ones.

    ``table`` maps each action to (required, optional) argument names. An argument equal to
    its entry in ``defaults`` counts as not passed.
    """
    defaults = defaults or {}
    required, optional = table[action]
    reject_foreign(action, {key: value for key, value in arguments.items()
                            if key not in defaults or value != defaults[key]},
                   required | optional)
    for key in sorted(required):
        need(arguments[key], key, action)
    return {key: arguments[key] for key in required | optional}
