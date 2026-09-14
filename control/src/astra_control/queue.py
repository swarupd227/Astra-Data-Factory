"""Home / my queue: what needs a person today, aggregated from what this factory's own agents
and gate packs have already produced — approvals, exceptions assigned, breaks to explain, drift
changes — filtered by role, so nobody hunts for work (S6.3.2, ADR 0058, product spec Section 3's
own top task named for nearly every persona).

Nothing here re-scores or re-derives anything — the same discipline `astra_agents.
gate_evidence_compiler` already established (ADR 0051): every item is read straight off a
`report.json` (or `gate_pack.json`) this factory's own tools already wrote. Never imported as
Python — a fourth cross-plane dependency this module does not need — read as the exact JSON each
tool's own CLI already writes to disk and a person could inspect directly.

Four sources, one per queue item kind:

  approval    a gate pack (S5.11.1) whose own `approval` criterion is NOT MET while every other
              criterion IS — everything else about this release is ready; a person's decision is
              the only thing left. A gate pack with any other criterion still unmet is not queued
              here at all — that is a different problem, for whoever owns the missing evidence,
              not an approval waiting on a person.
  exception   an Exception Triage draft (S5.8.1) suggestion not already auto-applying —
              whitelisted-and-confident exceptions already acted on need nobody; everything else,
              including a code with no taxonomy entry at all, still does.
  break       a Break Explainer draft (S5.10.1) explanation whose own `explained` is false —
              literally its own "needs a person" category (ADR 0050), nothing broader.
  drift       every Drift Watcher draft (S5.9.1) finding — drift is drift; that report carries no
              "already handled" signal to filter on, so every finding is a queue item until a
              person clears it downstream.

"Filtered by my role" reuses `astra_control.permissions.Role` directly (S6.3.1) — the same six
roles, no new vocabulary. Which roles see which kind is traced to the story that built the
underlying tool and `docs/ux/personas.md`'s own grounded tasks, the same way S6.3.1 traced each
action to its own story rather than inventing a mapping: approvals to steward and PM (the gate
pack's own persona table entries — "signs gates," "records approval"); exceptions to ops and BSA
(both do onboarding and exception triage per personas.md); breaks to steward and ops (both
investigate a parity difference per personas.md); drift to engineer and steward (the product
spec's own later S6.3.9 story names both as this review's actors).

**"And assignments" is not yet real.** None of the four report shapes this module reads carries
an assignee — a specific person, not just a role. This module filters by role only and says so
here rather than inventing a field with no source; per-person assignment is later work, once
something in this factory actually records one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from astra_control.permissions import Role


class QueueItemKind(Enum):
    APPROVAL = "approval"
    EXCEPTION = "exception"
    BREAK = "break"
    DRIFT = "drift"


# Which roles this kind is relevant for — traced to the story that built the underlying tool and
# docs/ux/personas.md's own grounded tasks (module docstring), not invented for symmetry.
KIND_ROLES: dict[QueueItemKind, frozenset[Role]] = {
    QueueItemKind.APPROVAL: frozenset({Role.STEWARD, Role.PM}),
    QueueItemKind.EXCEPTION: frozenset({Role.OPS, Role.BSA}),
    QueueItemKind.BREAK: frozenset({Role.STEWARD, Role.OPS}),
    QueueItemKind.DRIFT: frozenset({Role.ENGINEER, Role.STEWARD}),
}


def _read_json(path: Path) -> dict | None:
    """Never raises — a missing or unreadable report means nothing to queue from it, the same
    honest 'no evidence' shape astra_agents.gate_evidence_compiler already established."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class QueueItem:
    kind: QueueItemKind
    title: str
    detail: str
    source: str  # the report this came from, so a person can trace it back
    roles: frozenset[Role]

    def to_dict(self) -> dict:
        return {"kind": self.kind.value, "title": self.title, "detail": self.detail, "source": self.source, "roles": sorted(r.value for r in self.roles)}


# -- one function per source, each reading exactly the real report shape it names ---------------


