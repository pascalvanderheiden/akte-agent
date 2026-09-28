"""Evaluate the subset of ARM template expressions that this repo's Bicep compiles to.

Compiled templates express decisions (`if`, `startsWith`, ...) as expression
strings, so asserting on the text only proves the shape of the expression, not
which branch it takes. This evaluator resolves those expressions to concrete
values, which lets tests pin down both branches of a decision.

Only the functions the templates actually use are implemented; anything else
raises rather than silently returning a wrong value.
"""

import json
import re

_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')"
    r"|(?P<number>-?\d+)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<punct>[(),.\[\]]))"
)

# Resource ids are opaque to this evaluator; a marker carries enough for a test
# to decide what `reference()` on it should return.
class ResourceId:
    def __init__(self, resource_type, segments):
        self.resource_type = resource_type
        self.segments = tuple(segments)

    def __repr__(self):
        return f"ResourceId({self.resource_type!r}, {self.segments!r})"


class UnsupportedExpression(Exception):
    """Raised for template syntax this evaluator deliberately does not guess at."""


def _tokenize(expression):
    position = 0
    while position < len(expression):
        match = _TOKEN.match(expression, position)
        if not match:
            if expression[position:].strip() == "":
                break
            raise UnsupportedExpression(f"Cannot tokenize {expression[position:]!r}")
        position = match.end()
        kind = match.lastgroup
        value = match.group()
        if kind == "string":
            yield "literal", value.strip()[1:-1].replace("''", "'")
        elif kind == "number":
            yield "literal", int(value)
        elif kind == "name":
            yield "name", value.strip()
        else:
            yield "punct", value.strip()


def _parse(expression):
    tokens = list(_tokenize(expression))
    node, index = _parse_postfix(tokens, 0)
    if index != len(tokens):
        raise UnsupportedExpression(f"Trailing tokens in {expression!r}")
    return node


def _expect(tokens, index, punct):
    if index >= len(tokens) or tokens[index] != ("punct", punct):
        raise UnsupportedExpression(f"Expected {punct!r} at token {index}")
    return index + 1


def _parse_postfix(tokens, index):
    node, index = _parse_primary(tokens, index)
    while index < len(tokens) and tokens[index] in (("punct", "."), ("punct", "[")):
        if tokens[index] == ("punct", "."):
            kind, value = tokens[index + 1]
            if kind != "name":
                raise UnsupportedExpression("Expected a property name after '.'")
            node = ("property", node, value)
            index += 2
        else:
            key, index = _parse_postfix(tokens, index + 1)
            index = _expect(tokens, index, "]")
            node = ("index", node, key)
    return node, index


def _parse_primary(tokens, index):
    if index >= len(tokens):
        raise UnsupportedExpression("Unexpected end of expression")
    kind, value = tokens[index]
    if kind == "literal":
        return ("literal", value), index + 1
    if kind != "name":
        raise UnsupportedExpression(f"Unexpected token {value!r}")
    if value in ("true", "false", "null"):
        return ("literal", {"true": True, "false": False, "null": None}[value]), index + 1
    index = _expect(tokens, index + 1, "(")
    arguments = []
    if tokens[index] != ("punct", ")"):
        while True:
            argument, index = _parse_postfix(tokens, index)
            arguments.append(argument)
            if index < len(tokens) and tokens[index] == ("punct", ","):
                index += 1
                continue
            break
    index = _expect(tokens, index, ")")
    return ("call", value, arguments), index


def _format(template, *arguments):
    return re.sub(
        r"\{(\d+)\}",
        lambda match: _to_string(arguments[int(match.group(1))]),
        template,
    )


