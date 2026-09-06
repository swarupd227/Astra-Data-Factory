"""Pattern: fixed-width files with header, trailer and one or more detail record types.

Given a Source Spec, every line of a file becomes either file metadata
(header and trailer records) or one typed detail row, with problems
reported per line and field instead of stopping. The record type of a line
is read from the position the spec configures, either a fixed number of
characters or the characters up to an end marker.

This is the reference implementation of the pattern. The SQL the generation
plane renders for the same spec (S3.2.2) must agree with it row for row.
"""

from __future__ import annotations

from typing import Any, Iterable

from astra_knowledge.patterns.result import ParsedFile, ParsedRow, RowProblem
from astra_knowledge.patterns.values import convert
from astra_knowledge.registry import Field, MatchRule, Record, SourceSpec


def matches(rule: MatchRule | None, line: str) -> bool:
    """Whether a line is the record type described by the rule. No rule matches every line."""
    if rule is None:
        return True
    if rule.kind == "position":
        return line[rule.start - 1 : rule.start - 1 + rule.length] == rule.value
    if rule.kind == "end_marker":
        token = line[rule.start - 1 :]
        cut = token.find(rule.end_marker)
        return (token if cut < 0 else token[:cut]) == rule.value
    return False


def record_type_of(spec: SourceSpec, line: str) -> Record | None:
    """The first record type whose match rule fits the line, in spec order."""
    return next((r for r in spec.records if matches(r.match, line)), None)


def _raw(line: str, f: Field) -> str:
    return line[f.start - 1 : f.start - 1 + f.length]


def convert_fields(record: Record, raw_of, result: ParsedFile, line_number: int, *, explicit_numbers: bool) -> dict[str, Any]:
    """Convert every field of a record, reporting problems. `raw_of(field)` returns the field's characters."""
    values: dict[str, Any] = {}
    for f in record.fields:
        raw = raw_of(f)
        sign = None
        if f.sign_field:
            sign_field = record.field(f.sign_field)
            sign = raw_of(sign_field) if sign_field else None
        converted = convert(
            raw,
            f.type,
            scale=f.picture.scale if f.picture else 0,
            format=f.format,
            codes=f.codes,
            sign=sign,
            has_sign_field=f.sign_field is not None,
            explicit=explicit_numbers and not (f.picture and f.picture.scale),
            convention=f.sign_convention,
            sign_style=f.sign_style,
        )
        values[f.name] = converted.value
        if converted.problem:
            result.problems.append(RowProblem(line_number, converted.problem, record.label, f.name, code=converted.code))
        elif f.required and converted.value is None:
            result.problems.append(RowProblem(line_number, "required field is blank", record.label, f.name, code="FIELD_REQUIRED_BLANK"))
    return values


def place(record: Record, values: dict[str, Any], result: ParsedFile, line_number: int) -> None:
    """Put a converted record where it belongs: a detail row or file metadata."""
    if record.type == "detail":
        result.rows.append(ParsedRow(record.label, line_number, values))
    elif record.label in result.metadata:
        result.problems.append(RowProblem(line_number, f"a second {record.label} record; a file has at most one", record.label, level="record", code="RECORD_DUPLICATE_HEADER"))
    else:
        result.metadata[record.label] = values


def finish(spec: SourceSpec, result: ParsedFile, counts: dict[str, int]) -> ParsedFile:
    result.counts = counts
    for kind in ("header", "trailer"):
        if any(r.type == kind for r in spec.records) and kind not in result.metadata and result.lines:
            result.problems.append(RowProblem(0, f"the file has no {kind} record", level="file", code=f"FILE_{kind.upper()}_MISSING"))
    return result


def parse_fixed_width(spec: SourceSpec, lines: Iterable[str]) -> ParsedFile:
    if spec.format != "fixed_width":
        raise ValueError(f"{spec.label} is a {spec.format} layout; this pattern parses fixed_width files")

    result = ParsedFile(spec=spec)
    record_length = spec.record_length or max((f.end for _, f in spec.fields()), default=0)
    counts: dict[str, int] = {r.label: 0 for r in spec.records}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        result.lines += 1

        if len(line) > record_length:
            result.problems.append(RowProblem(line_number, f"line is {len(line)} characters, longer than the record length {record_length}", level="record", code="RECORD_TOO_LONG"))
        line = line.ljust(record_length)

        record = record_type_of(spec, line)
        if record is None:
            result.problems.append(RowProblem(line_number, "no record type matches this line", level="record", code="RECORD_TYPE_UNKNOWN"))
            continue
        counts[record.label] += 1

        values = convert_fields(record, lambda f, line=line: _raw(line, f), result, line_number, explicit_numbers=False)
        place(record, values, result, line_number)

    return finish(spec, result, counts)
