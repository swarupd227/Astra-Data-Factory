"""One thing wrong with one file, reported where it can be acted on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Problem:
    path: str
    line: int | None
    message: str

    def format(self, style: str = "text", title: str = "Validation") -> str:
        if style == "github":
            location = f"file={self.path}" + (f",line={self.line}" if self.line else "")
            return f"::error {location},title={title}::{self.message}"
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"


def dedupe(problems: list[Problem]) -> list[Problem]:
    seen: set[tuple[str, int | None, str]] = set()
    unique: list[Problem] = []
    for p in problems:
        key = (p.path, p.line, p.message)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def display_path(path: Path, root: Path | None) -> str:
    """The path as people see it: relative to the repository root, forward slashes."""
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
