"""Pattern: full versus delta merge into Silver, decided by a header flag.

A custodian file is either a full refresh or an update, and the header says
which. The spec's `merge` block names the header field and its codes, the
header fields that scope a refresh (for example the remote id), the header
field carrying the business date, and the keys of the merged record.

  refresh   every existing row of the file's scope is replaced by the rows
            in the file as of the file's business date: rows in the file are
            inserted or updated, rows not in the file are retired
  update    rows in the file are inserted or updated on their keys; every
            other row of the scope is carried forward untouched

This is the reference implementation over an in-memory Silver state. The
MERGE the generation plane renders (S3.2.3) must agree with it row for
row; each application also yields the log entry the platform records in
CONTROL.MERGE_LOG, from which stale refreshes are alerted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from astra_knowledge.patterns.result import ParsedFile, RowProblem
from astra_knowledge.registry import MergeRule, SourceSpec


@dataclass
class SilverRow:
    scope: tuple
    key: tuple
    values: dict[str, Any]
    business_date: date | None
    first_file: str
    last_file: str
    last_line: int


@dataclass
class SilverState:
    """Rows keyed by scope and key, as Silver would hold them."""

    rows: dict[tuple, SilverRow] = field(default_factory=dict)

    def in_scope(self, scope: tuple) -> dict[tuple, SilverRow]:
        return {k: r for k, r in self.rows.items() if r.scope == scope}

    def latest_business_date(self, scope: tuple) -> date | None:
        dates = [r.business_date for r in self.rows.values() if r.scope == scope and r.business_date]
        return max(dates) if dates else None


@dataclass(frozen=True)
class MergeLogEntry:
    """What the platform records for every merge (CONTROL.MERGE_LOG)."""

    source: str
    scope: str
    business_date: date | None
    mode: str | None
    file_name: str
    inserted: int
    updated: int
    carried: int
    retired: int


@dataclass
class MergeResult:
    mode: str | None
    scope: tuple
    business_date: date | None
    inserted: int = 0
    updated: int = 0
    carried: int = 0
    retired: int = 0
    problems: list[RowProblem] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return not any(p.level == "file" for p in self.problems)

    def log_entry(self, spec: SourceSpec, file_name: str) -> MergeLogEntry:
        scope_text = ", ".join(f"{n}={v}" for n, v in zip(spec.merge.scope, self.scope)) if spec.merge and spec.merge.scope else "all"
        return MergeLogEntry(spec.label, scope_text, self.business_date, self.mode, file_name, self.inserted, self.updated, self.carried, self.retired)


def _header_values(spec: SourceSpec, parsed: ParsedFile) -> dict[str, Any] | None:
    header = spec.header()
    return parsed.metadata.get(header.label) if header else None


def merge_mode(spec: SourceSpec, parsed: ParsedFile) -> tuple[str | None, RowProblem | None]:
    """The mode the header declares, or a file-level problem."""
    rule = spec.merge
    if rule is None:
        return None, RowProblem(0, f"{spec.label} declares no merge block", level="file")
    header = _header_values(spec, parsed)
    if header is None:
        return None, RowProblem(0, "the file has no header record, so the merge mode is unknown", level="file", code="MERGE_MODE_UNKNOWN")
    code = header.get(rule.mode_field)
    mode = rule.mode_for(code)
    if mode is None:
        accepted = ", ".join(f"{k!r} = {v}" for k, v in rule.modes.items())
        return None, RowProblem(0, f"merge mode {code!r} in header field {rule.mode_field} is not one of {accepted}", level="file", code="MERGE_MODE_UNKNOWN")
    return mode, None


def apply(state: SilverState, spec: SourceSpec, parsed: ParsedFile, file_name: str = "") -> MergeResult:
    """Apply a parsed file to the Silver state in place. A file-level problem leaves the state untouched."""
    rule = spec.merge
    mode, problem = merge_mode(spec, parsed)
    header = _header_values(spec, parsed) or {}
    scope = tuple(header.get(name) for name in (rule.scope if rule else ()))
    business_date = header.get(rule.business_date_field) if rule else None
    result = MergeResult(mode=mode, scope=scope, business_date=business_date)
    if problem:
        result.problems.append(problem)
        return result
    if parsed.rejected:
        result.problems.append(RowProblem(0, "the file was rejected by the parser; nothing merged", level="file"))
        return result

    label = rule.record or spec.logical_records()[0]
    latest = state.latest_business_date(scope)
    if business_date is not None and latest is not None and business_date < latest:
        result.problems.append(RowProblem(0, f"business date {business_date} is earlier than the {latest} already in Silver for this scope; the file is out of order and was not merged", level="file", code="MERGE_OUT_OF_ORDER"))
        return result

    incoming: dict[tuple, Any] = {}
    for row in parsed.rows:
        if row.record != label:
            continue
        blank = [k for k in rule.keys if row.values.get(k) is None]
        if blank:
            result.problems.append(RowProblem(row.line_number, f"key field{'s' if len(blank) > 1 else ''} {', '.join(blank)} blank; the row cannot be merged", row.record, blank[0], level="record", code="MERGE_KEY_BLANK"))
            continue
        key = tuple(row.values[k] for k in rule.keys)
        if key in incoming:
            result.problems.append(RowProblem(row.line_number, f"a second row for {_key_text(rule, key)} in the same file; the first at line {incoming[key].line_number} is kept", row.record, level="record", code="MERGE_DUPLICATE_KEY"))
            continue
        incoming[key] = row

    existing = state.in_scope(scope)
    for key, row in incoming.items():
        full_key = scope + key
        current = state.rows.get(full_key)
        if current is None:
            state.rows[full_key] = SilverRow(scope, key, dict(row.values), business_date, file_name, file_name, row.line_number)
            result.inserted += 1
        else:
            current.values = dict(row.values)
            current.business_date = business_date
            current.last_file = file_name
            current.last_line = row.line_number
            result.updated += 1

    for full_key, row in existing.items():
        if row.key in incoming:
            continue
        if mode == "refresh":
            del state.rows[full_key]
            result.retired += 1
        else:
            result.carried += 1
    return result


def _key_text(rule: MergeRule, key: tuple) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in zip(rule.keys, key))
