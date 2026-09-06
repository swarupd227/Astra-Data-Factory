"""Pattern: delimited files with quoting, escapes and optional header rows.

Comma, pipe or any single-character delimiter; fields optionally enclosed in
a quote character, with a quote inside a field written twice or preceded by
an escape character; a configurable number of header rows to skip, whose
first row can be checked against the labels the spec declares.

A column count that differs from the spec is a file-level problem: the file
is rejected, though the rows that could be read are returned for diagnosis.
Values are written as people write them, so numbers are explicit
("-123.45") unless the field carries a picture with implied decimals.
"""

from __future__ import annotations

import csv
from typing import Iterable

from astra_knowledge.patterns.fixed_width import convert_fields, finish, place
from astra_knowledge.patterns.result import ParsedFile, RowProblem
from astra_knowledge.registry import MatchRule, Record, SourceSpec

MISMATCHES_SHOWN = 5


def matches(rule: MatchRule | None, row: list[str]) -> bool:
    if rule is None:
        return True
    if rule.kind != "column" or rule.column > len(row):
        return False
    return row[rule.column - 1].strip() == rule.value


def record_type_of(spec: SourceSpec, row: list[str]) -> Record | None:
    return next((r for r in spec.records if matches(r.match, row)), None)


def _reader(spec: SourceSpec, lines: Iterable[str]):
    file = spec.file
    escape = file.get("escape")
    return csv.reader(
        (line.rstrip("\r\n") for line in lines),
        delimiter=file["delimiter"],
        quotechar=file.get("quote") or '"',
        escapechar=escape,
        doublequote=escape is None,
        strict=True,
    )


def parse_delimited(spec: SourceSpec, lines: Iterable[str]) -> ParsedFile:
    if spec.format != "delimited":
        raise ValueError(f"{spec.label} is a {spec.format} layout; this pattern parses delimited files")

    result = ParsedFile(spec=spec)
    file = spec.file
    header_rows = int(file.get("header_rows", 0))
    declared = file.get("column_count")
    counts: dict[str, int] = {r.label: 0 for r in spec.records}
    labelled = [(r, f) for r, f in spec.fields() if f.label]
    mismatches: list[tuple[int, int, int]] = []  # line, columns found, columns expected
    headers_seen = 0
    reader = _reader(spec, lines)

    try:
        for row in reader:
            line_number = reader.line_num
            if not row or all(not cell.strip() for cell in row):
                continue
            result.lines += 1

            if headers_seen < header_rows:
                headers_seen += 1
                if headers_seen == 1 and labelled:
                    wrong = [
                        f"column {f.column} is '{row[f.column - 1].strip() if f.column <= len(row) else ''}', expected '{f.label}'"
                        for _, f in labelled
                        if f.column > len(row) or row[f.column - 1].strip() != f.label
                    ]
                    if wrong:
                        result.problems.append(RowProblem(line_number, "header row does not match the spec: " + "; ".join(wrong), level="file"))
                continue

            if declared is not None and len(row) != declared:
                mismatches.append((line_number, len(row), declared))
                continue

            record = record_type_of(spec, row)
            if record is None:
                result.problems.append(RowProblem(line_number, "no record type matches this line", level="record"))
                continue

            # Without a declared count, each record type needs the columns its own fields use.
            if declared is None:
                needed = max((f.column for f in record.fields if f.column), default=0)
                if len(row) < needed:
                    mismatches.append((line_number, len(row), needed))
                    continue
            counts[record.label] += 1

            values = convert_fields(record, lambda f, row=row: row[f.column - 1] if f.column and f.column <= len(row) else "", result, line_number, explicit_numbers=True)
            place(record, values, result, line_number)
    except csv.Error as exc:
        result.problems.append(RowProblem(reader.line_num, f"malformed quoting: {exc}; the rest of the file was not read", level="file"))

    if mismatches:
        more = f", and {len(mismatches) - MISMATCHES_SHOWN} more" if len(mismatches) > MISMATCHES_SHOWN else ""
        if declared is not None:
            shown = ", ".join(f"line {line} has {count}" for line, count, _ in mismatches[:MISMATCHES_SHOWN])
            message = f"{len(mismatches)} line(s) do not have the expected {declared} columns: {shown}{more}"
        else:
            shown = ", ".join(f"line {line} has {count} (needs {need})" for line, count, need in mismatches[:MISMATCHES_SHOWN])
            message = f"{len(mismatches)} line(s) have fewer columns than their record type needs: {shown}{more}"
        result.problems.append(RowProblem(mismatches[0][0], message, level="file"))

    return finish(spec, result, counts)
