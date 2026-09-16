"""Ops exception UI: Exception Triage's own real suggestion groups (`report.json`, S5.8.1) moved
through new -> suggested -> approved -> resubmitted -> closed by a reconciliation operator
(`docs/ux/personas.md`'s own "Ops reconciler" renaming of the product spec's "ops / business
analyst," `Role.OPS` here), plus an ageing report by code from the same real per-exception
`raised_at` timestamps Exception Triage itself reads (S6.2.5, ADR 0074, closing feature F6.2).

Reads `report.json` directly — the identical "read the real shape, never import astra_agents"
convention `astra_control.queue.exceptions_from` already established for this exact file — and
tracks each suggestion group (`astra_agents.exception_triage`'s own `Suggestion` identity: a
rejection code plus its root-cause `group_key`) through its own workflow status on a full-state
card with its own history embedded, the same shape `astra_control.board.CustodianCard` already
established, rather than a second append-only log duplicating it.

Deliberately scoped at the same suggestion-group granularity `astra_control.queue` and Exception
Triage's own report already use, never per-exception-id: a real per-exception store with state
transitions and owners is its own later story (the backlog's own S7.1.4 "Exception store and
state machine," F7.1, scheduled after this one) — building a second one here would preempt it, not
extend it. "new" and "suggested" map onto whether Exception Triage's own report proposed a real
resolution for a code (a taxonomy entry exists) or not (`unresolved_codes`) — a real,
already-computed distinction, never an invented vocabulary; this is a workflow status for this
screen alone and is never confused with the CDM's own, differently-shaped `Exception.STATUS`
column (`NEW`/`RESOLVED`/`AUTO_RESOLVED`/`DISMISSED`), which is per-record and unrelated. A
whitelisted, already-`auto_apply` group needs no operator at all and is never tracked here — the
same exclusion `astra_control.queue.exceptions_from` already established.

"Resubmission re-runs the record through resolution" (the story's own AC): confirmed exhaustively
that no callable or composable re-run mechanism exists anywhere in this codebase for a single
record — `astra_data.render.resolve`'s own `RESOLVE` procedure is deployed, whole-run, set-based
SQL invoked by live pipeline orchestration, structurally unlike `astra_control.golden_viewer`'s
own composable capture command. `resubmit()` records the transition honestly — who, when, and
that no live re-run happened in this environment — rather than fabricating one.

Ageing reads the real `exceptions.csv` Exception Triage itself reads (the CDM `Exception`
entity's own columns, including `raised_at`) directly, for the same reason: it is the only place
a real per-exception timestamp exists anywhere in this codebase; Exception Triage's own
`report.json` does not carry `raised_at` forward into a `Suggestion`.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

STATUSES = ("new", "suggested", "approved", "resubmitted", "closed")


class ExceptionReviewError(RuntimeError):
    pass


def _read_json(path: Path) -> dict | None:
    """Never raises — a missing or unreadable triage report means nothing to sync, the same
    shape astra_control.queue._read_json already established for this exact file."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _at(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- Exception Triage's own suggestion groups, read raw ------------------------------------------


@dataclass(frozen=True)
class ExceptionGroup:
    rejection_code: str
    group_key: str
    count: int
    sample_raw_value: str | None
    resolution: str | None
    confidence: float
    whitelisted: bool
    auto_apply: bool
    exception_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.rejection_code}:{self.group_key}"

    def to_dict(self) -> dict:
        return {
            "rejection_code": self.rejection_code,
            "group_key": self.group_key,
            "count": self.count,
            "sample_raw_value": self.sample_raw_value,
            "resolution": self.resolution,
            "confidence": self.confidence,
            "whitelisted": self.whitelisted,
            "auto_apply": self.auto_apply,
            "exception_ids": list(self.exception_ids),
        }


def groups_from(path: Path) -> tuple[ExceptionGroup, ...]:
    """Every real suggestion in Exception Triage's own report.json that is not already
    auto-applying — the same exclusion astra_control.queue.exceptions_from already established;
    a whitelisted, self-healing group needs no operator at all."""
    data = _read_json(path)
    if data is None:
        return ()
    groups = []
    for s in data.get("suggestions") or []:
        if s.get("auto_apply"):
            continue
        groups.append(
            ExceptionGroup(
                rejection_code=s.get("rejection_code", "?"),
                group_key=s.get("group_key", "?"),
                count=s.get("count", 0),
                sample_raw_value=s.get("sample_raw_value"),
                resolution=s.get("resolution"),
                confidence=s.get("confidence", 0.0),
                whitelisted=bool(s.get("whitelisted")),
                auto_apply=bool(s.get("auto_apply")),
                exception_ids=tuple(s.get("exception_ids") or ()),
            )
        )
    return tuple(groups)


# -- the review board: one full-state card per group, its own history embedded -------------------


@dataclass(frozen=True)
class ReviewTransition:
    status: str
    by: str
    at: str
    note: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "by": self.by, "at": self.at, "note": self.note}


