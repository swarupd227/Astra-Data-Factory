"""Gate Evidence Compiler: a gate pack assembled from verification results, approvals and
metrics, per release, against named criteria (S5.11.1, ADR 0051, product spec Section 6).

No fixed list of gate criteria existed anywhere in this repository before this story (ADR 0034,
written for the parity report, said as much: compiling the full pack was "Control-plane work well
past E4," with parity's own `report.json` left as "the one piece of evidence... in the place a
later compiler would look for it"). `GATE_CRITERIA` names seven, one for each verification tool
this factory has already built — DQ, parity, volume, chaos, DR, agent evaluation — plus a human
approval, each read from the exact `report.json` shape that tool's own `to_dict()` already
produces. Nothing here re-scores anything: `meets_target`, `proven`, `status` and `passed` are
already computed, real booleans this agent only reads.

The guardrail this story asks for — a criterion with no evidence is shown as NOT MET — is not a
special case to remember; it falls out of the design. Each criterion's evidence path is optional;
when it is not given, or the file at it does not exist, that criterion is NOT MET with no evidence
attached, the same as if the underlying tool had actually failed. A pack is never silently missing
a criterion — every one of the seven is always listed, met or not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from astra_core.yamlsource import load

NO_EVIDENCE = "no evidence found"


class GateEvidenceCompilerError(RuntimeError):
    pass


# -- reading a tool's own report.json -------------------------------------------


def _read_json(path: Path | None) -> dict[str, Any] | None:
    """None for a missing OR an unreadable file: a corrupt report is itself evidence something
    upstream is broken, not a reason for this agent to crash rather than show NOT MET."""
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _dq_met(d: dict) -> bool:
    return bool(d.get("meets_target"))


def _dq_summary(d: dict) -> str:
    return f"score {d.get('score')} (target {d.get('target')})"


def _parity_met(d: dict) -> bool:
    return bool(d.get("meets_target"))


def _parity_summary(d: dict) -> str:
    return f"match_rate {d.get('match_rate')} (target {d.get('target')})"


def _volume_met(d: dict) -> bool:
    return d.get("status") == "published"


def _volume_summary(d: dict) -> str:
    return f"status {d.get('status')}"


def _chaos_met(d: dict) -> bool:
    return bool(d.get("proven"))


def _chaos_summary(d: dict) -> str:
    return f"status {d.get('status')}, proven {d.get('proven')}"


def _dr_met(d: dict) -> bool:
    return bool(d.get("proven"))


def _dr_summary(d: dict) -> str:
    return f"status {d.get('status')}, within_rto {d.get('within_rto')}, within_rpo {d.get('within_rpo')}"


def _agent_eval_met(d: dict) -> bool:
    return bool(d.get("all_passed", d.get("passed")))


def _agent_eval_summary(d: dict) -> str:
    if "all_passed" in d:
        return f"all_passed {d.get('all_passed')} ({len(d.get('results', []))} agent(s) scored, {len(d.get('skipped', []))} skipped)"
    return f"passed {d.get('passed')} ({d.get('agent', '?')})"


@dataclass(frozen=True)
class CriterionSpec:
    id: str
    name: str
    description: str
    met: Callable[[dict], bool]
    summarize: Callable[[dict], str]


GATE_CRITERIA: tuple[CriterionSpec, ...] = (
    CriterionSpec("dq_score", "Data quality score meets target", "The DQ runner's score for this source meets its configured target.", _dq_met, _dq_summary),
    CriterionSpec("parity", "Parity meets target", "The parity report's match rate meets the platform target.", _parity_met, _parity_summary),
    CriterionSpec("volume", "Volume test published", "The volume test completed and published within its time budget.", _volume_met, _volume_summary),
    CriterionSpec("chaos", "Chaos scenarios proven", "Every injected chaos scenario was detected, alerted and recovered.", _chaos_met, _chaos_summary),
    CriterionSpec("dr_drill", "DR drill proven", "The disaster-recovery drill met its RTO and RPO.", _dr_met, _dr_summary),
    CriterionSpec("agent_eval", "Agent evaluation passed", "The agents this release depends on meet their own gold-set thresholds.", _agent_eval_met, _agent_eval_summary),
)


# -- approvals: a small, append-only log, mirroring exception_triage's decisions log ------------


@dataclass(frozen=True)
class Approval:
    release: str
    approver: str
    at: str
    note: str | None = None

    def to_dict(self) -> dict:
        d = {"release": self.release, "approver": self.approver, "at": self.at}
        if self.note:
            d["note"] = self.note
        return d


def load_approvals(path: Path | None) -> tuple[Approval, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    data = load(path.read_text(encoding="utf-8")) or {}
    return tuple(Approval(release=a["release"], approver=a["approver"], at=a["at"], note=a.get("note")) for a in data.get("approvals") or ())


def record_approval(path: Path, *, release: str, approver: str, note: str | None = None, at: datetime | None = None) -> tuple[Approval, ...]:
    """Appends one approval to the log and rewrites it, the same shape exception_triage's own
    record_decision uses for the same reason: a small, append-only, human-made record."""
    if not release.strip():
        raise GateEvidenceCompilerError("which release this approves must be given (--release)")
    if not approver.strip():
        raise GateEvidenceCompilerError("who approved it must be given (--approver)")
    existing = load_approvals(path if Path(path).is_file() else None)
    when = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = existing + (Approval(release=release.strip(), approver=approver.strip(), at=when, note=note.strip() if note else None),)
    lines = ["# Gate Evidence Compiler approvals: one release approval per entry, oldest first.", "approvals_version: 0", "", "approvals:"]
    for a in updated:
        text = f"  - {{ release: {a.release!r}, approver: {a.approver!r}, at: \"{a.at}\""
        if a.note:
            text += f", note: {a.note!r}"
        lines.append(text + " }")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- the pack --------------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    description: str
    evidence_path: str | None
    evidence: dict[str, Any] | None
    met: bool
    summary: str

    @property
    def status(self) -> str:
        return "MET" if self.met else "NOT MET"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "status": self.status, "met": self.met, "evidence_path": self.evidence_path, "evidence": self.evidence, "summary": self.summary}


@dataclass(frozen=True)
class GatePack:
    release: str
    criteria: tuple[Criterion, ...]

    @property
    def all_met(self) -> bool:
        return all(c.met for c in self.criteria)

    @property
    def unmet(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if not c.met)

    def to_dict(self) -> dict:
        return {"release": self.release, "all_met": self.all_met, "criteria": [c.to_dict() for c in self.criteria]}


@dataclass(frozen=True)
class EvidenceSources:
    dq_report: Path | None = None
    parity_report: Path | None = None
    volume_report: Path | None = None
    chaos_report: Path | None = None
    dr_report: Path | None = None
    agent_eval_report: Path | None = None
    approvals: Path | None = None


_REPORT_FIELDS = ("dq_report", "parity_report", "volume_report", "chaos_report", "dr_report", "agent_eval_report")


def generate(release: str, sources: EvidenceSources) -> GatePack:
    criteria = []
    for spec, field_name in zip(GATE_CRITERIA, _REPORT_FIELDS):
        path = getattr(sources, field_name)
        data = _read_json(path)
        if data is None:
            criteria.append(Criterion(spec.id, spec.name, spec.description, str(path) if path else None, None, False, NO_EVIDENCE))
        else:
            criteria.append(Criterion(spec.id, spec.name, spec.description, str(path), data, spec.met(data), spec.summarize(data)))

    approvals = load_approvals(sources.approvals)
    approval = next((a for a in approvals if a.release == release), None)
    if approval is None:
        criteria.append(Criterion("approval", "Release approved", "A person has recorded approval for this specific release.", str(sources.approvals) if sources.approvals else None, None, False, NO_EVIDENCE))
    else:
        criteria.append(Criterion("approval", "Release approved", "A person has recorded approval for this specific release.", str(sources.approvals), approval.to_dict(), True, f"approved by {approval.approver} at {approval.at}"))

    return GatePack(release=release, criteria=tuple(criteria))


def run(release: str, sources: EvidenceSources) -> GatePack:
    return generate(release, sources)


# -- the report ------------------------------------------------------------------


def render_markdown(pack: GatePack) -> str:
    out = [f"# Gate pack: {pack.release}", ""]
    out.append(f"{len(pack.criteria)} criteri(on/a). Ready to release: {'yes' if pack.all_met else 'no'}.")
    out.append("")
    out.append("| Criterion | Status | Evidence |")
    out.append("|---|---|---|")
    for c in pack.criteria:
        mark = "**MET**" if c.met else "**NOT MET**"
        out.append(f"| {c.name} | {mark} | {c.summary} |")
    out.append("")
    if pack.unmet:
        out.append("## Not met — gate decisions are made on this pack alone")
        out.append("")
        for c in pack.unmet:
            out.append(f"- **{c.name}**: {c.description}")
        out.append("")
    return "\n".join(out)


def write_pack(pack: GatePack, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "gate_pack.md"
    data_path = out / "gate_pack.json"
    report_path.write_text(render_markdown(pack), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(pack.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
