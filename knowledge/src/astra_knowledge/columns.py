"""Typed columns shared by the canonical model and the reference-data feeds.

A column has a logical type (string, integer, decimal, date, timestamp,
boolean), a description, whether it is required, an optional PII category
from the foundation's tag (ADR 0008), an optional code list and an optional
lookup into a control table. `sql_type` is its Snowflake Iceberg type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SQL_TYPES = {
    "string": "STRING",
    "integer": "NUMBER(18,0)",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ(6)",
    "boolean": "BOOLEAN",
}


@dataclass(frozen=True)
class Code:
    value: str
    meaning: str


@dataclass(frozen=True)
class Lookup:
    """A control table the column's values must exist in."""

    schema: str
    table: str
    column: str

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    required: bool = False
    precision: int | None = None
    scale: int | None = None
    pii: str | None = None
    codes: tuple[Code, ...] = ()
    lookup: Lookup | None = None

    @property
    def sql_type(self) -> str:
        if self.type == "decimal":
            return f"NUMBER({self.precision},{self.scale})"
        return SQL_TYPES[self.type]

    def widens(self, other: "Column") -> bool:
        """True when a value of `other`'s type always fits this column's type."""
        if self.type == other.type:
            if self.type != "decimal":
                return True
            return self.precision >= other.precision and self.scale >= other.scale and (self.precision - self.scale) >= (other.precision - other.scale)
        if other.type == "integer" and self.type == "decimal":
            return (self.precision - self.scale) >= 18
        return False


def column_from(data: dict[str, Any]) -> Column:
    """A Column from its validated mapping in a model or feed file."""
    return Column(
        name=data["name"],
        type=data["type"],
        description=" ".join(str(data["description"]).split()),
        required=bool(data.get("required", False)),
        precision=data.get("precision"),
        scale=data.get("scale"),
        pii=data.get("pii"),
        codes=tuple(Code(str(c["value"]), c["meaning"]) for c in data.get("codes") or ()),
        lookup=Lookup(data["lookup"]["schema"], data["lookup"]["table"], data["lookup"]["column"]) if data.get("lookup") else None,
    )


def column_problems(column: dict[str, Any]) -> list[str]:
    """What the schema cannot say about one column mapping."""
    problems: list[str] = []
    if column["type"] == "decimal" and column["scale"] >= column["precision"]:
        problems.append(f"column '{column['name']}': scale {column['scale']} must be less than precision {column['precision']}")
    values = [c["value"] for c in column.get("codes") or ()]
    for value in dict.fromkeys(values):
        if values.count(value) > 1:
            problems.append(f"column '{column['name']}': code '{value}' is listed twice")
    return problems
