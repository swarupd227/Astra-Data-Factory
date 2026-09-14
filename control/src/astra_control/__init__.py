"""Astra Data Factory control plane (E6).

Today: the factory board (S6.1.1) — every custodian at its station, one WIP limit enforced per
stream, custodians live per week; config studio (S6.1.2) — a guided profile/draft/dry-run
sequence with a promotion-request log, self-service for a simple-tier custodian with no engineer
involved; and diff review (S6.1.3) — a side-by-side diff of a config change, reusing
`astra_verification.replay.config_diff` directly, with citations and every other custodian a
touched rule also affects. No live Postgres yet: `board.yaml` and the promotion-requests log are
real, working stand-ins for the store E6 will eventually have (astra_control.board's own module
docstring).
"""

from astra_control.board import (
    STATIONS,
    IN_FLIGHT_STATIONS,
    Board,
    BoardError,
    CustodianCard,
    Station,
    Transition,
    add_custodian,
    live_per_week,
    load_board,
    move,
    render_markdown,
    save_board,
    set_wip_limit,
)
from astra_control.config_studio import (
    SEQUENCE,
    TIERS,
    ConfigStudioError,
    PromotionRequest,
    advance,
    load_promotion_requests,
    request_promotion,
    start,
)
from astra_control.diff_review import (
    DiffReview,
    DiffReviewError,
    RuleImpact,
    citation_link,
    review,
    write_review,
)
from astra_control.diff_review import render_markdown as render_diff_markdown

__all__ = [
    "STATIONS",
    "IN_FLIGHT_STATIONS",
    "SEQUENCE",
    "TIERS",
    "Board",
    "BoardError",
    "ConfigStudioError",
    "CustodianCard",
    "DiffReview",
    "DiffReviewError",
    "PromotionRequest",
    "RuleImpact",
    "Station",
    "Transition",
    "add_custodian",
    "advance",
    "citation_link",
    "live_per_week",
    "load_board",
    "load_promotion_requests",
    "move",
    "render_diff_markdown",
    "render_markdown",
    "request_promotion",
    "review",
    "save_board",
    "set_wip_limit",
    "start",
    "write_review",
]
