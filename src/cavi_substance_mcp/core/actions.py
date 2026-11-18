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
    """JSON Schema conditions on ``action`` that require and forbid what ``checked`` does.

    A required argument's own schema must refuse null, as ``checked`` does.
    """
    names = set().union(*(required | optional for required, optional in table.values()))

    def rules(actions, settled):
        # What every action in the group shares is stated once, then the first action is
        # split from the rest.
        required = set.intersection(*(table[action][0] for action in actions)) - settled
        refused = set.intersection(*(names - table[action][0] - table[action][1]
                                     for action in actions)) - settled
        rule = {"required": sorted(required)} if required else {}
        if refused:
            rule["properties"] = dict.fromkeys(sorted(refused), False)
        if len(actions) > 1:
            settled = settled | required | refused
            rule["if"] = {"properties": {"action": {"const": actions[0]}}}
            for key, group in (("then", actions[:1]), ("else", actions[1:])):
                if branch := rules(group, settled):
                    rule[key] = branch
        return rule

    rule = rules(list(table), set())
    return rule if rule.keys() <= {"if", "then", "else"} else {"allOf": [rule]}
