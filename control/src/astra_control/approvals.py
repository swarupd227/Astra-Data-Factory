"""Approvals and autonomy levels: approve or reject a draft with a comment, and see the autonomy
level of the agent that proposed it, so every change has an accountable person (S6.2.1, ADR 0070,
product spec Section 8: "Every approval records who, what, the evidence seen, and the agent
version").

This is a new, general-purpose approval record — richer than any of the five approval-shaped
flows F6.3 already built (`rule_review.change_status`, `agent_review.accept`/`reject`,
`drift_review.approve`, `autonomy_admin.set_level`, `config_studio.request_promotion`), none of
which records "the evidence seen" as a reference, or an agent version at all. It does not replace
or wrap any of them — nothing in this backlog names S6.2.1 as their foundation (F6.3 was already
built and worked around this story's own absence: `docs/adr/0067-audit-log-viewer.md`'s own point
3 already names S6.2.1 and the product spec, not built code, as the source of the "agent version"
and "evidence seen" vocabulary). This module is its own, later, general mechanism a future
integration can call for a draft with no dedicated review flow of its own — never a retrofit of
the five already-built ones.

**"Agent version" has no real source anywhere in this codebase and is never fabricated.** The
product spec names it as its own field, distinct from "model" (Appendix A: "PROVENANCE.json —
inputs, agent versions, models, approvers"). The closest real thing — an LLM model name on three
agents' own `AnthropicClient` classes (`spec_reader`/`rule_recovery`/`modeler`, each with its own
per-agent `--model` flag, defaulting to `"claude-sonnet-5"`) — is a genuinely different concept
the spec's own text does not conflate with "agent version," and only exists for 3 of 11
draft-producing agents. `agent_version` here is an optional, caller-supplied string, honestly
`None` when not given — the same "no live X" honesty this plane has already practiced for a live
SSO provider, a cost figure, a run log, an agent's own version.

**"The evidence seen" is a real, checked file path, never a fabricated pointer** — the same
"store the path, read generically, never assume internal shape" pattern
`astra_agents.gate_evidence_compiler.Criterion.evidence_path` already established, the one place
in this codebase that already generalizes across heterogeneous agent reports this way. A given
`evidence_path` is checked to exist before the approval or rejection is recorded — claiming
evidence was shown that does not exist would be worse than recording none.

**"Rejection returns the item to the agent with the comment" writes a real `rejection.yaml` file
into the draft's own real directory — not a live agent re-run.** Every agent's own CLI in this
codebase runs stateless, one shot, from files given on the command line
(`agents/src/astra_agents/cli.py`'s own module docstring); none reads a prior rejection or
feedback file as an input today, and making one do so would mean changing the Agents plane
itself — out of bounds here, the same "never import `astra_agents`" boundary extended to never
writing into its own input contract either. This module writes the record a future agent
enhancement could read, in the same directory its own `report.json`/`report.md` already live
(`work/<agent>/<subject>/`, the real, already-established draft-not-registry layout) — it does
not make any agent read it today.

**The autonomy level shown is `astra_control.autonomy_admin.current_level`, called directly —
not a second lookup.** Both modules live in `control/`; nothing here reimplements it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astra_core.yamlsource import SourceError, load

from astra_control.autonomy_admin import LevelChange, current_level


class ApprovalError(RuntimeError):
    pass


def _now(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml_str(value: str) -> str:
    return json.dumps(value)


def _yaml_field(value: str | None) -> str:
    return _yaml_str(value) if value is not None else "null"


def _check_evidence(evidence_path: str | None) -> None:
    if evidence_path is not None and not Path(evidence_path).is_file():
        raise ApprovalError(f"evidence file not found: {evidence_path}")


# -- AC1: approval stores user, time, evidence seen, agent version, and the proposer's own level --


@dataclass(frozen=True)
class Approval:
    subject: str
    agent: str
    task_class: str
    autonomy_level: str
    approved_by: str
    at: str
    evidence_path: str | None
    agent_version: str | None
    comment: str | None

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "agent": self.agent,
            "task_class": self.task_class,
            "autonomy_level": self.autonomy_level,
            "approved_by": self.approved_by,
            "at": self.at,
            "evidence_path": self.evidence_path,
            "agent_version": self.agent_version,
            "comment": self.comment,
        }


def load_approvals(path: Path | None) -> tuple[Approval, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    try:
        data = load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError) as exc:
        raise ApprovalError(f"{path}: {exc}") from exc
    return tuple(
        Approval(
            subject=a["subject"],
            agent=a["agent"],
            task_class=a["task_class"],
            autonomy_level=a["autonomy_level"],
            approved_by=a["approved_by"],
            at=a["at"],
            evidence_path=a.get("evidence_path"),
            agent_version=a.get("agent_version"),
            comment=a.get("comment"),
        )
        for a in data.get("approvals") or ()
    )


def approve(
    path: Path,
    *,
    subject: str,
    agent: str,
    task_class: str,
    approved_by: str,
    guardrails_changes: tuple[LevelChange, ...] = (),
    evidence_path: str | None = None,
    agent_version: str | None = None,
    comment: str | None = None,
    at: datetime | None = None,
) -> tuple[Approval, ...]:
    """Refused outright, nothing written, on a blank subject/agent/task_class/approved_by, or an
    `evidence_path` that does not exist."""
    subject, agent, task_class, approved_by = subject.strip(), agent.strip(), task_class.strip(), approved_by.strip()
    if not subject:
        raise ApprovalError("subject must be given")
    if not agent:
        raise ApprovalError("agent must be given")
    if not task_class:
        raise ApprovalError("task_class must be given")
    if not approved_by:
        raise ApprovalError("approved_by must be given")
    _check_evidence(evidence_path)

    existing = load_approvals(path if Path(path).is_file() else None)
    record = Approval(
        subject=subject,
        agent=agent,
        task_class=task_class,
        autonomy_level=current_level(guardrails_changes, agent, task_class),
        approved_by=approved_by,
        at=_now(at),
        evidence_path=evidence_path,
        agent_version=agent_version,
        comment=comment.strip() if comment and comment.strip() else None,
    )
    updated = existing + (record,)

    lines = ["# Approvals: one entry per approval, oldest first. Who, when, the evidence seen and", "# the proposing agent's own autonomy level at the time -- an accountable person for every change.", "approvals_version: 0", "", "approvals:"]
    for a in updated:
        lines.append(
            "  - { subject: "
            + _yaml_str(a.subject)
            + ", agent: "
            + a.agent
            + ", task_class: "
            + a.task_class
            + ", autonomy_level: "
            + a.autonomy_level
            + ", approved_by: "
            + _yaml_str(a.approved_by)
            + ", at: "
            + _yaml_str(a.at)
            + ", evidence_path: "
            + _yaml_field(a.evidence_path)
            + ", agent_version: "
            + _yaml_field(a.agent_version)
            + ", comment: "
            + _yaml_field(a.comment)
            + " }"
        )
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- AC2: rejection returns the item to the agent with the comment -------------------------------


@dataclass(frozen=True)
class Rejection:
    subject: str
    agent: str
    task_class: str
    autonomy_level: str
    rejected_by: str
    at: str
    evidence_path: str | None
    agent_version: str | None
    comment: str

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "agent": self.agent,
            "task_class": self.task_class,
            "autonomy_level": self.autonomy_level,
            "rejected_by": self.rejected_by,
            "at": self.at,
            "evidence_path": self.evidence_path,
            "agent_version": self.agent_version,
            "comment": self.comment,
        }


def load_rejections(path: Path | None) -> tuple[Rejection, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    try:
        data = load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError) as exc:
        raise ApprovalError(f"{path}: {exc}") from exc
    return tuple(
        Rejection(
            subject=r["subject"],
            agent=r["agent"],
            task_class=r["task_class"],
            autonomy_level=r["autonomy_level"],
            rejected_by=r["rejected_by"],
            at=r["at"],
            evidence_path=r.get("evidence_path"),
            agent_version=r.get("agent_version"),
            comment=r["comment"],
        )
        for r in data.get("rejections") or ()
    )


def _render_rejection_file(rejection: Rejection) -> str:
    lines = [
        "# This draft was rejected -- a future agent run could read this file as feedback; nothing",
        "# in this codebase's own agent CLIs reads it today (astra_control.approvals' own module docstring).",
        "rejection_version: 0",
        "",
        "rejection:",
        f"  subject: {_yaml_str(rejection.subject)}",
        f"  rejected_by: {_yaml_str(rejection.rejected_by)}",
        f"  at: {_yaml_str(rejection.at)}",
        f"  comment: {_yaml_str(rejection.comment)}",
        "",
    ]
    return "\n".join(lines)


def reject(
    path: Path,
    *,
    subject: str,
    agent: str,
    task_class: str,
    rejected_by: str,
    comment: str,
    draft_dir: Path | None = None,
    guardrails_changes: tuple[LevelChange, ...] = (),
    evidence_path: str | None = None,
    agent_version: str | None = None,
    at: datetime | None = None,
) -> tuple[Rejection, ...]:
    """Refused outright, nothing written, on a blank subject/agent/task_class/rejected_by/comment
    — AC2's own "with the comment" makes the comment required, unlike an approval's own optional
    one. `draft_dir`, when given, gets a real `rejection.yaml` written into it — the draft's own
    real directory (`work/<agent>/<subject>/`) — "returning the item" (module docstring)."""
    subject, agent, task_class, rejected_by = subject.strip(), agent.strip(), task_class.strip(), rejected_by.strip()
    if not subject:
        raise ApprovalError("subject must be given")
    if not agent:
        raise ApprovalError("agent must be given")
    if not task_class:
        raise ApprovalError("task_class must be given")
    if not rejected_by:
        raise ApprovalError("rejected_by must be given")
    if not comment or not comment.strip():
        raise ApprovalError("comment must be given")
    _check_evidence(evidence_path)

    existing = load_rejections(path if Path(path).is_file() else None)
    record = Rejection(
        subject=subject,
        agent=agent,
        task_class=task_class,
        autonomy_level=current_level(guardrails_changes, agent, task_class),
        rejected_by=rejected_by,
        at=_now(at),
        evidence_path=evidence_path,
        agent_version=agent_version,
        comment=comment.strip(),
    )
    updated = existing + (record,)

    lines = ["# Rejections: one entry per rejection, oldest first.", "rejections_version: 0", "", "rejections:"]
    for r in updated:
        lines.append(
            "  - { subject: "
            + _yaml_str(r.subject)
            + ", agent: "
            + r.agent
            + ", task_class: "
            + r.task_class
            + ", autonomy_level: "
            + r.autonomy_level
            + ", rejected_by: "
            + _yaml_str(r.rejected_by)
            + ", at: "
            + _yaml_str(r.at)
            + ", evidence_path: "
            + _yaml_field(r.evidence_path)
            + ", agent_version: "
            + _yaml_field(r.agent_version)
            + ", comment: "
            + _yaml_str(r.comment)
            + " }"
        )
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")

    if draft_dir is not None:
        draft_dir = Path(draft_dir)
        if not draft_dir.is_dir():
            raise ApprovalError(f"draft directory not found: {draft_dir}")
        (draft_dir / "rejection.yaml").write_text(_render_rejection_file(record), encoding="utf-8", newline="\n")

    return updated


# -- the view --------------------------------------------------------------------------


def render_approvals_markdown(approvals: tuple[Approval, ...]) -> str:
    out = ["# Approvals", ""]
    if not approvals:
        out += ["No approvals recorded yet.", ""]
        return "\n".join(out)
    out.append("| Subject | Agent | Task class | Level | By | At | Evidence | Agent version | Comment |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for a in approvals:
        out.append(f"| {a.subject} | {a.agent} | {a.task_class} | {a.autonomy_level} | {a.approved_by} | {a.at} | {a.evidence_path or '-'} | {a.agent_version or '-'} | {a.comment or '-'} |")
    out.append("")
    return "\n".join(out)


def render_rejections_markdown(rejections: tuple[Rejection, ...]) -> str:
    out = ["# Rejections", ""]
    if not rejections:
        out += ["No rejections recorded yet.", ""]
        return "\n".join(out)
    out.append("| Subject | Agent | Task class | Level | By | At | Comment |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rejections:
        out.append(f"| {r.subject} | {r.agent} | {r.task_class} | {r.autonomy_level} | {r.rejected_by} | {r.at} | {r.comment} |")
    out.append("")
    return "\n".join(out)
