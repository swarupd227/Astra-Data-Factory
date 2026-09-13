"""Exception Triage: suggested resolutions per exception, with confidence, grouped by root
cause, and auto-apply only for whitelisted classes at level L3 (S5.8.1, ADR 0048, product spec
Section 6).

Every piece of this agent already has a home in the domain pack, not a new format: a suggested
resolution is the rejection taxonomy's own `resolution` text (`domains/<pack>/rejections.yaml`,
`astra_knowledge.rejections.RejectionCode`) for the exception's code — never invented, since the
taxonomy's `resolution` field is already the reviewed, correct answer a steward wrote. Whether a
class is whitelisted for L3 self-healing is the taxonomy's own `auto_resolve` flag, whose schema
description names this agent by name: "Exception Triage may apply the resolution without a
person (autonomy level L3, with audit)." Root cause is a group of exceptions sharing a code and
either the same raw value or the same field — the closest thing to "the same underlying cause" a
mechanical grouping can say without guessing why.

Confidence is not a model's opinion; it is `acceptance_rate`, the measured fraction of past
`accepted`/`rejected` decisions recorded for a code, Laplace-smoothed so a code with no history
yet starts at a neutral 0.5 rather than a fabricated number. `auto_apply` requires two things at
once — the taxonomy's own whitelist (`auto_resolve: true`) and a confidence that has actually
earned it (`>= AUTO_APPLY_CONFIDENCE`) — so a newly whitelisted class with no track record yet
does not auto-apply on day one, matching the product spec's own text: "Promotion to L3 requires a
measured acceptance rate above a threshold over a stated window."

That per-code confidence gate is this agent's own, always applied. Whether this agent's whitelisted-
exception-classes task class is authorized to act at L3 *at all* is a separate, architect-level
decision — `astra_agents.guardrails`' own registry (S5.13.1, ADR 0053). `gate_auto_apply` applies
it on top: `astra-agents exception-triage run --guardrails ...` forces every auto_apply back to
False when the registry says this task class is not currently at L3, whatever a code's own
confidence says.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astra_core.yamlsource import load
from astra_knowledge.rejections import RejectionCode, Taxonomy, load_taxonomy

# Laplace smoothing: a code with zero recorded decisions starts at PRIOR_ACCEPTED / PRIOR_TOTAL =
# 0.5, neither trusted nor distrusted; each real decision moves it, and it takes a real, observed
# track record to cross AUTO_APPLY_CONFIDENCE, not just a person flipping auto_resolve on.
PRIOR_ACCEPTED = 1
PRIOR_TOTAL = 2
AUTO_APPLY_CONFIDENCE = 0.8

DECISIONS = ("accepted", "rejected")


class ExceptionTriageError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def load_rejections(path: Path) -> Taxonomy:
    path = Path(path)
    if not path.is_file():
        raise ExceptionTriageError(f"rejections file not found: {path}")
    taxonomy, problems = load_taxonomy(path)
    if problems:
        raise ExceptionTriageError("; ".join(p.format() for p in problems))
    return taxonomy


@dataclass(frozen=True)
class ExceptionRecord:
    """One row of the CDM's own Exception entity (domains/<pack>/cdm's Exception, 1.0.yaml) —
    the same columns, not a second shape a person would have to translate."""

    id: str
    rejection_code: str
    level: str
    entity: str | None
    custodian_id: str | None
    field_name: str | None
    raw_value: str | None
    message: str
    record_key: str | None
    raised_at: str
    status: str = "NEW"


REQUIRED_COLUMNS = ("exception_id", "rejection_code", "level", "message", "raised_at")


def load_exceptions(path: Path) -> tuple[ExceptionRecord, ...]:
    path = Path(path)
    if not path.is_file():
        raise ExceptionTriageError(f"exceptions file not found: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = [c.strip().lower() for c in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in fields]
        if missing:
            raise ExceptionTriageError(f"{path}: missing column(s) {', '.join(missing)}")
        records = []
        for i, row in enumerate(reader, start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
            if not row.get("exception_id"):
                raise ExceptionTriageError(f"{path}: line {i}: exception_id is blank")
            records.append(
                ExceptionRecord(
                    id=row["exception_id"],
                    rejection_code=row["rejection_code"],
                    level=row["level"],
                    entity=row.get("entity") or None,
                    custodian_id=row.get("custodian_id") or None,
                    field_name=row.get("field_name") or None,
                    raw_value=row.get("raw_value") or None,
                    message=row["message"],
                    record_key=row.get("record_key") or None,
                    raised_at=row["raised_at"],
                    status=row.get("status") or "NEW",
                )
            )
    return tuple(records)


@dataclass(frozen=True)
class Decision:
    code: str
    decision: str  # accepted | rejected
    at: str
    by: str


def load_decisions(path: Path | None) -> tuple[Decision, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        raise ExceptionTriageError(f"decisions file not found: {path}")
    data = load(path.read_text(encoding="utf-8")) or {}
    return tuple(Decision(code=d["code"], decision=d["decision"], at=d["at"], by=d["by"]) for d in data.get("decisions") or ())


def record_decision(path: Path, *, code: str, decision: str, by: str, at: datetime | None = None) -> tuple[Decision, ...]:
    """Appends one decision to the log and rewrites it. The only way this agent's own confidence
    changes: a real accept or reject, recorded by who made it and when."""
    if decision not in DECISIONS:
        raise ExceptionTriageError(f"'{decision}' is not a decision; decisions are {', '.join(DECISIONS)}")
    if not by.strip():
        raise ExceptionTriageError("who made the decision must be given (--by)")
    existing = load_decisions(path if Path(path).is_file() else None)
    when = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = existing + (Decision(code=code, decision=decision, at=when, by=by.strip()),)
    lines = ["# Exception Triage decisions: one accepted or rejected suggestion per entry, oldest first.", "decisions_version: 0", "", "decisions:"]
    for d in updated:
        lines.append(f"  - {{ code: {d.code}, decision: {d.decision}, at: \"{d.at}\", by: \"{d.by}\" }}")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


def acceptance_rate(code: str, decisions: tuple[Decision, ...]) -> tuple[float, int, int]:
    relevant = [d for d in decisions if d.code == code]
    accepted = sum(1 for d in relevant if d.decision == "accepted")
    total = len(relevant)
    confidence = (accepted + PRIOR_ACCEPTED) / (total + PRIOR_TOTAL)
    return confidence, accepted, total


# -- root-cause grouping and suggestions -----------------------------------------


def _group_key(exc: ExceptionRecord) -> str:
    """The closest thing to "the same underlying cause" a mechanical grouping can say without
    guessing why: the same raw value is the strongest signal (the same missing account number,
    the same unmapped code); failing that, the same field; failing that, just the code itself."""
    if exc.raw_value:
        return f"value:{exc.raw_value}"
    if exc.field_name:
        return f"field:{exc.field_name}"
    return "code"


@dataclass(frozen=True)
class Suggestion:
    rejection_code: str
    group_key: str
    exception_ids: tuple[str, ...]
    sample_raw_value: str | None
    resolution: str | None  # None when the code is not in the taxonomy at all
    confidence: float
    accepted: int
    total_decisions: int
    whitelisted: bool  # the taxonomy's own auto_resolve
    auto_apply: bool  # whitelisted AND a confidence that has actually earned it

    @property
    def count(self) -> int:
        return len(self.exception_ids)

    @property
    def has_resolution(self) -> bool:
        return self.resolution is not None

    def to_dict(self) -> dict:
        return {
            "rejection_code": self.rejection_code,
            "group_key": self.group_key,
            "exception_ids": list(self.exception_ids),
            "count": self.count,
            "sample_raw_value": self.sample_raw_value,
            "resolution": self.resolution,
            "confidence": round(self.confidence, 4),
            "accepted": self.accepted,
            "total_decisions": self.total_decisions,
            "whitelisted": self.whitelisted,
            "auto_apply": self.auto_apply,
        }


@dataclass(frozen=True)
class TriageDraft:
    suggestions: tuple[Suggestion, ...]
    unresolved_codes: tuple[str, ...]  # codes this run saw with no taxonomy entry at all
    acceptance_by_code: dict[str, tuple[float, int, int]]

    @property
    def ok(self) -> bool:
        return not self.unresolved_codes

    @property
    def auto_apply_suggestions(self) -> tuple[Suggestion, ...]:
        return tuple(s for s in self.suggestions if s.auto_apply)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "unresolved_codes": list(self.unresolved_codes),
            "acceptance_by_code": {code: {"confidence": round(rate, 4), "accepted": accepted, "total": total} for code, (rate, accepted, total) in sorted(self.acceptance_by_code.items())},
        }


def generate(exceptions: tuple[ExceptionRecord, ...], taxonomy: Taxonomy, decisions: tuple[Decision, ...] = ()) -> TriageDraft:
    groups: dict[tuple[str, str], list[ExceptionRecord]] = {}
    for exc in exceptions:
        if exc.status != "NEW":
            continue
        groups.setdefault((exc.rejection_code, _group_key(exc)), []).append(exc)

    suggestions: list[Suggestion] = []
    unresolved: set[str] = set()
    for (code, key), members in groups.items():
        rc: RejectionCode | None = taxonomy.code(code)
        confidence, accepted, total = acceptance_rate(code, decisions)
        whitelisted = bool(rc and rc.auto_resolve)
        if rc is None:
            unresolved.add(code)
        suggestions.append(
            Suggestion(
                rejection_code=code,
                group_key=key,
                exception_ids=tuple(sorted(e.id for e in members)),
                sample_raw_value=members[0].raw_value,
                resolution=rc.resolution if rc else None,
                confidence=confidence,
                accepted=accepted,
                total_decisions=total,
                whitelisted=whitelisted,
                auto_apply=whitelisted and confidence >= AUTO_APPLY_CONFIDENCE,
            )
        )
    suggestions.sort(key=lambda s: (-s.count, s.rejection_code, s.group_key))

    all_codes = {s.rejection_code for s in suggestions} | {d.code for d in decisions}
    acceptance_by_code = {code: acceptance_rate(code, decisions) for code in all_codes}

    return TriageDraft(suggestions=tuple(suggestions), unresolved_codes=tuple(sorted(unresolved)), acceptance_by_code=acceptance_by_code)


def gate_auto_apply(draft: TriageDraft, *, authorized: bool) -> TriageDraft:
    """astra_agents.guardrails' own enforcement, applied on top of this agent's per-code
    confidence gate, never instead of it: when this task class is not currently authorized to
    act at L3, every suggestion's auto_apply is forced False, whatever its own confidence says."""
    if authorized:
        return draft
    return replace(draft, suggestions=tuple(replace(s, auto_apply=False) for s in draft.suggestions))


