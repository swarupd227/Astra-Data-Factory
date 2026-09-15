"""Admin: autonomy levels and the self-healing whitelist — L0-L3 set per agent and task class, a
reason required and logged, L3 refused outright unless the measured acceptance rate already
clears the bar (S6.3.12, ADR 0068, product spec's own autonomy table, Section 8: "L3 · Act with
audit... Whitelisted exception classes, drift config deltas in non-prod, cost sizing").

`astra_agents.guardrails` (S5.13.1) already owns this exact governance: `Level`, `Evidence`,
`LevelChange`, `record_change` (which validates a reason is given and, for L3 specifically, that
`evidence.sample_size >= 20` and `evidence.acceptance_rate >= 0.8` before writing anything), and
`load_changes`. This module never imports it — the same "no new agent import" boundary this whole
plane has held since S6.3.2, extended here to a module that happens to be pure governance logic
with no model call in it at all: the boundary is about which package `control/` depends on, not
about which functions happen to be safe. `LEVELS`, `L3_MINIMUM_SAMPLE` and
`L3_ACCEPTANCE_THRESHOLD` are duplicated here as a small closed vocabulary, the same way
`astra_control.config_studio`'s own `TIERS` is duplicated from `astra_agents.pattern_matcher`
"rather than importing the whole Agents plane for one tuple... unlikely to drift on its own." This
module's own `set_level` reimplements `record_change`'s exact validation, in the same order, with
the same messages, and writes the exact same hand-rendered YAML shape — so the log this module
writes is the same file `astra_agents.guardrails.load_changes` already reads, and vice versa.

**The self-healing whitelist is not a separate artifact.** It is `RejectionCode.auto_resolve` on
each code of a domain pack's own rejection taxonomy (`domains/<pack>/rejections.yaml`,
`astra_knowledge.rejections`) — Exception Triage already reads this exact field (`whitelisted =
bool(rc and rc.auto_resolve)`) alongside guardrails' own L3 authorization, independently, ANDed
together at the CLI, never inside one function. This module reads it the same way, via
`astra_knowledge.rejections.load_taxonomy` directly (the Knowledge plane, already imported
throughout this plane — the "no new agent import" boundary is specific to `astra_agents`, not to
every other plane).

**This module never writes to `domains/*/rejections.yaml`.** The real file is large, hand-authored
prose with section-header comments and per-code narrative resolutions — not a small append-only
log a line-renderer could safely round-trip the way `rules.render_rule` or this module's own
`set_level` can. `astra_agents.exception_triage`'s own ADR (0048) already states the real
boundary directly: "Adding `auto_resolve: true` to a new code in the real taxonomy is a steward's
decision, not this agent's" — a human edits the file. "Manage the whitelist" here means requesting
a change, logged with who and why, the same "propose, log, a human applies it" shape
`astra_control.drift_review.approve` already established for "approve creates a Git change; never
touches prod" — this module's own request log never touches `domains/` either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astra_core.yamlsource import SourceError, load
from astra_knowledge.rejections import Taxonomy, load_taxonomy

LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_LEVEL = "L0"
L3_MINIMUM_SAMPLE = 20  # duplicated from astra_agents.guardrails -- module docstring
L3_ACCEPTANCE_THRESHOLD = 0.8


class AutonomyAdminError(RuntimeError):
    pass


def _now(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_yaml(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        return load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError):
        return {}


# -- AC2/AC3: set an autonomy level -- reason required, L3 needs evidence above threshold --------


@dataclass(frozen=True)
class Evidence:
    acceptance_rate: float
    sample_size: int
    window: str

    def to_dict(self) -> dict:
        return {"acceptance_rate": round(self.acceptance_rate, 4), "sample_size": self.sample_size, "window": self.window}


@dataclass(frozen=True)
class LevelChange:
    agent: str
    task_class: str
    level: str
    approver: str
    reason: str
    at: str
    evidence: Evidence | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "task_class": self.task_class,
            "level": self.level,
            "approver": self.approver,
            "reason": self.reason,
            "at": self.at,
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


def load_changes(path: Path | None) -> tuple[LevelChange, ...]:
    """The exact real astra_agents.guardrails.load_changes reading, duplicated (module docstring)
    -- this reads the identical file that module's own CLI writes and reads."""
    if path is None:
        return ()
    data = _read_yaml(path)
    changes = []
    for c in data.get("changes") or ():
        evidence = c.get("evidence")
        changes.append(
            LevelChange(
                agent=c["agent"],
                task_class=c["task_class"],
                level=c["level"],
                approver=c["approver"],
                reason=c["reason"],
                at=c["at"],
                evidence=Evidence(evidence["acceptance_rate"], evidence["sample_size"], evidence["window"]) if evidence else None,
            )
        )
    return tuple(changes)


