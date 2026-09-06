"""Pattern: pair detail records into one logical record on configured keys.

Some layouts split a position across two records, an A record and a B
record, that share key fields such as account and CUSIP. The spec's
`pairing` block names the records and the keys; this step joins them into
one row per logical record and reports what could not be joined:

  PAIR_INCOMPLETE   a record whose partner never appeared, with its line and keys
  PAIR_DUPLICATE    a second record of the same type for the same keys
  PAIR_KEY_BLANK    a record whose key field is blank, so it cannot be paired

Merged rows carry the keys once, then every other field of each record in
the pairing's order; a field name that appears in more than one record is
prefixed with its record label so nothing is lost.
"""

from __future__ import annotations

from typing import Any

from astra_knowledge.patterns.result import ParsedFile, ParsedRow, RowProblem
from astra_knowledge.registry import Pairing, SourceSpec


def _key_text(pairing: Pairing, key: tuple) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in zip(pairing.keys, key))


def _merged_field_names(spec: SourceSpec, pairing: Pairing) -> dict[str, dict[str, str]]:
    """For each record label, the output name of each of its fields."""
    keys = set(pairing.keys)
    seen: dict[str, int] = {}
    for label in pairing.records:
        record = spec.record(label)
        for f in record.fields:
            if f.name not in keys:
                seen[f.name] = seen.get(f.name, 0) + 1
    names: dict[str, dict[str, str]] = {}
    for label in pairing.records:
        record = spec.record(label)
        names[label] = {f.name: (f.name if f.name in keys or seen[f.name] == 1 else f"{label}_{f.name}") for f in record.fields}
    return names


def pair(parsed: ParsedFile) -> ParsedFile:
    """Apply every pairing of the spec. Returns a new ParsedFile; the input is left as it is."""
    spec = parsed.spec
    if not spec.pairings:
        return parsed

    result = ParsedFile(spec=spec, metadata=dict(parsed.metadata), problems=list(parsed.problems), counts=dict(parsed.counts), lines=parsed.lines)
    paired_labels = {label for p in spec.pairings for label in p.records}
    logical: list[ParsedRow] = [row for row in parsed.rows if row.record not in paired_labels]

    for pairing in spec.pairings:
        names = _merged_field_names(spec, pairing)
        pending: dict[tuple, dict[str, ParsedRow]] = {}
        completed = 0

        for row in parsed.rows:
            if row.record not in pairing.records:
                continue
            blank = [k for k in pairing.keys if row.values.get(k) is None]
            if blank:
                result.problems.append(RowProblem(row.line_number, f"key field{'s' if len(blank) > 1 else ''} {', '.join(blank)} blank; the record cannot be paired", row.record, blank[0], level="record", code="PAIR_KEY_BLANK"))
                continue
            key = tuple(row.values[k] for k in pairing.keys)
            slot = pending.setdefault(key, {})
            if row.record in slot:
                result.problems.append(RowProblem(row.line_number, f"a second {row.record} record for {_key_text(pairing, key)}; the first is at line {slot[row.record].line_number}", row.record, level="record", code="PAIR_DUPLICATE"))
                continue
            slot[row.record] = row
            if len(slot) == len(pairing.records):
                merged: dict[str, Any] = {k: v for k, v in zip(pairing.keys, key)}
                for label in pairing.records:
                    for name, value in slot[label].values.items():
                        if name not in pairing.keys:
                            merged[names[label][name]] = value
                logical.append(ParsedRow(pairing.name, min(r.line_number for r in slot.values()), merged))
                completed += 1
                del pending[key]

        for key, slot in pending.items():
            missing = [label for label in pairing.records if label not in slot]
            for label, row in slot.items():
                result.problems.append(
                    RowProblem(
                        row.line_number,
                        f"no {' or '.join(missing)} record for {_key_text(pairing, key)}; the {label} record at line {row.line_number} is incomplete",
                        label,
                        level="record",
                        code="PAIR_INCOMPLETE",
                    )
                )
        result.counts[pairing.name] = completed

    result.rows = sorted(logical, key=lambda r: r.line_number)
    return result