def run(exceptions_path: Path, rejections_path: Path, decisions_path: Path | None = None) -> TriageDraft:
    exceptions = load_exceptions(exceptions_path)
    taxonomy = load_rejections(rejections_path)
    decisions = load_decisions(decisions_path)
    return generate(exceptions, taxonomy, decisions)


# -- the report ----------------------------------------------------------------


def render_markdown(draft: TriageDraft) -> str:
    out = [f"# Exception Triage draft", ""]
    out.append(f"{len(draft.suggestions)} root cause(s) across {sum(s.count for s in draft.suggestions)} exception(s). {len(draft.auto_apply_suggestions)} eligible to auto-apply.")
    out.append("")
    out.append("## Suggestions, by root cause")
    out.append("")
    out.append("| Code | Sample value | Count | Resolution | Confidence | Whitelisted | Auto-apply |")
    out.append("|---|---|---|---|---|---|---|")
    for s in draft.suggestions:
        resolution = s.resolution or "*(no taxonomy entry — no suggestion)*"
        out.append(f"| {s.rejection_code} | {s.sample_raw_value or '-'} | {s.count} | {resolution} | {s.confidence:.0%} ({s.accepted}/{s.total_decisions}) | {'yes' if s.whitelisted else 'no'} | {'**yes**' if s.auto_apply else 'no'} |")
    out.append("")
    if draft.unresolved_codes:
        out.append("## Codes with no taxonomy entry")
        out.append("")
        out.append("Not guessed at — these need a person, or a new code added to the taxonomy first:")
        out.append("")
        for code in draft.unresolved_codes:
            out.append(f"- {code}")
        out.append("")
    out.append("## Acceptance rate, per code")
    out.append("")
    out.append("| Code | Confidence | Accepted | Decisions recorded |")
    out.append("|---|---|---|---|")
    for code, (rate, accepted, total) in sorted(draft.acceptance_by_code.items()):
        out.append(f"| {code} | {rate:.0%} | {accepted} | {total} |")
    out.append("")
    return "\n".join(out)


def write_draft(draft: TriageDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
