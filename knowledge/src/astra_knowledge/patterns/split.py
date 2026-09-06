"""Pattern: split one custodian record into several canonical records.

A dividend reinvestment (DRIP) record is one line at the custodian and two
transactions in the canonical model: the dividend received and the shares
bought with it. The spec's `split` rules say when a row splits (a field
equal to one of some codes) and what each part gets: values set, values
cleared, numbers negated. Every part keeps the line it came from, the rule
and its position, so a canonical record is always traceable to its source.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from astra_knowledge.patterns.result import ParsedFile, ParsedRow, RowProblem
from astra_knowledge.registry import SourceSpec, SplitRule


def _rule_for(spec: SourceSpec, row: ParsedRow) -> SplitRule | None:
    return next((r for r in spec.splits if r.record == row.record and r.applies(row.values.get(r.field))), None)


def split(parsed: ParsedFile) -> ParsedFile:
    """Apply the spec's split rules. Rows no rule matches pass through unchanged."""
    spec = parsed.spec
    if not spec.splits:
        return parsed

    result = ParsedFile(spec=spec, metadata=dict(parsed.metadata), problems=list(parsed.problems), counts=dict(parsed.counts), lines=parsed.lines)
    for row in parsed.rows:
        rule = _rule_for(spec, row)
        if rule is None:
            result.rows.append(row)
            continue
        for index, part in enumerate(rule.parts):
            values: dict[str, Any] = dict(row.values)
            values.update(part.set)
            for name in part.negate:
                value = values.get(name)
                if value is None:
                    continue
                if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
                    values[name] = -value
                else:
                    result.problems.append(RowProblem(row.line_number, f"split '{rule.name}' part '{part.name}' negates '{name}', which is not a number", row.record, name, level="record", code="SPLIT_NOT_NUMERIC"))
            result.rows.append(ParsedRow(part.name, row.line_number, values, origin=row.record, split=rule.name, part=index))
            result.counts[part.name] = result.counts.get(part.name, 0) + 1
    return result
