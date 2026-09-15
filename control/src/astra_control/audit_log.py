"""Audit log viewer: who approved what, when, searchable across every plane's own approval and
change records, exported to CSV — so SOX evidence is a query, not an archaeology dig (S6.3.11,
ADR 0067, product spec Section 8: "Every approval records who, what, the evidence seen, and the
agent version").

This module reads six real, already-written records this platform already produces and unifies
them into one `AuditRecord` shape — the same "many heterogeneous sources, one filterable item"
pattern `astra_control.queue` already established for its own four report kinds (S6.3.2), scaled
to six here: `astra_control.config_studio.PromotionRequest`, `astra_control.drift_review.
ChangeRequest` and `astra_knowledge.rules.HistoryEntry` are read by calling this plane's own
already-built loaders directly (`config_studio`/`drift_review` are already `control/` modules;
`astra_knowledge` is the Knowledge plane, already imported throughout this plane). `astra_agents.
guardrails.LevelChange` and `astra_agents.gate_evidence_compiler.Approval` are read from their own
real YAML log files directly — the same "no new agent import, read the file it already wrote"
boundary `astra_control.queue` and `astra_control.agent_review` already established, since
`guardrails.py` and `gate_evidence_compiler.py` both live in the Agents plane. `astra_control.
board.Transition` (a custodian moved on the factory board, `by` optionally recorded) is the sixth
source — a transition with no recorded `by` carries nothing to audit and is skipped, never shown
as an anonymous action.

**Two honest gaps, confirmed by reading every one of these six sources, not assumed:**

1. **Nothing anywhere in this codebase records an "agent version" on an approval** — despite the
   backlog (S6.2.1, and this very story) and the product spec (Section 8: "...and the agent
   version") both naming it explicitly, no dataclass in `control/`, `agents/`, or `verification/`
   has that field. `PROVENANCE.json` (the Generation plane's own build artifact) is the closest
   thing that exists in code, but it records a render's own tool version, not an approver, and is
   never linked to any approval record. Every `AuditRecord.agent_version` here is `None`, always —
   an honest "not recorded," never a fabricated version string.
2. **No audit-shaped record anywhere stores a reference to the evidence actually shown at
   decision time** — every one has at most a free-text `note`/`reason`; `guardrails.LevelChange`'s
   own structured `evidence` (`acceptance_rate`, `sample_size`, `window`) is the sole exception,
   and even that is a summary statistic, never a report path, diff, or citation. `AuditRecord.
   evidence` here is that free text (or the guardrails' own structured summary, rendered), never
   a fabricated reference to something the reviewer did not actually see.

**S6.3.6's own agent-suggestion accept/reject (`astra_control.agent_review`) has no audit trail
at all and is absent from this aggregation.** Its gold-set `Case` schema is `{id, tier, input,
expected}`, `additionalProperties: false` — structurally no room for a `by`/`at` field (ADR
0062). This is not an oversight in this module; there is genuinely nothing to read.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from astra_core.yamlsource import SourceError, load
from astra_knowledge.rules import Catalog

from astra_control.board import BoardError, load_board
from astra_control.config_studio import load_promotion_requests
from astra_control.drift_review import load_change_requests
from astra_control.spec_viewer import SpecViewerError, load_registry, load_spec

CSV_COLUMNS = ("kind", "action", "user", "at", "custodian", "subject", "evidence", "agent_version", "source")


class AuditLogError(RuntimeError):
    pass


def _read_yaml(path: Path) -> dict:
    """Never raises: a missing or unreadable log contributes nothing, the same "no evidence, not
    an error" shape astra_control.queue's own _read_json already established."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        return load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError):
        return {}


# -- the unified record --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRecord:
    kind: str
    action: str
    user: str
    at: str
    custodian: str | None
    subject: str
    evidence: str
    agent_version: str | None
    source: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "action": self.action,
            "user": self.user,
            "at": self.at,
            "custodian": self.custodian,
            "subject": self.subject,
            "evidence": self.evidence,
            "agent_version": self.agent_version,
            "source": self.source,
        }