@dataclass(frozen=True)
class ReviewCard:
    rejection_code: str
    group_key: str
    status: str
    count: int
    sample_raw_value: str | None
    original_resolution: str | None
    resolution: str | None
    transitions: tuple[ReviewTransition, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.rejection_code}:{self.group_key}"

    @property
    def edited(self) -> bool:
        return self.resolution != self.original_resolution

    def to_dict(self) -> dict:
        return {
            "rejection_code": self.rejection_code,
            "group_key": self.group_key,
            "status": self.status,
            "count": self.count,
            "sample_raw_value": self.sample_raw_value,
            "original_resolution": self.original_resolution,
            "resolution": self.resolution,
            "edited": self.edited,
            "transitions": [t.to_dict() for t in self.transitions],
        }


@dataclass(frozen=True)
class ReviewBoard:
    cards: tuple[ReviewCard, ...] = ()

    def card(self, rejection_code: str, group_key: str) -> ReviewCard | None:
        cid = f"{rejection_code}:{group_key}"
        return next((c for c in self.cards if c.id == cid), None)

    def to_dict(self) -> dict:
        return {"cards": [c.to_dict() for c in sorted(self.cards, key=lambda c: c.id)]}


def _card_from_dict(data: dict) -> ReviewCard:
    return ReviewCard(
        rejection_code=data["rejection_code"],
        group_key=data["group_key"],
        status=data["status"],
        count=data.get("count", 0),
        sample_raw_value=data.get("sample_raw_value"),
        original_resolution=data.get("original_resolution"),
        resolution=data.get("resolution"),
        transitions=tuple(ReviewTransition(t["status"], t["by"], t["at"], t.get("note")) for t in data.get("transitions") or ()),
    )


def load_review_board(path: Path) -> ReviewBoard:
    path = Path(path)
    if not path.is_file():
        return ReviewBoard()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ReviewBoard(cards=tuple(_card_from_dict(c) for c in data.get("cards") or ()))


