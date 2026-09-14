"""Config studio: profile a sample, review the drafted config, dry-run it and request
promotion — self-service for a simple-tier custodian, with no engineer involved (S6.1.2,
ADR 0055, product spec Section 3: "profile, review draft, dry-run, request promotion"; Section
7.1: "Steward reviews (simple tier: BSA reviews). Approval promotes to QA...").

Nothing here re-runs an agent. Profiling a sample is `astra_agents.profiler.run`; drafting a
config is `astra_agents.modeler.run` (a live model call); a dry run is
`astra_verification.dryrun.dry_run` (a live Snowflake sandbox) — none of the three can run
honestly inside this module, the same reason `astra_agents.gate_evidence_compiler` never runs a
verification tool itself, only reads what one already produced.

What this module owns is the sequence and its own audit trail. `start` and `advance` drive a
custodian through the factory board's own `profile -> draft -> dry_run` stations one at a time,
in order — stricter than `astra_control.board.move`, which allows any station to any station
(ADR 0054 point 6) because a person with judgement might need to send work backward; a guided
self-service flow should not let a BSA skip a review step forward instead. Every call records who
made it: `astra_control.board.Transition` now carries an optional `by`, so the board's own
history — not a second, parallel log — is the "who and when" record for these three steps.

`request_promotion` is the one new event this module adds: a small, append-only log — the same
"who, when, a free-text note" shape `astra_agents.gate_evidence_compiler`'s `Approval` and
`astra_agents.guardrails`'s `LevelChange` already use — recording that a custodian at `dry_run` is
ready to move toward QA and a dual-run. Promotion itself (the move to `dual_run`) is a separate,
later approval this module does not grant.

The one guardrail this story is named for: a **simple**-tier request needs only its own requester
— self-service, no engineer. A medium- or complex-tier request needs a distinct reviewer recorded
too, the product spec's own text for exactly this case. `TIERS` is duplicated here from
`astra_agents.pattern_matcher`'s own closed vocabulary (ADR 0043) rather than importing the whole
Agents plane for one tuple of three strings unlikely to drift on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astra_core.yamlsource import load

from astra_control.board import Board, BoardError, Station, add_custodian, move

TIERS = ("simple", "medium", "complex")
SEQUENCE = (Station.PROFILE, Station.DRAFT, Station.DRY_RUN)  # config studio's own guided flow; promotion beyond dry_run is requested, not moved directly here


class ConfigStudioError(RuntimeError):
    pass


def _now(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _station(value: str) -> Station:
    try:
        return Station(value)
    except ValueError:
        raise ConfigStudioError(f"'{value}' is not a station; config studio's own sequence is {', '.join(s.value for s in SEQUENCE)}") from None


# -- the guided sequence: profile -> draft -> dry_run, one step at a time -----------------------


def start(board: Board, custodian_id: str, stream: str, *, by: str, at: datetime | None = None) -> Board:
    """A sample arrives: the custodian enters the board at `profile`, the sequence's own start."""
    try:
        return add_custodian(board, custodian_id, stream, by=by, at=at)
    except BoardError as exc:
        raise ConfigStudioError(str(exc)) from exc


def advance(board: Board, custodian_id: str, to: Station | str, *, by: str, at: datetime | None = None) -> Board:
    """Moves a custodian exactly one step forward through `profile -> draft -> dry_run`. Never
    sideways, never more than one station at a time, never past `dry_run` — promotion beyond it
    is requested with `request_promotion`, not granted by moving the card directly."""
    to_station = to if isinstance(to, Station) else _station(to)
    if to_station not in SEQUENCE:
        raise ConfigStudioError(f"config studio only advances a custodian through {', '.join(s.value for s in SEQUENCE)}; promotion beyond dry_run is requested with request_promotion, not moved directly")
    card = board.card(custodian_id)
    if card is None:
        raise ConfigStudioError(f"'{custodian_id}' is not on the board yet; call start() first")
    current_index = SEQUENCE.index(card.station) if card.station in SEQUENCE else -1
    target_index = SEQUENCE.index(to_station)
    if target_index != current_index + 1:
        raise ConfigStudioError(f"'{custodian_id}' is at {card.station.value}; config studio advances one station at a time, in order ({' -> '.join(s.value for s in SEQUENCE)})")
    try:
        return move(board, custodian_id, to_station, by=by, at=at)
    except BoardError as exc:
        raise ConfigStudioError(str(exc)) from exc


