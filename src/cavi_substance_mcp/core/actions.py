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


def action_schema(table) -> dict:
    """JSON Schema conditions on ``action`` that require and forbid what ``checked`` does."""
    names = set().union(*(required | optional for required, optional in table.values()))
    rules = None
    for action, (required, optional) in reversed(table.items()):
        then = {}
        if required:
            then["required"] = sorted(required)
        if names - required - optional:
            then["properties"] = dict.fromkeys(sorted(names - required - optional), False)
        # The last action is the only one left once every earlier test has failed.
        rules = then if rules is None else {
            "if": {"properties": {"action": {"const": action}}}, "then": then, "else": rules}
    return rules