def current_change(changes: tuple[LevelChange, ...], agent: str, task_class: str) -> LevelChange | None:
    matching = [c for c in changes if c.agent == agent and c.task_class == task_class]
    return matching[-1] if matching else None


def current_level(changes: tuple[LevelChange, ...], agent: str, task_class: str) -> str:
    """None ever recorded means L0 -- astra_agents.guardrails' own default, unchanged."""
    change = current_change(changes, agent, task_class)
    return change.level if change else DEFAULT_LEVEL


def every_current_level(changes: tuple[LevelChange, ...]) -> dict[tuple[str, str], LevelChange]:
    latest: dict[tuple[str, str], LevelChange] = {}
    for c in changes:
        latest[(c.agent, c.task_class)] = c
    return latest


def set_level(
    path: Path,
    *,
    agent: str,
    task_class: str,
    level: str,
    approver: str,
    reason: str,
    evidence: Evidence | None = None,
    at: datetime | None = None,
) -> tuple[LevelChange, ...]:
    """Validates, then appends one level change and rewrites the log -- refused outright, nothing
    written, when the change does not meet the guardrail (module docstring's own reimplementation
    of astra_agents.guardrails.record_change, in the same order, with the same messages)."""
    if not agent.strip():
        raise AutonomyAdminError("agent must be given")
    if not task_class.strip():
        raise AutonomyAdminError("task_class must be given")
    if level not in LEVELS:
        raise AutonomyAdminError(f"'{level}' is not a level; levels are {', '.join(LEVELS)}")
    if not approver.strip():
        raise AutonomyAdminError("approver must be given")
    if not reason.strip():
        raise AutonomyAdminError("reason must be given")
    if level == "L3":
        if evidence is None:
            raise AutonomyAdminError("a change to L3 needs evidence (acceptance rate, sample size, window); none was given")
        if not evidence.window.strip():
            raise AutonomyAdminError("a change to L3 needs evidence.window stated (what period or sample it measures)")
        if evidence.sample_size < L3_MINIMUM_SAMPLE:
            raise AutonomyAdminError(f"a change to L3 needs at least {L3_MINIMUM_SAMPLE} decisions measured; evidence gives only {evidence.sample_size}")
        if evidence.acceptance_rate < L3_ACCEPTANCE_THRESHOLD:
            raise AutonomyAdminError(f"a change to L3 needs an acceptance rate >= {L3_ACCEPTANCE_THRESHOLD:.0%}; evidence gives {evidence.acceptance_rate:.0%}")

    existing = load_changes(path if Path(path).is_file() else None)
    when = _now(at)
    change = LevelChange(agent.strip(), task_class.strip(), level, approver.strip(), reason.strip(), when, evidence)
    updated = existing + (change,)

    lines = [
        "# Guardrails and autonomy: one level change per entry, oldest first. The current level of an",
        "# (agent, task_class) is its most recent entry here; none recorded at all means L0.",
        "changes_version: 0",
        "",
        "changes:",
    ]
    for c in updated:
        lines.append(
            f"  - {{ agent: {c.agent}, task_class: {c.task_class}, level: {c.level}, "
            f'approver: "{c.approver}", reason: "{c.reason}", at: "{c.at}"'
            + (f', evidence: {{ acceptance_rate: {c.evidence.acceptance_rate}, sample_size: {c.evidence.sample_size}, window: "{c.evidence.window}" }} }}' if c.evidence else " }")
        )
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- the self-healing whitelist: read the real taxonomy, request a change, never write it --------


def load_whitelist(rejections_path: Path, root: Path | None = None) -> Taxonomy:
    rejections_path = Path(rejections_path)
    if not rejections_path.is_file():
        raise AutonomyAdminError(f"rejection taxonomy file not found: {rejections_path}")
    taxonomy, problems = load_taxonomy(rejections_path, root)
    if problems:
        raise AutonomyAdminError("; ".join(p.format() for p in problems))
    return taxonomy


def whitelisted_codes(taxonomy: Taxonomy) -> tuple[str, ...]:
    return tuple(c.code for c in taxonomy.codes if c.auto_resolve)


