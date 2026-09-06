"""Pattern: cancels and corrections linked to the originals they refer to.

Transaction files carry an action code: a row is new, cancels an earlier
row, or corrects one. The spec's `lifecycle` block names the action field
and its codes, the fields that identify a row and the fields of a cancel or
correction that name the original. Applied to a stream of rows over a
state of transactions:

  new       the row becomes an active record; a second row with the same
            identity is LIFECYCLE_DUPLICATE
  cancel    the original is marked cancelled and points at the cancel; the
            cancel is recorded and points at the original
  correct   the original is marked superseded and points at the correction;
            the correction is the active record and points at the original

A cancel or correction whose original is not there is
LIFECYCLE_ORIGINAL_MISSING; one aimed at a record already cancelled or
superseded is LIFECYCLE_ALREADY_CLOSED. Split parts of one custodian line
share its identity plus their part name, so a cancel of the line closes
every part.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astra_knowledge.patterns.result import ParsedFile, ParsedRow, RowProblem
from astra_knowledge.registry import LifecycleRule, SourceSpec


@dataclass
class LifecycleRecord:
    identity: tuple
    values: dict[str, Any]
    status: str  # active, cancelled, superseded, cancel
    file_name: str
    line_number: int
    cancels: tuple | None = None
    corrects: tuple | None = None
    cancelled_by: tuple | None = None
    superseded_by: tuple | None = None


@dataclass
class LifecycleState:
    records: dict[tuple, LifecycleRecord] = field(default_factory=dict)

    def active(self) -> list[LifecycleRecord]:
        return [r for r in self.records.values() if r.status == "active"]

    def matching(self, reference: tuple) -> list[LifecycleRecord]:
        """Records whose identity starts with the reference: the original and its split parts."""
        return [r for r in self.records.values() if r.identity[: len(reference)] == reference and r.status in ("active", "cancelled", "superseded")]


@dataclass
class LifecycleResult:
    new: int = 0
    cancelled: int = 0
    corrected: int = 0
    problems: list[RowProblem] = field(default_factory=list)


def _identity(rule: LifecycleRule, row: ParsedRow) -> tuple:
    base = tuple(row.values.get(name) for name in rule.identity)
    return base + (row.record,) if row.origin is not None else base


def apply_lifecycle(state: LifecycleState, spec: SourceSpec, parsed: ParsedFile, file_name: str = "") -> LifecycleResult:
    """Apply the rows of a parsed file to the lifecycle state, in file order."""
    rule = spec.lifecycle
    result = LifecycleResult()
    if rule is None:
        result.problems.append(RowProblem(0, f"{spec.label} declares no lifecycle block", level="file"))
        return result

    for row in parsed.rows:
        if row.record != rule.record and row.origin != rule.record:
            continue
        identity = _identity(rule, row)
        if any(v is None for v in identity[: len(rule.identity)]):
            result.problems.append(RowProblem(row.line_number, f"identity field{'s' if len(rule.identity) > 1 else ''} {', '.join(rule.identity)} blank; the row cannot be tracked", row.record, level="record", code="LIFECYCLE_IDENTITY_BLANK"))
            continue
        action = rule.action_for(row.values.get(rule.action_field))

        if action == "new":
            if identity in state.records:
                first = state.records[identity]
                result.problems.append(RowProblem(row.line_number, f"a second record with identity {_text(rule, identity)}; the first is at line {first.line_number} of {first.file_name or 'an earlier file'}", row.record, level="record", code="LIFECYCLE_DUPLICATE"))
                continue
            state.records[identity] = LifecycleRecord(identity, dict(row.values), "active", file_name, row.line_number)
            result.new += 1
            continue

        reference = tuple(row.values.get(name) for name in rule.reference)
        if any(v is None for v in reference):
            result.problems.append(RowProblem(row.line_number, f"{action} row does not name the original: {', '.join(rule.reference)} blank", row.record, level="record", code="LIFECYCLE_ORIGINAL_MISSING"))
            continue
        originals = [r for r in state.matching(reference) if r.identity != identity]
        if row.origin is not None:
            originals = [r for r in originals if r.identity[-1] == row.record] or originals
        if not originals:
            result.problems.append(RowProblem(row.line_number, f"{action} of {_text(rule, reference)}, but no such record has been seen", row.record, level="record", code="LIFECYCLE_ORIGINAL_MISSING"))
            continue
        closed = [r for r in originals if r.status != "active"]
        if closed:
            first = closed[0]
            result.problems.append(RowProblem(row.line_number, f"{action} of {_text(rule, reference)}, which is already {first.status}", row.record, level="record", code="LIFECYCLE_ALREADY_CLOSED"))
            continue

        if action == "cancel":
            for original in originals:
                original.status = "cancelled"
                original.cancelled_by = identity
            state.records[identity] = LifecycleRecord(identity, dict(row.values), "cancel", file_name, row.line_number, cancels=originals[0].identity[: len(reference)])
            result.cancelled += len(originals)
        else:
            for original in originals:
                original.status = "superseded"
                original.superseded_by = identity
            state.records[identity] = LifecycleRecord(identity, dict(row.values), "active", file_name, row.line_number, corrects=originals[0].identity[: len(reference)])
            result.corrected += len(originals)
    return result


def _text(rule: LifecycleRule, identity: tuple) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in zip(rule.identity, identity))