def approvals_from(path: Path) -> tuple[QueueItem, ...]:
    data = _read_json(path)
    if data is None:
        return ()
    criteria = {c["id"]: c for c in data.get("criteria") or []}
    approval = criteria.get("approval")
    if approval is None or approval.get("met"):
        return ()
    others = [c for cid, c in criteria.items() if cid != "approval"]
    if not others or not all(c.get("met") for c in others):
        return ()
    release = data.get("release", "?")
    return (
        QueueItem(
            kind=QueueItemKind.APPROVAL,
            title=f"Approve release {release}",
            detail="Every other gate criterion is met; only approval is outstanding.",
            source=str(path),
            roles=KIND_ROLES[QueueItemKind.APPROVAL],
        ),
    )


def exceptions_from(path: Path) -> tuple[QueueItem, ...]:
    data = _read_json(path)
    if data is None:
        return ()
    items = []
    for s in data.get("suggestions") or []:
        if s.get("auto_apply"):
            continue
        code = s.get("rejection_code", "?")
        count = s.get("count", 0)
        detail = s.get("resolution") or f"'{code}' has no taxonomy entry — needs a person, not a suggestion."
        items.append(
            QueueItem(
                kind=QueueItemKind.EXCEPTION,
                title=f"{count} exception(s): {code}",
                detail=detail,
                source=str(path),
                roles=KIND_ROLES[QueueItemKind.EXCEPTION],
            )
        )
    return tuple(items)


def breaks_from(path: Path) -> tuple[QueueItem, ...]:
    data = _read_json(path)
    if data is None:
        return ()
    items = []
    for e in data.get("explanations") or []:
        if e.get("explained"):
            continue
        key = e.get("key")
        field = e.get("field", "?")
        title = f"Unexplained: {field} for {key}" if key else f"Unexplained: {field}"
        items.append(
            QueueItem(
                kind=QueueItemKind.BREAK,
                title=title,
                detail=e.get("description") or "",
                source=str(path),
                roles=KIND_ROLES[QueueItemKind.BREAK],
            )
        )
    return tuple(items)


def drift_from(path: Path) -> tuple[QueueItem, ...]:
    data = _read_json(path)
    if data is None:
        return ()
    items = []
    for f in data.get("findings") or []:
        items.append(
            QueueItem(
                kind=QueueItemKind.DRIFT,
                title=f"Drift: {f.get('kind', '?')} at {f.get('path', '?')}",
                detail=f.get("description") or "",
                source=str(path),
                roles=KIND_ROLES[QueueItemKind.DRIFT],
            )
        )
    return tuple(items)


# -- aggregating and filtering --------------------------------------------------------------


@dataclass(frozen=True)
class QueueSources:
    gate_packs: tuple[Path, ...] = ()
    exception_triage_reports: tuple[Path, ...] = ()
    break_explainer_reports: tuple[Path, ...] = ()
    drift_watcher_reports: tuple[Path, ...] = ()


def build_queue(sources: QueueSources) -> tuple[QueueItem, ...]:
    items: list[QueueItem] = []
    for p in sources.gate_packs:
        items.extend(approvals_from(p))
    for p in sources.exception_triage_reports:
        items.extend(exceptions_from(p))
    for p in sources.break_explainer_reports:
        items.extend(breaks_from(p))
    for p in sources.drift_watcher_reports:
        items.extend(drift_from(p))
    return tuple(items)


def for_role(items: tuple[QueueItem, ...], role: Role | str) -> tuple[QueueItem, ...]:
    role = role if isinstance(role, Role) else Role(role)
    return tuple(i for i in items if role in i.roles)


def counts_by_kind(items: tuple[QueueItem, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in items:
        counts[i.kind.value] = counts.get(i.kind.value, 0) + 1
    return counts


# -- the view --------------------------------------------------------------------------


def render_markdown(items: tuple[QueueItem, ...], *, role: Role | None = None) -> str:
    heading = f"# My queue — {role.value}" if role else "# My queue"
    out = [heading, ""]
    if not items:
        out += ["Nothing needs you today.", ""]
        return "\n".join(out)
    counts = counts_by_kind(items)
    out.append(", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())))
    out.append("")
    out.append("| Kind | Title | Detail | Source |")
    out.append("|---|---|---|---|")
    for i in items:
        out.append(f"| {i.kind.value} | {i.title} | {i.detail} | {i.source} |")
    out.append("")
    return "\n".join(out)
