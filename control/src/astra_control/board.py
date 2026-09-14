"""The factory board: every custodian shown at its station, one WIP limit enforced per stream
(S6.1.1, ADR 0054, product spec Section 3 and Section 7.1; backlog F6.1).

Five stations, the product spec's own onboarding flow (Section 7.1: "Ops drops a layout document
and sample files... Spec Reader and Profiler run... Modeler drafts the config... the sandbox
dry-runs samples... promotes to QA, then to a dual-run in production... Cutover") collapsed to
the board's own coarser view: `profile`, `draft`, `dry_run`, `dual_run`, `cutover` — a closed
vocabulary, the backlog's own station list (S6.1.1 AC1), not one this module invents or extends.
Every station short of cutover counts as work in flight; cutover is done — "custodians live per
week" (AC3) counts across that line, not inside the in-flight set.

A stream is the portfolio-level grouping a WIP limit actually gates — a client engagement
("envestnet-custodial", "blackrock-tableau", ...), one level up from family, the same level the
product spec's own release-order text works at ("the first family in each stream is chosen by
value", Section 7.1). Free text, not a closed vocabulary, the same reason a rejection code's
`owner` or a guardrails `task_class` is: an architect names streams as engagements arrive, this
module does not.

The WIP limit gates ENTRY into a stream's in-flight set, never a lateral move already inside it:
adding a custodian to the board (always at `profile`) or moving one back from `cutover` into an
in-flight station both increase how many of that stream are in flight, and are checked; moving a
custodian that is already in flight from one in-flight station to another does not change that
count, so it is never blocked by the limit — not even for a stream already over a since-lowered
limit. A WIP limit stops new work entering; it does not trap work already committed (ADR 0054).

No live Postgres yet: `board.yaml` is a real, working stand-in for the Postgres-backed store E6
will eventually have — the same relationship `astra_agents.guardrails`'s own changes log already
has to a database (ADR 0053). Unlike that log, the board's own history is not append-only: it is
full state, rewritten on every save, because a custodian's transitions already carry its own
history (ADR 0054) — there is nothing here for a second log to duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class BoardError(RuntimeError):
    pass


class Station(Enum):
    PROFILE = "profile"
    DRAFT = "draft"
    DRY_RUN = "dry_run"
    DUAL_RUN = "dual_run"
    CUTOVER = "cutover"


STATIONS: tuple[Station, ...] = tuple(Station)  # the flow's own order
IN_FLIGHT_STATIONS: tuple[Station, ...] = (Station.PROFILE, Station.DRAFT, Station.DRY_RUN, Station.DUAL_RUN)


def _station(value: Station | str) -> Station:
    if isinstance(value, Station):
        return value
    try:
        return Station(value)
    except ValueError:
        raise BoardError(f"'{value}' is not a station; stations are {', '.join(s.value for s in STATIONS)}") from None


def _now(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- the board ------------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    station: Station
    at: str  # ISO 8601 UTC, "%Y-%m-%dT%H:%M:%SZ"
    by: str | None = None  # who made this move; optional so a caller with no identity to record still works

    def to_dict(self) -> dict:
        return {"station": self.station.value, "at": self.at, "by": self.by}


@dataclass(frozen=True)
class CustodianCard:
    custodian_id: str
    stream: str
    transitions: tuple[Transition, ...]

    @property
    def station(self) -> Station:
        return self.transitions[-1].station

    @property
    def since(self) -> str:
        return self.transitions[-1].at

    @property
    def moved_by(self) -> str | None:
        return self.transitions[-1].by

    @property
    def in_flight(self) -> bool:
        return self.station in IN_FLIGHT_STATIONS

    def to_dict(self) -> dict:
        return {"id": self.custodian_id, "stream": self.stream, "station": self.station.value, "since": self.since, "transitions": [t.to_dict() for t in self.transitions]}


@dataclass(frozen=True)
class Board:
    cards: tuple[CustodianCard, ...] = ()
    wip_limits: dict[str, int] = field(default_factory=dict)

    def card(self, custodian_id: str) -> CustodianCard | None:
        return next((c for c in self.cards if c.custodian_id == custodian_id), None)

    def streams(self) -> tuple[str, ...]:
        return tuple(sorted({c.stream for c in self.cards} | set(self.wip_limits)))

    def wip_count(self, stream: str) -> int:
        return sum(1 for c in self.cards if c.stream == stream and c.in_flight)

    def wip_limit(self, stream: str) -> int | None:
        return self.wip_limits.get(stream)

    def over_limit_streams(self) -> tuple[str, ...]:
        return tuple(s for s in self.streams() if (limit := self.wip_limit(s)) is not None and self.wip_count(s) > limit)

    def to_dict(self) -> dict[str, Any]:
        return {"wip_limits": dict(sorted(self.wip_limits.items())), "custodians": [c.to_dict() for c in self.cards]}


# -- operations: every one either returns a new Board or raises BoardError, never both ----------


def add_custodian(board: Board, custodian_id: str, stream: str, *, by: str | None = None, at: datetime | None = None) -> Board:
    """Adds a custodian to the board at `profile` — the board's own entry point. Blocked exactly
    like a move into the in-flight set: the stream's WIP limit, if any, is checked first."""
    custodian_id, stream = custodian_id.strip(), stream.strip()
    if not custodian_id:
        raise BoardError("custodian_id must be given")
    if not stream:
        raise BoardError("stream must be given")
    if board.card(custodian_id) is not None:
        raise BoardError(f"'{custodian_id}' is already on the board")
    limit = board.wip_limit(stream)
    if limit is not None and board.wip_count(stream) >= limit:
        raise BoardError(f"adding '{custodian_id}' would put the {stream!r} stream at {board.wip_count(stream) + 1} in flight; the WIP limit is {limit}")
    card = CustodianCard(custodian_id=custodian_id, stream=stream, transitions=(Transition(Station.PROFILE, _now(at), by),))
    return replace(board, cards=board.cards + (card,))


