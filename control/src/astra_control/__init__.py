"""Astra Data Factory control plane (E6).

Today: the factory board (S6.1.1) — every custodian at its station, one WIP limit enforced per
stream, custodians live per week; config studio (S6.1.2) — a guided profile/draft/dry-run
sequence with a promotion-request log, self-service for a simple-tier custodian with no engineer
involved; diff review (S6.1.3) — a side-by-side diff of a config change, reusing
`astra_verification.replay.config_diff` directly, with citations and every other custodian a
touched rule also affects; and sign-in, roles and permissions (S6.3.1) — six closed roles, each
one's allowed actions across the three modules above listed and enforced at the CLI boundary, an
identity provider's already-authenticated claims turned into an internal role through a platform
administrator's own group mapping, and an auditor role with no write action at all; and home / my
queue (S6.3.2) — approvals, exceptions assigned, breaks to explain and drift changes, aggregated
straight from the report.json and gate_pack.json files this factory's own agents already wrote,
filtered by the same six roles; and the custodian page (S6.3.3) — family, tier, config version,
current station, files today against a real cutoff, parity trend, open exceptions and cost, all
in one place, each field read from something this repository already built (the board, a real
parity report, `astra_control.queue`'s own exception count) and never fabricated when its own
source is missing. No live Postgres yet: `board.yaml` and the promotion-requests log are real,
working stand-ins for the store E6 will eventually have (astra_control.board's own module
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
from astra_control.permissions import (
    PERMISSIONS,
    READ_ACTIONS,
    WRITE_ACTIONS,
    Action,
    AuthorizationError,
    Identity,
    Role,
    RoleMapping,
    authorized,
    identity_from_claims,
    load_role_mapping,
    require,
)
from astra_control.permissions import render_permissions as render_permissions_markdown
from astra_control.queue import (
    KIND_ROLES,
    QueueItem,
    QueueItemKind,
    QueueSources,
    approvals_from,
    breaks_from,
    build_queue,
    counts_by_kind,
    drift_from,
    exceptions_from,
    for_role,
)
from astra_control.queue import render_markdown as render_queue_markdown
from astra_control.custodian_page import (
    CustodianPage,
    CustodianPageError,
    ExpectedFile,
    load_arrivals,
)
from astra_control.custodian_page import build as build_custodian_page
from astra_control.custodian_page import render_markdown as render_custodian_page_markdown

__all__ = [
    "KIND_ROLES",
    "PERMISSIONS",
    "READ_ACTIONS",
    "SEQUENCE",
    "STATIONS",
    "IN_FLIGHT_STATIONS",
    "TIERS",
    "WRITE_ACTIONS",
    "Action",
    "AuthorizationError",
    "Board",
    "BoardError",
    "ConfigStudioError",
    "CustodianCard",
    "CustodianPage",
    "CustodianPageError",
    "DiffReview",
    "DiffReviewError",
    "ExpectedFile",
    "Identity",
    "PromotionRequest",
    "QueueItem",
    "QueueItemKind",
    "QueueSources",
    "Role",
    "RoleMapping",
    "RuleImpact",
    "Station",
    "Transition",
    "add_custodian",
    "advance",
    "approvals_from",
    "authorized",
    "breaks_from",
    "build_custodian_page",
    "build_queue",
    "citation_link",
    "counts_by_kind",
    "drift_from",
    "exceptions_from",
    "for_role",
    "identity_from_claims",
    "live_per_week",
    "load_arrivals",
    "load_board",
    "load_promotion_requests",
    "load_role_mapping",
    "move",
    "render_custodian_page_markdown",
    "render_diff_markdown",
    "render_markdown",
    "render_permissions_markdown",
    "render_queue_markdown",
    "request_promotion",
    "require",
    "review",
    "save_board",
    "set_wip_limit",
    "start",
    "write_review",
]
