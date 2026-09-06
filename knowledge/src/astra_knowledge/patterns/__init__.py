"""The pattern library (product spec Section 4).

A pattern is a reusable way to handle a class of source. Each one has an
id, a statement of what it applies to, a reference implementation that
turns a file into typed rows, and tests. The generation plane renders the
same semantics into native code for the target platform; the reference
implementation is the oracle that rendered code is checked against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from astra_knowledge.patterns.delimited import parse_delimited
from astra_knowledge.patterns.fixed_width import parse_fixed_width
from astra_knowledge.patterns.result import ParsedFile, ParsedRow, RowProblem
from astra_knowledge.registry import SourceSpec


@dataclass(frozen=True)
class Pattern:
    id: str
    name: str
    description: str
    applies_to: Callable[[SourceSpec], bool]
    parse: Callable[[SourceSpec, Iterable[str]], ParsedFile]


FIXED_WIDTH_MULTI_RECORD = Pattern(
    id="fixed_width_multi_record",
    name="Fixed-width multi-record file",
    description=(
        "Fixed-width files with a header, a trailer and one or more detail record types. "
        "The record type is read from a configured position, fixed-length or up to an end marker. "
        "Header and trailer become file metadata; every detail record becomes one typed row."
    ),
    applies_to=lambda spec: spec.format == "fixed_width",
    parse=parse_fixed_width,
)

DELIMITED_FILE = Pattern(
    id="delimited_file",
    name="Delimited file",
    description=(
        "Comma, pipe or other single-character delimited files with quoted fields, doubled or escaped quotes, "
        "and optional header rows checked against the spec's labels. A column count other than the spec's "
        "rejects the file."
    ),
    applies_to=lambda spec: spec.format == "delimited",
    parse=parse_delimited,
)

PATTERNS: dict[str, Pattern] = {p.id: p for p in (FIXED_WIDTH_MULTI_RECORD, DELIMITED_FILE)}


def patterns_for(spec: SourceSpec) -> list[Pattern]:
    """Every pattern that can handle the spec, in library order."""
    return [p for p in PATTERNS.values() if p.applies_to(spec)]


def parse(spec: SourceSpec, lines: Iterable[str]) -> ParsedFile:
    """Parse a file with the first pattern that applies to its spec."""
    applicable = patterns_for(spec)
    if not applicable:
        raise ValueError(f"no pattern in the library handles {spec.label} ({spec.format})")
    return applicable[0].parse(spec, lines)


__all__ = ["DELIMITED_FILE", "FIXED_WIDTH_MULTI_RECORD", "PATTERNS", "ParsedFile", "ParsedRow", "Pattern", "RowProblem", "parse", "patterns_for"]