def move(board: Board, custodian_id: str, to: Station | str, *, by: str | None = None, at: datetime | None = None) -> Board:
    """Moves a custodian to `to`. Blocked only when the move would newly add the custodian to its
    stream's in-flight set (it was not in flight before) and doing so would exceed the stream's
    WIP limit — a lateral move between two in-flight stations is never blocked (ADR 0054)."""
    to = _station(to)
    card = board.card(custodian_id)
    if card is None:
        raise BoardError(f"'{custodian_id}' is not on the board")
    entering_in_flight = to in IN_FLIGHT_STATIONS and not card.in_flight
    if entering_in_flight:
        limit = board.wip_limit(card.stream)
        if limit is not None and board.wip_count(card.stream) >= limit:
            raise BoardError(f"moving '{custodian_id}' to {to.value} would put the {card.stream!r} stream at {board.wip_count(card.stream) + 1} in flight; the WIP limit is {limit}")
    updated = replace(card, transitions=card.transitions + (Transition(to, _now(at), by),))
    cards = tuple(updated if c.custodian_id == custodian_id else c for c in board.cards)
    return replace(board, cards=cards)


def set_wip_limit(board: Board, stream: str, limit: int) -> Board:
    stream = stream.strip()
    if not stream:
        raise BoardError("stream must be given")
    if limit < 0:
        raise BoardError("limit must be zero or more")
    limits = dict(board.wip_limits)
    limits[stream] = limit
    return replace(board, wip_limits=limits)


def live_per_week(board: Board) -> dict[str, int]:
    """ISO week ('2026-W37') -> how many custodians most recently moved into `cutover` that week
    — counts go-lives by when they happened, not a snapshot of who happens to be live today."""
    counts: dict[str, int] = {}
    for c in board.cards:
        last_cutover = next((t for t in reversed(c.transitions) if t.station is Station.CUTOVER), None)
        if last_cutover is None:
            continue
        d = datetime.strptime(last_cutover.at, "%Y-%m-%dT%H:%M:%SZ").date()
        year, week, _ = d.isocalendar()
        counts[f"{year}-W{week:02d}"] = counts.get(f"{year}-W{week:02d}", 0) + 1
    return counts


# -- persistence: full state, rewritten each save (ADR 0054) -------------------------------------


def from_dict(data: dict[str, Any]) -> Board:
    cards = tuple(
        CustodianCard(
            custodian_id=c["id"],
            stream=c["stream"],
            transitions=tuple(Transition(_station(t["station"]), t["at"], t.get("by")) for t in c["transitions"]),
        )
        for c in data.get("custodians") or ()
    )
    return Board(cards=cards, wip_limits=dict(data.get("wip_limits") or {}))


def load_board(path: Path) -> Board:
    path = Path(path)
    if not path.is_file():
        return Board()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return from_dict(data)


def save_board(board: Board, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(board.to_dict(), sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# -- the view --------------------------------------------------------------------------


def render_markdown(board: Board) -> str:
    out = ["# Factory board", ""]
    if not board.cards and not board.wip_limits:
        out += ["No custodians on the board yet.", ""]
        return "\n".join(out)
    for stream in board.streams():
        limit = board.wip_limit(stream)
        count = board.wip_count(stream)
        flag = " **OVER LIMIT**" if limit is not None and count > limit else ""
        budget = f"{count}/{limit}" if limit is not None else f"{count} (no limit set)"
        out.append(f"## {stream} — {budget} in flight{flag}")
        out.append("")
        out.append("| Station | Custodian | Since | By |")
        out.append("|---|---|---|---|")
        stream_cards = sorted((c for c in board.cards if c.stream == stream), key=lambda c: (STATIONS.index(c.station), c.custodian_id))
        for c in stream_cards:
            out.append(f"| {c.station.value} | {c.custodian_id} | {c.since} | {c.moved_by or '—'} |")
        out.append("")
    out.append("## Custodians live per week")
    out.append("")
    weeks = live_per_week(board)
    if weeks:
        out.append("| Week | Live |")
        out.append("|---|---|")
        for week, count in sorted(weeks.items()):
            out.append(f"| {week} | {count} |")
    else:
        out.append("None yet.")
    out.append("")
    return "\n".join(out)