def save_review_board(board: ReviewBoard, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(board.to_dict(), sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def sync(board: ReviewBoard, groups: tuple[ExceptionGroup, ...]) -> ReviewBoard:
    """Adds a card for every real group not yet tracked, at "new" (no resolution yet — needs a
    taxonomy entry first) or "suggested" (a real resolution already proposed); a card already
    tracked is never touched, so an operator's own work in progress is never overwritten by a
    later Exception Triage run finding the same group again."""
    existing_ids = {c.id for c in board.cards}
    new_cards = tuple(
        ReviewCard(
            rejection_code=g.rejection_code,
            group_key=g.group_key,
            status="new" if g.resolution is None else "suggested",
            count=g.count,
            sample_raw_value=g.sample_raw_value,
            original_resolution=g.resolution,
            resolution=g.resolution,
        )
        for g in groups
        if g.id not in existing_ids
    )
    return replace(board, cards=board.cards + new_cards)


def _require_card(board: ReviewBoard, rejection_code: str, group_key: str) -> ReviewCard:
    card = board.card(rejection_code, group_key)
    if card is None:
        raise ExceptionReviewError(f"'{rejection_code}:{group_key}' is not tracked; sync() a real Exception Triage report first")
    return card


def _with_card(board: ReviewBoard, card: ReviewCard) -> ReviewBoard:
    others = tuple(c for c in board.cards if c.id != card.id)
    return replace(board, cards=others + (card,))


def _require_by(by: str) -> str:
    if not by.strip():
        raise ExceptionReviewError("who made this change must be given (--by)")
    return by.strip()


def accept(board: ReviewBoard, rejection_code: str, group_key: str, *, by: str, at: datetime | None = None) -> ReviewBoard:
    card = _require_card(board, rejection_code, group_key)
    by = _require_by(by)
    if card.status != "suggested":
        detail = " — it has no taxonomy resolution yet" if card.status == "new" else ""
        raise ExceptionReviewError(f"'{card.id}' is {card.status}; only a suggested exception group can be accepted{detail}")
    new_card = replace(card, status="approved", transitions=card.transitions + (ReviewTransition("approved", by, _at(at)),))
    return _with_card(board, new_card)


def edit(board: ReviewBoard, rejection_code: str, group_key: str, resolution_text: str, *, by: str, at: datetime | None = None) -> ReviewBoard:
    """A corrected resolution, kept alongside the original Exception Triage suggested — `edited`
    (ReviewCard's own property) and the two texts stay both available for comparison, the same
    "keep the original for comparison" shape astra_control.agent_review's own Edited/FieldChange
    already established (a lighter-weight version here: one editable field, not several)."""
    card = _require_card(board, rejection_code, group_key)
    by = _require_by(by)
    if card.status != "approved":
        raise ExceptionReviewError(f"'{card.id}' is {card.status}; only an approved exception group's resolution can be edited")
    if not resolution_text.strip():
        raise ExceptionReviewError("resolution text must not be blank")
    new_card = replace(card, resolution=resolution_text.strip(), transitions=card.transitions + (ReviewTransition("approved", by, _at(at), note="resolution edited"),))
    return _with_card(board, new_card)


_NO_LIVE_RERUN = (
    "no live re-run: this environment has no callable single-record resolution step "
    "(astra_data.render.resolve's own RESOLVE procedure is whole-run, set-based SQL invoked by "
    "live pipeline orchestration) — tracked for the batch's own next real resolution run"
)


def resubmit(board: ReviewBoard, rejection_code: str, group_key: str, *, by: str, at: datetime | None = None) -> ReviewBoard:
    card = _require_card(board, rejection_code, group_key)
    by = _require_by(by)
    if card.status != "approved":
        raise ExceptionReviewError(f"'{card.id}' is {card.status}; only an approved exception group can be resubmitted")
    new_card = replace(card, status="resubmitted", transitions=card.transitions + (ReviewTransition("resubmitted", by, _at(at), note=_NO_LIVE_RERUN),))
    return _with_card(board, new_card)


def close(board: ReviewBoard, rejection_code: str, group_key: str, *, by: str, at: datetime | None = None) -> ReviewBoard:
    card = _require_card(board, rejection_code, group_key)
    by = _require_by(by)
    if card.status != "resubmitted":
        raise ExceptionReviewError(f"'{card.id}' is {card.status}; only a resubmitted exception group can be closed")
    new_card = replace(card, status="closed", transitions=card.transitions + (ReviewTransition("closed", by, _at(at)),))
    return _with_card(board, new_card)


def render_markdown(board: ReviewBoard) -> str:
    lines = ["# Exceptions, by cause", ""]
    by_code: dict[str, list[ReviewCard]] = {}
    for c in board.cards:
        by_code.setdefault(c.rejection_code, []).append(c)
    if not by_code:
        lines.append("No exception groups tracked yet.")
        return "\n".join(lines) + "\n"
    for code in sorted(by_code):
        cards = sorted(by_code[code], key=lambda c: c.group_key)
        lines.append(f"## {code}")
        lines.append("")
        lines.append("| Group | Count | Status | Sample value | Resolution |")
        lines.append("|---|---|---|---|---|")
        for c in cards:
            resolution = c.resolution or "*(no taxonomy entry — needs a person)*"
            edited = " *(edited)*" if c.edited else ""
            lines.append(f"| {c.group_key} | {c.count} | {c.status} | {c.sample_raw_value or '-'} | {resolution}{edited} |")
        lines.append("")
    return "\n".join(lines) + "\n"


# -- ageing report: real per-exception raised_at, from the same CSV Exception Triage reads -------


@dataclass(frozen=True)
class AgeingByCode:
    rejection_code: str
    open_count: int
    oldest_raised_at: str
    age_days: int

    def to_dict(self) -> dict:
        return {"rejection_code": self.rejection_code, "open_count": self.open_count, "oldest_raised_at": self.oldest_raised_at, "age_days": self.age_days}


def _parse_raised_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ageing_report(path: Path, *, clock=lambda: datetime.now(timezone.utc)) -> tuple[AgeingByCode, ...]:
    """Every still-open (status NEW) exception's own real raised_at, grouped by code — the only
    place a real per-exception timestamp exists anywhere in this codebase (module docstring);
    Exception Triage's own report.json does not carry it forward. Never raises — a missing or
    unreadable exceptions file means nothing to report, not an error."""
    path = Path(path)
    if not path.is_file():
        return ()
    now = clock()
    by_code: dict[str, list[datetime]] = {}
    try:
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
                if (row.get("status") or "NEW").upper() != "NEW":
                    continue
                code, raised_at = row.get("rejection_code"), row.get("raised_at")
                if not code or not raised_at:
                    continue
                try:
                    by_code.setdefault(code, []).append(_parse_raised_at(raised_at))
                except ValueError:
                    continue
    except (OSError, UnicodeDecodeError, csv.Error):
        return ()
    reports = []
    for code, timestamps in by_code.items():
        oldest = min(timestamps)
        reports.append(AgeingByCode(rejection_code=code, open_count=len(timestamps), oldest_raised_at=oldest.strftime("%Y-%m-%dT%H:%M:%SZ"), age_days=(now - oldest).days))
    reports.sort(key=lambda r: (-r.age_days, r.rejection_code))
    return tuple(reports)


def render_ageing_markdown(report: tuple[AgeingByCode, ...]) -> str:
    lines = ["# Exception ageing, by code", ""]
    if not report:
        lines.append("No open exceptions.")
        return "\n".join(lines) + "\n"
    lines += ["| Code | Open | Oldest raised | Age (days) |", "|---|---|---|---|"]
    for r in report:
        lines.append(f"| {r.rejection_code} | {r.open_count} | {r.oldest_raised_at} | {r.age_days} |")
    lines.append("")
    return "\n".join(lines) + "\n"
