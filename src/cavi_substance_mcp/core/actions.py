"""Per-action argument tables for flat MCP tools."""

from cavi_mcp_core.server import need, reject_foreign


def _supplied(key, value, defaults):
    """True when ``value`` was passed: not null, and not an omitted default."""
    if value is None:
        return False
    return key not in defaults or value != defaults[key]


def checked(action, arguments, table, defaults=None, one_of=None):
    """The arguments ``action`` accepts, after refusing missing required and foreign ones.

    ``table`` maps each action to (required, optional) argument names. An argument equal to
    its entry in ``defaults`` counts as not passed. ``one_of`` maps an action to names of
    which exactly one must be supplied.
    """
    defaults = defaults or {}
    one_of = one_of or {}
    required, optional = table[action]
    reject_foreign(action, {key: value for key, value in arguments.items()
                            if key not in defaults or value != defaults[key]},
                   required | optional)
    for key in sorted(required):
        need(arguments[key], key, action)
    if names := one_of.get(action):
        supplied = [key for key in names
                    if key in arguments and _supplied(key, arguments[key], defaults)]
        if len(supplied) != 1:
            if action == "cook" and set(names) == {"path", "builtin"}:
                raise ValueError("cook needs exactly one of path or builtin")
            listed = " or ".join(sorted(names))
            raise ValueError(f"{action} needs exactly one of {listed}")
    return {key: arguments[key] for key in required | optional}


def _exclusive(names):
    """oneOf branches that require one name and refuse the others."""
    branches = []
    for name in sorted(names):
        branch = {"required": [name]}
        others = sorted(set(names) - {name})
        if others:
            branch["properties"] = dict.fromkeys(others, False)
        branches.append(branch)
    return branches


def action_schema(table, one_of=None) -> dict:
    """JSON Schema conditions on ``action`` that require and forbid what ``checked`` does.

    A required argument's own schema must refuse null, as ``checked`` does. An action in
    ``one_of`` advertises that XOR as oneOf branches which require one name and set the
    other properties to false.
    """
    one_of = one_of or {}
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
        if len(actions) == 1 and (exclusive := one_of.get(actions[0])):
            rule["oneOf"] = _exclusive(exclusive)
        if len(actions) > 1:
            settled = settled | required | refused
            rule["if"] = {"properties": {"action": {"const": actions[0]}}}
            for key, group in (("then", actions[:1]), ("else", actions[1:])):
                if branch := rules(group, settled):
                    rule[key] = branch
        return rule

    rule = rules(list(table), set())
    return rule if rule.keys() <= {"if", "then", "else"} else {"allOf": [rule]}
