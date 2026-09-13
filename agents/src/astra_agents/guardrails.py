"""Guardrails and autonomy: L0-L3 levels enforced per agent per task class
(S5.13.1, ADR 0053, product spec Section 8).

The product spec names four levels, and only four — a closed vocabulary, not something an agent
or a client invents a fifth of:

  L0  Observe          the agent runs, output is logged, nobody sees it in the flow
  L1  Suggest           output shown to a human who decides
  L2  Prepare           the agent prepares the change and the evidence; a human approves with one click
  L3  Act with audit    the agent applies the change; a human is notified; reversible

A task class is free text an architect names ("whitelisted_exception_classes", "docs",
"complex_tier_mapping", ...) — the spec gives examples, not a closed list, because new agents and
new task classes arrive over time; only the level itself is closed. A level is never just set: it
is *changed*, once, with a reason and an approver, into an append-only log — the same shape
`astra_agents.gate_evidence_compiler`'s approvals log and `astra_agents.exception_triage`'s
decisions log already use, because this is the same kind of fact: a person's decision, recorded so
a later reader does not have to take anyone's word for it. The Control plane's own real autonomy
admin surface (S6.3.12, product spec's Section 6.3) does not exist yet; this is a real, working
stand-in with the same shape a later UI-backed version would likely take, the same relationship
`astra_agents.gate_evidence_compiler`'s approval log already has to S6.2.1's own approvals API.

The current level of an (agent, task class) is simply the most recent change recorded for it —
never a separate, second "current state" file that could disagree with its own history. An
(agent, task class) with no change recorded at all is L0: "new agent or new domain pack in shadow
mode" is the product spec's own words for exactly this case, so a missing entry defaults to the
safest level rather than raising.

Promotion is not free-text-gated by a person's good judgement alone: this module enforces the
spec's own written rule structurally. Every change needs an approver and a reason (no blank
either) — "level change requires approval and is logged." A change *to* L3 additionally needs
evidence — a measured acceptance rate, the sample size it was measured over, and the window it
covers — checked against a fixed threshold and a fixed minimum sample size before the change is
even written to the log; a change with no evidence, or evidence that does not clear the bar, is
rejected outright, not recorded and flagged. "Promotion to L3 requires a measured acceptance rate
above a threshold over a stated window" is the product spec's own sentence, word for word.

`permits` is this module's own enforcement hook: given the log and an (agent, task class), it
answers whether that pair is currently authorized to act at a given level. `astra_agents.
exception_triage.gate_auto_apply` is the one place in this plane that already had L3-shaped
behavior before this story (whitelisted exception classes, auto-applying above a per-code
confidence) — `astra-agents exception-triage run --guardrails ...` now also consults this
module's own registry before auto-applying, on top of its own per-code confidence, never instead
of it. That is what "enforced" means here: a real, working call site, not only a data structure
nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from astra_core.yamlsource import load

# "Promotion to L3 requires a measured acceptance rate above a threshold over a stated window"
# (product spec Section 8) — the same bar astra_agents.exception_triage.AUTO_APPLY_CONFIDENCE
# already holds a single rejection code's own accept/reject history to. The two are not the same
# measurement (this one is an architect's evidence for a whole task class; that one is a code's
# own running confidence) and are deliberately not code-coupled, but they agree on the number.
L3_ACCEPTANCE_THRESHOLD = 0.8
# A rate measured over too few decisions is not "measured"; five lucky accepts should not read as
# evidence. There is no principled derivation for this number in the product spec; it is this
# module's own floor, named so a promotion's rejection message can say exactly what was missing.
L3_MINIMUM_SAMPLE = 20


class GuardrailsError(RuntimeError):
    pass


class Level(Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def meaning(self) -> str:
        return MEANING[self]


_RANK = {Level.L0: 0, Level.L1: 1, Level.L2: 2, Level.L3: 3}

MEANING = {
    Level.L0: "Observe: the agent runs, output is logged, nobody sees it in the flow.",
    Level.L1: "Suggest: output shown to a human who decides.",
    Level.L2: "Prepare: the agent prepares the change and the evidence; a human approves with one click.",
    Level.L3: "Act with audit: the agent applies the change; a human is notified; reversible.",
}

DEFAULT_LEVEL = Level.L0


def _level(value: Level | str) -> Level:
    if isinstance(value, Level):
        return value
    try:
        return Level(value)
    except ValueError:
        raise GuardrailsError(f"'{value}' is not a level; levels are {', '.join(lvl.value for lvl in Level)}") from None


# -- evidence, required only to change into L3 --------------------------------------


@dataclass(frozen=True)
class Evidence:
    acceptance_rate: float
    sample_size: int
    window: str  # a human-readable statement of what was measured, for example "trailing 90 days" or "last 40 decisions"

    def to_dict(self) -> dict:
        return {"acceptance_rate": round(self.acceptance_rate, 4), "sample_size": self.sample_size, "window": self.window}


def _check_l3_evidence(evidence: Evidence | None) -> None:
    if evidence is None:
        raise GuardrailsError(f"a change to L3 needs evidence (acceptance rate, sample size, window); none was given")
    if not evidence.window.strip():
        raise GuardrailsError("a change to L3 needs evidence.window stated (what period or sample it measures)")
    if evidence.sample_size < L3_MINIMUM_SAMPLE:
        raise GuardrailsError(f"a change to L3 needs at least {L3_MINIMUM_SAMPLE} decisions measured; evidence gives only {evidence.sample_size}")
    if evidence.acceptance_rate < L3_ACCEPTANCE_THRESHOLD:
        raise GuardrailsError(f"a change to L3 needs an acceptance rate >= {L3_ACCEPTANCE_THRESHOLD:.0%}; evidence gives {evidence.acceptance_rate:.0%}")


# -- the log: one change at a time, append-only --------------------------------------


@dataclass(frozen=True)
class LevelChange:
    agent: str
    task_class: str
    level: Level
    approver: str
    reason: str
    at: str
    evidence: Evidence | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "task_class": self.task_class,
            "level": self.level.value,
            "approver": self.approver,
            "reason": self.reason,
            "at": self.at,
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


def load_changes(path: Path | None) -> tuple[LevelChange, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    data = load(path.read_text(encoding="utf-8")) or {}
    changes = []
    for c in data.get("changes") or ():
        evidence = c.get("evidence")
        changes.append(
            LevelChange(
                agent=c["agent"],
                task_class=c["task_class"],
                level=_level(c["level"]),
                approver=c["approver"],
                reason=c["reason"],
                at=c["at"],
                evidence=Evidence(acceptance_rate=evidence["acceptance_rate"], sample_size=evidence["sample_size"], window=evidence["window"]) if evidence else None,
            )
        )
    return tuple(changes)


def record_change(
    path: Path,
    *,
    agent: str,
    task_class: str,
    level: Level | str,
    approver: str,
    reason: str,
    evidence: Evidence | None = None,
    at: datetime | None = None,
) -> tuple[LevelChange, ...]:
    """Validates, then appends one level change and rewrites the log. Raises GuardrailsError and
    writes nothing when the change does not meet the guardrail — a rejected promotion is never
    silently recorded and flagged for later; it simply does not happen."""
    if not agent.strip():
        raise GuardrailsError("agent must be given")
    if not task_class.strip():
        raise GuardrailsError("task_class must be given")
    level = _level(level)
    if not approver.strip():
        raise GuardrailsError("approver must be given")
    if not reason.strip():
        raise GuardrailsError("reason must be given")
    if level is Level.L3:
        _check_l3_evidence(evidence)

    existing = load_changes(path if Path(path).is_file() else None)
    when = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    change = LevelChange(agent=agent.strip(), task_class=task_class.strip(), level=level, approver=approver.strip(), reason=reason.strip(), at=when, evidence=evidence)
    updated = existing + (change,)

    lines = ["# Guardrails and autonomy: one level change per entry, oldest first. The current level of an", "# (agent, task_class) is its most recent entry here; none recorded at all means L0.", "changes_version: 0", "", "changes:"]
    for c in updated:
        lines.append(f"  - {{ agent: {c.agent}, task_class: {c.task_class}, level: {c.level.value}, approver: \"{c.approver}\", reason: \"{c.reason}\", at: \"{c.at}\"" + (f", evidence: {{ acceptance_rate: {c.evidence.acceptance_rate}, sample_size: {c.evidence.sample_size}, window: \"{c.evidence.window}\" }} }}" if c.evidence else " }"))
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- reading the current state -----------------------------------------------------


def current_change(changes: tuple[LevelChange, ...], agent: str, task_class: str) -> LevelChange | None:
    """The most recent change recorded for this (agent, task_class), or None when none has ever been recorded."""
    matching = [c for c in changes if c.agent == agent and c.task_class == task_class]
    return matching[-1] if matching else None


def current_level(changes: tuple[LevelChange, ...], agent: str, task_class: str) -> Level:
    change = current_change(changes, agent, task_class)
    return change.level if change else DEFAULT_LEVEL


def permits(changes: tuple[LevelChange, ...], agent: str, task_class: str, required: Level) -> bool:
    """Whether this (agent, task_class) is currently authorized to act at `required` or higher."""
    return current_level(changes, agent, task_class).rank >= _level(required).rank


def every_current_level(changes: tuple[LevelChange, ...]) -> dict[tuple[str, str], LevelChange]:
    """The latest change per (agent, task_class) seen in the log — every pair the log has ever mentioned, at its current level."""
    latest: dict[tuple[str, str], LevelChange] = {}
    for c in changes:
        latest[(c.agent, c.task_class)] = c
    return latest


# -- the report ----------------------------------------------------------------


def render_status(changes: tuple[LevelChange, ...], *, agent: str | None = None, task_class: str | None = None) -> str:
    latest = every_current_level(changes)
    rows = [c for (a, t), c in sorted(latest.items()) if (agent is None or a == agent) and (task_class is None or t == task_class)]
    out = ["# Guardrails: current autonomy level", ""]
    if not rows:
        out += ["No level change has ever been recorded" + (f" for {agent}/{task_class}" if agent or task_class else "") + "; every (agent, task_class) not listed here is L0 by default.", ""]
        return "\n".join(out)
    out.append("| Agent | Task class | Level | Meaning | Approver | Reason | At |")
    out.append("|---|---|---|---|---|---|---|")
    for c in rows:
        out.append(f"| {c.agent} | {c.task_class} | {c.level.value} | {c.level.meaning} | {c.approver} | {c.reason} | {c.at} |")
    out.append("")
    return "\n".join(out)