def _to_string(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _index_of(haystack, needle):
    # ARM's string search functions are case-insensitive.
    return haystack.lower().find(needle.lower())


def _last_index_of(haystack, needle):
    return haystack.lower().rfind(needle.lower())


_FUNCTIONS = {
    "concat": lambda *values: _concat(*values),
    "createArray": lambda *values: list(values),
    "createObject": lambda *pairs: dict(zip(pairs[::2], pairs[1::2])),
    "empty": lambda value: _empty(value),
    "endsWith": lambda value, suffix: value.lower().endswith(suffix.lower()),
    "equals": lambda left, right: left == right,
    "format": _format,
    "greater": lambda left, right: left > right,
    "greaterOrEquals": lambda left, right: left >= right,
    "indexOf": _index_of,
    "int": int,
    "json": json.loads,
    "lastIndexOf": _last_index_of,
    "length": len,
    "less": lambda left, right: left < right,
    "lessOrEquals": lambda left, right: left <= right,
    "not": lambda value: not value,
    "replace": lambda value, old, new: value.replace(old, new),
    "split": lambda value, separator: value.split(separator),
    "startsWith": lambda value, prefix: value.lower().startswith(prefix.lower()),
    "string": _to_string,
    "substring": lambda value, start, length=None: (
        value[start:] if length is None else value[start : start + length]
    ),
    "toLower": lambda value: value.lower(),
    "union": lambda *values: {key: value for item in values for key, value in item.items()},
}


def _chain(values):
    for value in values:
        yield from value


def _concat(*values):
    lists = [isinstance(value, list) for value in values]
    if any(lists):
        if not all(lists):
            raise UnsupportedExpression("concat() mixes arrays with other values")
        return list(_chain(values))
    return "".join(_to_string(value) for value in values)


def _empty(value):
    if value is None:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False
    return len(value) == 0


class Evaluator:
    """Resolves a compiled template's variables, outputs and resource properties."""

    def __init__(self, template, parameters=None, reference_resolver=None):
        self.template = template
        self.reference_resolver = reference_resolver
        self._variables = {}
        self.parameters = {}
        for name, declaration in (template.get("parameters") or {}).items():
            if parameters and name in parameters:
                self.parameters[name] = parameters[name]
            elif "defaultValue" in declaration:
                self.parameters[name] = self.evaluate(declaration["defaultValue"])
            else:
                self.parameters[name] = _placeholder(declaration.get("type", "string"), name)

    def evaluate(self, value):
        if isinstance(value, str):
            # ARM escapes a leading `[[` only in a bracketed string; anything
            # else is an ordinary literal and is returned untouched below.
            if value.startswith("[[") and value.endswith("]"):
                return value[1:]
            if value.startswith("[") and value.endswith("]"):
                return self._evaluate_node(_parse(value[1:-1]))
            return value
        if isinstance(value, list):
            return [self.evaluate(item) for item in value]
        if isinstance(value, dict):
            return {key: self.evaluate(item) for key, item in value.items()}
        return value

    def output(self, name):
        return self.evaluate(self.template["outputs"][name]["value"])

    def _variable(self, name):
        if name not in self._variables:
            self._variables[name] = self.evaluate(self.template["variables"][name])
        return self._variables[name]

    def _evaluate_node(self, node):
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "property":
            target = self._evaluate_node(node[1])
            return target[node[2]]
        if kind == "index":
            return self._evaluate_node(node[1])[self._evaluate_node(node[2])]

        _, name, arguments = node
        if name == "if":
            return self._evaluate_node(arguments[1] if self._evaluate_node(arguments[0]) else arguments[2])
        if name == "and":
            return all(self._evaluate_node(argument) for argument in arguments)
        if name == "or":
            return any(self._evaluate_node(argument) for argument in arguments)
        if name == "coalesce":
            for argument in arguments:
                value = self._evaluate_node(argument)
                if value is not None:
                    return value
            return None

        values = [self._evaluate_node(argument) for argument in arguments]
        if name == "parameters":
            return self.parameters[values[0]]
        if name == "variables":
            return self._variable(values[0])
        if name in ("resourceId", "subscriptionResourceId", "extensionResourceId"):
            return ResourceId(values[0], values[1:])
        if name == "reference":
            if self.reference_resolver is None:
                raise UnsupportedExpression("This template needs a reference resolver")
            return self.reference_resolver(values[0])
        if name in _FUNCTIONS:
            return _FUNCTIONS[name](*values)
        raise UnsupportedExpression(f"Unsupported template function {name!r}")


def _placeholder(parameter_type, name):
    if parameter_type == "bool":
        return False
    if parameter_type == "int":
        return 0
    if parameter_type == "array":
        return []
    if parameter_type == "object":
        return {}
    return f"<{name}>"
