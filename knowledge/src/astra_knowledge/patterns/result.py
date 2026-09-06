"""What every pattern returns: file metadata, typed rows and problems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astra_knowledge.registry import SourceSpec


@dataclass(frozen=True)
class RowProblem:
    """Something wrong at a line. `level` says how far it reaches:

    file    the whole file is unusable (a column count mismatch, a missing trailer)
    record  the line could not be placed (no record type matches, a second header)
    field   one value on the line (a bad date, a blank required field)
    """

    line_number: int
    message: str
    record: str | None = None
    field: str | None = None
    level: str = "field"

    def text(self) -> str:
        where = f"line {self.line_number}" if self.line_number else "file"
        if self.record:
            where += f" ({self.record}"
            where += f".{self.field})" if self.field else ")"
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class ParsedRow:
    record: str
    line_number: int
    values: dict[str, Any]


@dataclass
class ParsedFile:
    spec: SourceSpec
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    rows: list[ParsedRow] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    lines: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def file_problems(self) -> list[RowProblem]:
        """Problems that reject the whole file."""
        return [p for p in self.problems if p.level == "file"]

    @property
    def rejected(self) -> bool:
        return bool(self.file_problems)