# -- one reader per source, each defensive -- a bad source contributes nothing, never breaks the aggregate --


def promotion_requests_from(path: Path) -> tuple[AuditRecord, ...]:
    requests = load_promotion_requests(path if Path(path).is_file() else None)
    return tuple(
        AuditRecord(
            kind="promotion_request",
            action="config-studio.request-promotion",
            user=r.requested_by,
            at=r.at,
            custodian=r.custodian_id,
            subject=f"{r.custodian_id} ({r.tier} tier)" + (f", reviewed by {r.reviewed_by}" if r.reviewed_by else ""),
            evidence=r.note or "not recorded",
            agent_version=None,
            source=str(path),
        )
        for r in requests
    )


def drift_approvals_from(path: Path, *, specs_dir: Path | None = None, root: Path | None = None) -> tuple[AuditRecord, ...]:
    """`ChangeRequest` carries no custodian of its own — when `specs_dir` is given, this looks the
    spec up in the real registry for its own `custodians` field (the same lookup `astra_control.
    drift_review.review` already does); a spec that no longer resolves (renamed, since removed)
    degrades to `custodian=None` rather than failing the whole read."""
    requests = load_change_requests(path if Path(path).is_file() else None)
    registry = None
    if specs_dir is not None:
        try:
            registry = load_registry(Path(specs_dir), root)
        except SpecViewerError:
            registry = None
    records = []
    for r in requests:
        custodian = None
        if registry is not None:
            try:
                spec = load_spec(registry, r.spec_id, r.spec_version)
                custodian = ", ".join(spec.custodians) or None
            except SpecViewerError:
                custodian = None
        records.append(
            AuditRecord(
                kind="drift_approval",
                action="drift-review.approve",
                user=r.approved_by,
                at=r.at,
                custodian=custodian,
                subject=f"{r.spec_id} {r.spec_version}",
                evidence=r.note or "not recorded",
                agent_version=None,
                source=str(path),
            )
        )
    return tuple(records)


def rule_status_changes_from(rules_dir: Path, root: Path | None = None) -> tuple[AuditRecord, ...]:
    catalog, problems = Catalog.load(Path(rules_dir), root)
    if problems:
        return ()
    records = []
    for rule in catalog.rules:
        custodian = ", ".join(rule.custodians) or None
        for entry in rule.history:
            records.append(
                AuditRecord(
                    kind="rule_status_change",
                    action="rule-review.set-status",
                    user=entry.by,
                    at=entry.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    custodian=custodian,
                    subject=f"{rule.id} -> {entry.status}",
                    evidence=entry.note or "not recorded",
                    agent_version=None,
                    source=str(rules_dir),
                )
            )
    return tuple(records)


def guardrail_changes_from(path: Path) -> tuple[AuditRecord, ...]:
    data = _read_yaml(path)
    records = []
    for c in data.get("changes") or ():
        reason = c.get("reason") or "not recorded"
        evidence_data = c.get("evidence")
        evidence = f"{reason} (acceptance_rate={evidence_data.get('acceptance_rate')}, sample_size={evidence_data.get('sample_size')}, window={evidence_data.get('window')})" if evidence_data else reason
        records.append(
            AuditRecord(
                kind="guardrail_change",
                action="guardrails.set-level",
                user=c.get("approver", ""),
                at=c.get("at", ""),
                custodian=None,
                subject=f"{c.get('agent')}.{c.get('task_class')} -> {c.get('level')}",
                evidence=evidence,
                agent_version=None,
                source=str(path),
            )
        )
    return tuple(records)


def gate_approvals_from(path: Path) -> tuple[AuditRecord, ...]:
    data = _read_yaml(path)
    return tuple(
        AuditRecord(
            kind="gate_approval",
            action="gate-evidence.approve",
            user=a.get("approver", ""),
            at=a.get("at", ""),
            custodian=None,
            subject=a.get("release", ""),
            evidence=a.get("note") or "not recorded",
            agent_version=None,
            source=str(path),
        )
        for a in data.get("approvals") or ()
    )


