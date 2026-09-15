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
source is missing; and the spec registry viewer (S6.3.4) — every field of a Source Spec with its
own citation turned into a real, openable reference, and a version compare that highlights added,
removed and shifted fields (a field whose own position moved between two versions, the one spec
change AC2 names on its own), matched against real committed drift between `specs/pershing_gcus`'s
two versions rather than a synthetic example; and the rule catalog browser and review (S6.3.5) —
a rule's text, class and citation side by side, confirmed, rejected or marked a legacy defect by
calling `astra_knowledge.rules.set_status` directly rather than a second mutation, bulk confirm
reaching every rule sharing the exact same text; and agent suggestion review (S6.3.6) — a Rule
Recovery draft's own text and citation side by side, an edit diffed against the kept original, and
accept/reject each appending a new case straight into that agent's own gold set
(`astra_verification.agent_eval.append_case`), naming which rule id should be recovered from the
reviewed input, never its wording. No live Postgres yet: `board.yaml` and the promotion-requests
log are real, working stand-ins for the store E6 will eventually have (astra_control.board's own
module docstring).
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
from astra_control.spec_viewer import (
    FieldEntry,
    SpecFieldDiff,
    SpecViewerError,
    compare as compare_specs,
    field_list,
    load_registry,
    load_spec,
)
from astra_control.spec_viewer import citation_link as spec_citation_link
from astra_control.spec_viewer import render_compare as render_spec_compare
from astra_control.spec_viewer import render_field_list
from astra_control.rule_review import (
    REJECTION_TAG_PREFIX,
    REVIEW_STATUSES,
    BulkResult,
    RuleEntry,
    RuleReviewError,
    bulk_confirm,
    change_status,
    filter_rules,
    identical_rules,
    load_catalog,
    rejection_codes,
    rule_entry,
)
from astra_control.rule_review import render_bulk_result
from astra_control.rule_review import render_markdown as render_rule_review_markdown
from astra_control.agent_review import (
    RULE_RECOVERY_AGENT,
    AgentReviewError,
    Edited,
    FieldChange,
    ReviewItem,
    accept as accept_agent_review,
    edit_citation,
    edit_text,
    load_draft_rule,
    reject as reject_agent_review,
    rule_recovery_item,
)
from astra_control.agent_review import render_diff_markdown as render_agent_review_diff_markdown
from astra_control.agent_review import render_markdown as render_agent_review_markdown

__all__ = [
    "KIND_ROLES",
    "PERMISSIONS",
    "READ_ACTIONS",
    "REJECTION_TAG_PREFIX",
    "REVIEW_STATUSES",
    "RULE_RECOVERY_AGENT",
    "SEQUENCE",
    "STATIONS",
    "IN_FLIGHT_STATIONS",
    "TIERS",
    "WRITE_ACTIONS",
    "Action",
    "AgentReviewError",
    "AuthorizationError",
    "Board",
    "BoardError",
    "BulkResult",
    "ConfigStudioError",
    "CustodianCard",
    "CustodianPage",
    "CustodianPageError",
    "DiffReview",
    "DiffReviewError",
    "Edited",
    "ExpectedFile",
    "FieldChange",
    "FieldEntry",
    "Identity",
    "PromotionRequest",
    "QueueItem",
    "QueueItemKind",
    "QueueSources",
    "ReviewItem",
    "Role",
    "RoleMapping",
    "RuleEntry",
    "RuleImpact",
    "RuleReviewError",
    "SpecFieldDiff",
    "SpecViewerError",
    "Station",
    "Transition",
    "accept_agent_review",
    "add_custodian",
    "advance",
    "approvals_from",
    "authorized",
    "breaks_from",
    "build_custodian_page",
    "build_queue",
    "bulk_confirm",
    "change_status",
    "citation_link",
    "compare_specs",
    "counts_by_kind",
    "drift_from",
    "edit_citation",
    "edit_text",
    "exceptions_from",
    "field_list",
    "filter_rules",
    "for_role",
    "identical_rules",
    "identity_from_claims",
    "live_per_week",
    "load_arrivals",
    "load_board",
    "load_catalog",
    "load_draft_rule",
    "load_promotion_requests",
    "load_registry",
    "load_role_mapping",
    "load_spec",
    "move",
    "reject_agent_review",
    "rejection_codes",
    "render_agent_review_diff_markdown",
    "render_agent_review_markdown",
    "render_bulk_result",
    "render_custodian_page_markdown",
    "render_diff_markdown",
    "render_field_list",
    "render_markdown",
    "render_permissions_markdown",
    "render_queue_markdown",
    "render_rule_review_markdown",
    "render_spec_compare",
    "request_promotion",
    "require",
    "review",
    "rule_entry",
    "rule_recovery_item",
    "save_board",
    "set_wip_limit",
    "spec_citation_link",
    "start",
    "write_review",
]