@dataclass(frozen=True)
class WhitelistRequest:
    code: str
    auto_resolve: bool  # the requested new value
    requested_by: str
    reason: str
    at: str

    def to_dict(self) -> dict:
        return {"code": self.code, "auto_resolve": self.auto_resolve, "requested_by": self.requested_by, "reason": self.reason, "at": self.at}


def load_whitelist_requests(path: Path | None) -> tuple[WhitelistRequest, ...]:
    if path is None:
        return ()
    data = _read_yaml(path)
    return tuple(
        WhitelistRequest(code=r["code"], auto_resolve=bool(r["auto_resolve"]), requested_by=r["requested_by"], reason=r["reason"], at=r["at"])
        for r in data.get("requests") or ()
    )


def request_whitelist_change(
    path: Path,
    *,
    code: str,
    auto_resolve: bool,
    requested_by: str,
    reason: str,
    taxonomy: Taxonomy | None = None,
    at: datetime | None = None,
) -> tuple[WhitelistRequest, ...]:
    """Logs a request to add or remove `code` from the self-healing whitelist -- never writes to
    domains/*/rejections.yaml itself (module docstring). `taxonomy`, when given, checks the code
    is real and that the request is not a no-op; without it, the request is still logged, since a
    steward reviewing the log is the one who ultimately checks it against the real taxonomy."""
    code = code.strip()
    if not code:
        raise AutonomyAdminError("code must be given")
    if not requested_by.strip():
        raise AutonomyAdminError("requested_by must be given")
    if not reason.strip():
        raise AutonomyAdminError("reason must be given")
    if taxonomy is not None:
        existing = taxonomy.code(code)
        if existing is None:
            raise AutonomyAdminError(f"'{code}' is not a code in {taxonomy.domain}'s own rejection taxonomy")
        if existing.auto_resolve == auto_resolve:
            raise AutonomyAdminError(f"'{code}' is already {'whitelisted' if auto_resolve else 'not whitelisted'}")

    existing_requests = load_whitelist_requests(path if Path(path).is_file() else None)
    when = _now(at)
    request = WhitelistRequest(code, auto_resolve, requested_by.strip(), reason.strip(), when)
    updated = existing_requests + (request,)

    lines = ["# Self-healing whitelist change requests: one per entry, oldest first. A steward edits", "# domains/<pack>/rejections.yaml directly to apply one (ADR 0048).", "requests_version: 0", "", "requests:"]
    for r in updated:
        lines.append(f'  - {{ code: {r.code}, auto_resolve: {"true" if r.auto_resolve else "false"}, requested_by: "{r.requested_by}", reason: "{r.reason}", at: "{r.at}" }}')
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- the view --------------------------------------------------------------------------


def render_levels_markdown(changes: tuple[LevelChange, ...]) -> str:
    out = ["# Autonomy levels", ""]
    latest = every_current_level(changes)
    if not latest:
        out += ["No level ever recorded; every (agent, task class) is at L0 by default.", ""]
        return "\n".join(out)
    out.append("| Agent | Task class | Level | Approver | Reason | At |")
    out.append("|---|---|---|---|---|---|")
    for (agent, task_class), c in sorted(latest.items()):
        out.append(f"| {agent} | {task_class} | **{c.level}** | {c.approver} | {c.reason} | {c.at} |")
    out.append("")
    return "\n".join(out)


def render_whitelist_markdown(taxonomy: Taxonomy) -> str:
    out = [f"# Self-healing whitelist: {taxonomy.domain}", ""]
    codes = whitelisted_codes(taxonomy)
    out.append(f"{len(codes)} of {len(taxonomy.codes)} code(s) whitelisted (`auto_resolve: true`).")
    out.append("")
    if codes:
        out.append("| Code | Resolution |")
        out.append("|---|---|")
        for code in codes:
            out.append(f"| {code} | {taxonomy.code(code).resolution} |")
        out.append("")
    return "\n".join(out)


def render_whitelist_requests_markdown(requests: tuple[WhitelistRequest, ...]) -> str:
    out = ["# Whitelist change requests", ""]
    if not requests:
        out += ["No requests recorded yet.", ""]
        return "\n".join(out)
    out.append("| Code | Requested | By | Reason | At |")
    out.append("|---|---|---|---|---|")
    for r in requests:
        out.append(f"| {r.code} | {'whitelist' if r.auto_resolve else 'un-whitelist'} | {r.requested_by} | {r.reason} | {r.at} |")
    out.append("")
    return "\n".join(out)