def board_moves_from(path: Path) -> tuple[AuditRecord, ...]:
    """A transition with no recorded `by` (`astra_control.board.Transition.by` is optional)
    carries nothing to audit and is skipped, never shown as an anonymous action."""
    if not Path(path).is_file():
        return ()
    try:
        board = load_board(Path(path))
    except BoardError:
        return ()
    records = []
    for card in board.cards:
        for t in card.transitions:
            if not t.by:
                continue
            records.append(
                AuditRecord(
                    kind="board_move",
                    action="board.move",
                    user=t.by,
                    at=t.at,
                    custodian=card.custodian_id,
                    subject=f"{card.custodian_id} -> {t.station.value}",
                    evidence="not recorded",
                    agent_version=None,
                    source=str(path),
                )
            )
    return tuple(records)


# -- aggregating every source together -------------------------------------------------------


@dataclass(frozen=True)
class AuditSources:
    promotion_requests: tuple[Path, ...] = ()
    drift_change_requests: tuple[Path, ...] = ()
    rules_dir: Path | None = None
    rules_root: Path | None = None
    specs_dir: Path | None = None
    guardrail_logs: tuple[Path, ...] = ()
    gate_approval_logs: tuple[Path, ...] = ()
    boards: tuple[Path, ...] = ()


def build_audit_log(sources: AuditSources) -> tuple[AuditRecord, ...]:
    records: list[AuditRecord] = []
    for p in sources.promotion_requests:
        records.extend(promotion_requests_from(p))
    for p in sources.drift_change_requests:
        records.extend(drift_approvals_from(p, specs_dir=sources.specs_dir, root=sources.rules_root))
    if sources.rules_dir is not None:
        records.extend(rule_status_changes_from(sources.rules_dir, sources.rules_root))
    for p in sources.guardrail_logs:
        records.extend(guardrail_changes_from(p))
    for p in sources.gate_approval_logs:
        records.extend(gate_approvals_from(p))
    for p in sources.boards:
        records.extend(board_moves_from(p))
    return tuple(sorted(records, key=lambda r: r.at))


# -- AC1: filter by user, custodian, date, action --------------------------------------------


def filter_records(
    records: tuple[AuditRecord, ...],
    *,
    user: str | None = None,
    custodian: str | None = None,
    action: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[AuditRecord, ...]:
    result = records
    if user:
        needle = user.lower()
        result = tuple(r for r in result if needle in r.user.lower())
    if custodian:
        needle = custodian.lower()
        result = tuple(r for r in result if r.custodian and needle in r.custodian.lower())
    if action:
        needle = action.lower()
        result = tuple(r for r in result if needle in r.action.lower())
    if start:
        result = tuple(r for r in result if r.at >= start)
    if end:
        result = tuple(r for r in result if r.at <= end)
    return result


# -- AC2: export to CSV -----------------------------------------------------------------------


def to_csv(records: tuple[AuditRecord, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for r in records:
        writer.writerow(r.to_dict())
    return buffer.getvalue()


def write_csv(records: tuple[AuditRecord, ...], path: Path) -> Path:
    path = Path(path)
    try:
        path.write_text(to_csv(records), encoding="utf-8", newline="")
    except OSError as exc:
        raise AuditLogError(f"could not write {path}: {exc}") from exc
    return path


# -- the view --------------------------------------------------------------------------


def render_markdown(records: tuple[AuditRecord, ...]) -> str:
    out = [f"# Audit log ({len(records)} record(s))", ""]
    if not records:
        out += ["No records match.", ""]
        return "\n".join(out)
    out.append("| At | User | Action | Custodian | Subject | Evidence |")
    out.append("|---|---|---|---|---|---|")
    for r in records:
        out.append(f"| {r.at} | {r.user} | {r.action} | {r.custodian or '-'} | {r.subject} | {r.evidence} |")
    out.append("")
    return "\n".join(out)
