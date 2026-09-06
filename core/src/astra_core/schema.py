"""JSON Schema validation with messages people can act on.

Schema errors are rewritten into plain sentences that name the field, what
was found and what is allowed, and are pointed at the line the value sits
on. Every plane's validator uses this so a config, a spec and a bundle
manifest all fail the same way.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from astra_core.yamlsource import line_of

IDENTIFIER_PATTERN = "^[a-z][a-z0-9_]{0,62}$"
DATE_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

_validators: dict[tuple[str, str], Draft202012Validator] = {}


def load_validator(package: str, name: str) -> Draft202012Validator:
    """A cached validator for a schema file shipped inside a package."""
    key = (package, name)
    if key not in _validators:
        schema = json.loads(resources.files(package).joinpath(name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        _validators[key] = Draft202012Validator(schema)
    return _validators[key]


def sorted_errors(validator: Draft202012Validator, data: Any) -> list[ValidationError]:
    return sorted(validator.iter_errors(data), key=lambda e: (list(map(str, e.absolute_path)), e.message))


def path_text(path: Iterable[Any]) -> str:
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else (f".{step}" if text else str(step))
    return text or "top level"


def error_line(data: Any, error: ValidationError) -> int | None:
    path = list(error.absolute_path)
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extras = _extra_keys(error)
        if extras:
            return line_of(data, path + [extras[0]])
    return line_of(data, path)


def _extra_keys(error: ValidationError) -> list[str]:
    allowed = set(error.schema.get("properties", {}))
    return sorted(k for k in error.instance if k not in allowed)


def type_name(value: Any) -> str:
    return {dict: "mapping", list: "list", str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}.get(type(value), type(value).__name__)


def describe_error(error: ValidationError) -> str:
    where = path_text(error.absolute_path)
    kind = error.validator
    if kind == "required":
        missing = [name for name in error.validator_value if name not in error.instance]
        return f"{where}: missing required field{'s' if len(missing) > 1 else ''} {', '.join(missing)}"
    if kind == "additionalProperties":
        extras = _extra_keys(error)
        allowed = ", ".join(sorted(error.schema.get("properties", {})))
        return f"{where}: unknown field{'s' if len(extras) > 1 else ''} {', '.join(extras)}; allowed fields are {allowed}"
    if kind == "enum":
        return f"{where}: {error.instance!r} is not one of {', '.join(map(str, error.validator_value))}"
    if kind == "const":
        return f"{where}: must be {error.validator_value!r}"
    if kind == "pattern":
        if error.validator_value == IDENTIFIER_PATTERN:
            return f"{where}: {error.instance!r} must be lower-case letters, digits and underscores, starting with a letter"
        if error.validator_value == DATE_PATTERN:
            return f"{where}: {error.instance!r} must be a date written as YYYY-MM-DD"
        return f"{where}: {error.instance!r} does not match the required pattern {error.validator_value}"
    if kind == "type":
        expected = error.validator_value if isinstance(error.validator_value, str) else " or ".join(error.validator_value)
        expected = {"object": "mapping", "array": "list"}.get(expected, expected)
        return f"{where}: expected {expected}, found {type_name(error.instance)}"
    if kind == "minLength":
        return f"{where}: must not be empty"
    if kind == "minItems":
        return f"{where}: must have at least {error.validator_value} item{'s' if error.validator_value != 1 else ''}"
    if kind == "minimum":
        return f"{where}: must be at least {error.validator_value}"
    if kind == "maximum":
        return f"{where}: must be at most {error.validator_value}"
    if kind == "uniqueItems":
        return f"{where}: items must be unique"
    if kind == "anyOf":
        return f"{where}: {_any_of_hint(error)}"
    return f"{where}: {error.message}"


def _any_of_hint(error: ValidationError) -> str:
    required = [alt.get("required", []) for alt in error.validator_value if isinstance(alt, dict) and alt.get("required")]
    if required:
        return "must have " + " or ".join(", ".join(r) for r in required)
    return error.message
