"""Transforms a mapping may apply to a source field before it lands in a canonical column.

A transform is written in the config as `name(arg, ...)`. Each one here
says what it needs (argument names and types, the source types it
accepts) and what it yields, so the compiler can reject a transform the
renderers do not know, a call with the wrong arguments, or one whose
result does not fit the target column. The renderers (S3.2.x) implement
exactly this set in SQL; the pattern library holds the reference
semantics of the numeric ones (patterns/numerics.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CALL = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*\((.*)\)\s*$", re.DOTALL)

# Logical types of source fields (Source Spec) and of transform results.
ANY = ("string", "code", "integer", "decimal", "date", "time", "boolean")


@dataclass(frozen=True)
class Transform:
    name: str
    parameters: tuple[tuple[str, str], ...]  # (name, "int" | "str")
    accepts: tuple[str, ...]
    yields: str | None  # None: the same type as the input
    description: str

    @property
    def signature(self) -> str:
        return f"{self.name}({', '.join(f'{n}: {t}' for n, t in self.parameters)})"


TRANSFORMS: dict[str, Transform] = {
    t.name: t
    for t in (
        Transform("signed_implied_decimal", (("digits", "int"), ("scale", "int")), ("decimal", "integer", "string"), "decimal", "Unsigned digits with implied decimals and a separate or leading sign, as the spec's sign convention says (S2.2.3)."),
        Transform("implied_decimal", (("scale", "int"),), ("decimal", "integer", "string"), "decimal", "Unsigned digits with implied decimals and no sign."),
        Transform("negate", (), ("decimal", "integer"), None, "The value with its sign reversed."),
        Transform("trim", (), ("string", "code"), "string", "Leading and trailing spaces removed."),
        Transform("upper", (), ("string", "code"), "string", "Upper case."),
        Transform("to_date", (("format", "str"),), ("string", "integer"), "date", "A date parsed with the given format (YYYYMMDD, MM/DD/YYYY, ...)."),
        Transform("nullif", (("value", "str"),), ANY, None, "NULL when the value equals the given text."),
    )
}


@dataclass(frozen=True)
class TransformCall:
    transform: Transform
    arguments: tuple[int | str, ...]

    @property
    def text(self) -> str:
        return f"{self.transform.name}({', '.join(repr(a) if isinstance(a, str) else str(a) for a in self.arguments)})"

    def result_type(self, source_type: str) -> str:
        return self.transform.yields or source_type

    def to_dict(self) -> dict:
        return {"name": self.transform.name, "arguments": list(self.arguments)}


class TransformError(ValueError):
    """The transform expression cannot be used; the message says why."""


def parse_transform(text: str) -> TransformCall:
    """Parse `name(arg, ...)`. Raises TransformError naming what is wrong."""
    match = CALL.match(text)
    if not match:
        raise TransformError(f"'{text}' is not a transform call; write name(arguments), for example signed_implied_decimal(13, 5)")
    name, raw_args = match.group(1), match.group(2).strip()
    transform = TRANSFORMS.get(name)
    if transform is None:
        raise TransformError(f"'{name}' is not a transform the renderers know; transforms are {', '.join(sorted(TRANSFORMS))}")
    parts = [a.strip() for a in raw_args.split(",")] if raw_args else []
    if len(parts) != len(transform.parameters):
        raise TransformError(f"{name} takes {len(transform.parameters)} argument{'s' if len(transform.parameters) != 1 else ''} ({transform.signature}), not {len(parts)}")
    arguments: list[int | str] = []
    for (param, kind), part in zip(transform.parameters, parts):
        if kind == "int":
            if not re.fullmatch(r"-?\d+", part):
                raise TransformError(f"{name}: {param} must be a whole number, not '{part}'")
            arguments.append(int(part))
        else:
            arguments.append(part.strip("'\""))
    return TransformCall(transform, tuple(arguments))