# -- requesting promotion: a small, append-only log ----------------------------------------------


@dataclass(frozen=True)
class PromotionRequest:
    custodian_id: str
    tier: str
    requested_by: str
    reviewed_by: str | None
    note: str | None
    at: str

    @property
    def self_service(self) -> bool:
        """True when this request needed no engineer — a simple-tier request, requester only."""
        return self.tier == "simple"

    def to_dict(self) -> dict:
        return {"custodian_id": self.custodian_id, "tier": self.tier, "requested_by": self.requested_by, "reviewed_by": self.reviewed_by, "note": self.note, "at": self.at, "self_service": self.self_service}


def load_promotion_requests(path: Path | None) -> tuple[PromotionRequest, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    data = load(path.read_text(encoding="utf-8")) or {}
    return tuple(
        PromotionRequest(custodian_id=r["custodian_id"], tier=r["tier"], requested_by=r["requested_by"], reviewed_by=r.get("reviewed_by"), note=r.get("note"), at=r["at"])
        for r in data.get("requests") or ()
    )


def _yaml_str(value: str) -> str:
    return json.dumps(value)  # a valid double-quoted YAML flow scalar too; safely escapes internal quotes and colons, unlike a bare f-string wrap


def _yaml_field(value: str | None) -> str:
    return _yaml_str(value) if value is not None else "null"


def request_promotion(
    board: Board,
    path: Path,
    custodian_id: str,
    *,
    tier: str,
    requested_by: str,
    reviewed_by: str | None = None,
    note: str | None = None,
    at: datetime | None = None,
) -> tuple[PromotionRequest, ...]:
    """Requires the custodian to have reached `dry_run` — the sequence's own last step before a
    promotion makes sense. A simple-tier request needs only `requested_by`; medium or complex
    needs `reviewed_by` too, or the request is refused outright and nothing is written."""
    card = board.card(custodian_id)
    if card is None:
        raise ConfigStudioError(f"'{custodian_id}' is not on the board")
    if card.station is not Station.DRY_RUN:
        raise ConfigStudioError(f"'{custodian_id}' is at {card.station.value}; promotion can only be requested once it has reached dry_run")
    if tier not in TIERS:
        raise ConfigStudioError(f"'{tier}' is not a tier; tiers are {', '.join(TIERS)}")
    requested_by = requested_by.strip()
    if not requested_by:
        raise ConfigStudioError("requested_by must be given")
    reviewed_by = reviewed_by.strip() if reviewed_by and reviewed_by.strip() else None
    if tier != "simple" and reviewed_by is None:
        raise ConfigStudioError(f"{tier} tier needs a steward's review (reviewed_by) before promotion can be requested; only simple tier is self-service, with no engineer involved")

    existing = load_promotion_requests(path if Path(path).is_file() else None)
    when = _now(at)
    note = note.strip() if note and note.strip() else None
    request = PromotionRequest(custodian_id=custodian_id, tier=tier, requested_by=requested_by, reviewed_by=reviewed_by, note=note, at=when)
    updated = existing + (request,)

    lines = ["# Config studio: one promotion request per entry, oldest first.", "requests_version: 0", "", "requests:"]
    for r in updated:
        lines.append(
            "  - { custodian_id: "
            + r.custodian_id
            + ", tier: "
            + r.tier
            + ", requested_by: "
            + _yaml_str(r.requested_by)
            + ", reviewed_by: "
            + _yaml_field(r.reviewed_by)
            + ", note: "
            + _yaml_field(r.note)
            + ", at: "
            + _yaml_str(r.at)
            + " }"
        )
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated
