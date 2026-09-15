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
reviewed input, never its wording; and the dual-run and parity viewer (S6.3.7) — match rate per
day straight from `astra_verification.parity_report.ParityReport`'s own `by_cycle`, break groups
by rule and field with counts, and a record-pair drill-down showing legacy vs lakehouse with the
differing field highlighted, both from `astra_agents.break_explainer`'s own already-written
report, grouped two different ways rather than recomputed; and the run status dashboard (S6.3.8) —
per custodian and file, expected, arrived, parsed, resolved, published, timed from the latest
file's own arrival to its own publish against a real 20-minute budget grounded in the backlog's
own repeated requirement, every stage past "expected" caller-supplied since no live query for any
of them exists in this environment, the same honest shape `astra_control.custodian_page`'s own
`arrivals` already established; and drift change review (S6.3.9) — a detected layout change's own
proposed spec delta, applied purely in memory and diffed with `astra_control.spec_viewer.compare`
unchanged, an optional config diff via `astra_control.diff_review.review` itself when a candidate
config already exists, and an impact list (the spec's own custodians, consumers only when a
caller supplies them, since nothing tracks a downstream consumer anywhere in this repository) —
approval appends one entry to a non-prod change-request log, the same shape `astra_control.
config_studio.request_promotion` already established, never a git operation and never a write to
`specs/` or `configs/` themselves; and the golden dataset viewer (S6.3.10) — which business days
are captured per custodian, every version with its own real hash, read straight off
`astra_verification.golden.load_index`, gaps computed against `astra_verification.golden.
Capture.business_days_between` (the same walk the real capture CLI itself does), each gap paired
with the real `astra-verify golden capture` command that would fill it — composed and shown, never
run; and the audit log viewer (S6.3.11) — who approved what, when, unified across six real
sources this platform already writes (promotion requests, drift approvals, rule status history,
guardrail level changes, gate approvals, board moves), filterable by user, custodian, date and
action, exported to a real CSV — with two confirmed, honestly-named gaps neither invented around:
nothing anywhere records an "agent version" on an approval, and no record stores a pointer to the
evidence actually shown at decision time, only free text; and admin: autonomy levels and the
whitelist (S6.3.12) — `set_level` reimplements `astra_agents.guardrails.record_change`'s own
validation faithfully (a reason required, L3 refused outright unless real evidence already clears
the acceptance-rate and sample-size threshold) without importing the Agents plane, writing the
identical log shape that module already reads; the self-healing whitelist is `RejectionCode.
auto_resolve` on a domain pack's own real rejection taxonomy, read directly, never written
directly — a change is only ever a logged request, since the real file is hand-authored and has
no safe round-trip writer, the same "propose, log, a human applies it" shape `drift_review.
approve` already established; and notification preferences (S6.3.13) — which alerts reach a
person on which channel (Slack, email, in-app), and a per-custodian severity floor ops can tune
on top, `reaches()` combining both; the first per-user settings store in this plane, and the
first write action with no role gate at all — setting one's own channel preference changes
nothing about shared factory state, so every role, auditor included, may do it; and, opening
feature F6.2, approvals and autonomy levels (S6.2.1) — a new, general approval record richer than
any of F6.3's own five approval-shaped flows: who, when, a real checked evidence path, an
honestly-`None` agent version (nothing in this codebase tracks one, despite the product spec's own
claim that every approval does), and the proposing agent's own real autonomy level via `astra_
control.autonomy_admin.current_level`, called directly; a rejection is refused outright without a
comment, and "returns the item to the agent" as a real `rejection.yaml` written into the draft's
own real directory, not a live agent re-run — no agent CLI in this codebase reads one back yet;
and git as system of record (S6.2.2) — the first module in this whole codebase that actually
mutates git history, because it runs only after an approval already exists: a real
`PROVENANCE.json` (unifying the two shapes `astra_data.render`/`astra_data.migration` already
hand-roll), staged with exactly the given artifacts and nothing else in the working tree, one real
commit per approval; `verify_committed` names exactly which artifacts git does not actually track,
the concrete meaning behind "no artifact exists only in the factory database." And throughput and
cost metrics (S6.2.3) — custodians live per week straight from `astra_control.board.live_per_week`,
unchanged; agent acceptance per custodian per day, a real event ratio (confirmed vs. rejected or
legacy_defect review outcomes from `astra_control.audit_log.rule_status_changes_from`'s own
`rule_status_change` records, "recovered" excluded since it is not a review decision) deliberately
distinct from `astra_verification.agent_eval`'s own gold-set precision/recall, the two named
separately in one report rather than conflated the way the product spec's own API sketch reads;
cost per custodian per day shown only when a caller supplies it, since no query-tag mechanism
exists anywhere in this repository to compute one — assembled into one weekly Markdown and JSON
report for the client's own reporting cadence, the same `weekly.md`/`weekly.json` convention
`astra_verification.agent_eval.write_weekly_report` already established. No live Postgres
yet: `board.yaml` and the promotion-requests log are real, working stand-ins
for the store E6 will eventually have (astra_control.board's own module docstring).
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
from astra_control.parity_viewer import (
    BreakGroup,
    FieldDiff,
    ParityViewerError,
    RecordPair,
    Trend,
    TrendPoint,
    break_groups,
    load_trend,
    record_pair,
    record_pairs,
)
from astra_control.parity_viewer import render_break_groups_markdown, render_record_pair_markdown, render_record_pairs_markdown, render_trend_markdown
from astra_control.run_status import (
    RUN_WINDOW_BUDGET_MINUTES,
    STAGES,
    CustodianRunStatus,
    Dashboard,
    FileStage,
    RunInput,
    RunStatusError,
    build as build_run_status,
    build_dashboard,
    load_run,
)
from astra_control.run_status import render_dashboard_markdown, render_status_markdown
from astra_control.drift_review import (
    ChangeRequest,
    DriftReview,
    DriftReviewError,
    apply_drift_findings,
    approve as approve_drift,
    load_change_requests,
    review as review_drift,
)
from astra_control.drift_review import render_change_requests_markdown, render_markdown as render_drift_markdown
from astra_control.golden_viewer import CapturedDay, Gap, GoldenCalendar, GoldenViewerError, build as build_golden_calendar
from astra_control.golden_viewer import render_markdown as render_golden_calendar_markdown
from astra_control.audit_log import (
    CSV_COLUMNS,
    AuditLogError,
    AuditRecord,
    AuditSources,
    board_moves_from,
    build_audit_log,
    drift_approvals_from,
    filter_records,
    gate_approvals_from,
    guardrail_changes_from,
    promotion_requests_from,
    rule_status_changes_from,
    to_csv,
    write_csv,
)
from astra_control.audit_log import render_markdown as render_audit_log_markdown
from astra_control.autonomy_admin import (
    DEFAULT_LEVEL,
    L3_ACCEPTANCE_THRESHOLD,
    L3_MINIMUM_SAMPLE,
    LEVELS,
    AutonomyAdminError,
    Evidence,
    LevelChange,
    WhitelistRequest,
    current_change,
    current_level,
    every_current_level,
    load_changes,
    load_whitelist,
    load_whitelist_requests,
    request_whitelist_change,
    set_level,
    whitelisted_codes,
)
from astra_control.autonomy_admin import render_levels_markdown, render_whitelist_markdown, render_whitelist_requests_markdown
from astra_control.notification_preferences import (
    CHANNELS,
    SEVERITIES,
    NotificationPreferencesError,
    NotificationSettings,
    load_settings,
    reaches,
    save_settings,
    set_custodian_threshold,
    set_user_preferences,
)
from astra_control.notification_preferences import render_thresholds_markdown, render_user_markdown
from astra_control.approvals import (
    Approval,
    ApprovalError,
    Rejection,
    approve,
    load_approvals,
    load_rejections,
    reject,
)
from astra_control.approvals import render_approvals_markdown, render_rejections_markdown
from astra_control.git_provenance import (
    Commit,
    GitProvenanceError,
    ProvenanceRecord,
    build_provenance,
    commit_approval,
    verify_committed,
    write_provenance,
)
from astra_control.git_provenance import render_commit_markdown, render_verify_markdown
from astra_control.throughput_metrics import (
    ACCEPTED_STATUS,
    COUNTED_STATUSES,
    REJECTED_STATUSES,
    CustodianDayAcceptance,
    CustodianDayCost,
    ThroughputReport,
    acceptance_by_custodian_day,
    build_report as build_throughput_report,
    write_report as write_throughput_report,
)
from astra_control.throughput_metrics import render_markdown as render_throughput_markdown

__all__ = [
    "ACCEPTED_STATUS",
    "CHANNELS",
    "COUNTED_STATUSES",
    "CSV_COLUMNS",
    "DEFAULT_LEVEL",
    "KIND_ROLES",
    "L3_ACCEPTANCE_THRESHOLD",
    "L3_MINIMUM_SAMPLE",
    "LEVELS",
    "PERMISSIONS",
    "READ_ACTIONS",
    "REJECTED_STATUSES",
    "REJECTION_TAG_PREFIX",
    "REVIEW_STATUSES",
    "RULE_RECOVERY_AGENT",
    "RUN_WINDOW_BUDGET_MINUTES",
    "SEQUENCE",
    "SEVERITIES",
    "STAGES",
    "STATIONS",
    "IN_FLIGHT_STATIONS",
    "TIERS",
    "WRITE_ACTIONS",
    "Action",
    "AgentReviewError",
    "Approval",
    "ApprovalError",
    "AuditLogError",
    "AuditRecord",
    "AuditSources",
    "AuthorizationError",
    "AutonomyAdminError",
    "Board",
    "BoardError",
    "BreakGroup",
    "BulkResult",
    "CapturedDay",
    "ChangeRequest",
    "Commit",
    "ConfigStudioError",
    "CustodianCard",
    "CustodianDayAcceptance",
    "CustodianDayCost",
    "CustodianPage",
    "CustodianPageError",
    "CustodianRunStatus",
    "Dashboard",
    "DiffReview",
    "DiffReviewError",
    "DriftReview",
    "DriftReviewError",
    "Edited",
    "Evidence",
    "ExpectedFile",
    "FieldChange",
    "FieldDiff",
    "FieldEntry",
    "FileStage",
    "Gap",
    "GitProvenanceError",
    "GoldenCalendar",
    "GoldenViewerError",
    "Identity",
    "LevelChange",
    "NotificationPreferencesError",
    "NotificationSettings",
    "ParityViewerError",
    "PromotionRequest",
    "ProvenanceRecord",
    "QueueItem",
    "QueueItemKind",
    "QueueSources",
    "RecordPair",
    "Rejection",
    "ReviewItem",
    "Role",
    "RoleMapping",
    "RuleEntry",
    "RuleImpact",
    "RuleReviewError",
    "RunInput",
    "RunStatusError",
    "SpecFieldDiff",
    "SpecViewerError",
    "Station",
    "ThroughputReport",
    "Transition",
    "Trend",
    "TrendPoint",
    "WhitelistRequest",
    "accept_agent_review",
    "acceptance_by_custodian_day",
    "add_custodian",
    "advance",
    "apply_drift_findings",
    "approvals_from",
    "approve",
    "approve_drift",
    "authorized",
    "board_moves_from",
    "break_groups",
    "breaks_from",
    "build_custodian_page",
    "build_dashboard",
    "build_golden_calendar",
    "build_provenance",
    "build_queue",
    "build_run_status",
    "build_throughput_report",
    "bulk_confirm",
    "change_status",
    "citation_link",
    "commit_approval",
    "compare_specs",
    "counts_by_kind",
    "current_change",
    "current_level",
    "drift_approvals_from",
    "drift_from",
    "edit_citation",
    "edit_text",
    "every_current_level",
    "exceptions_from",
    "field_list",
    "filter_records",
    "filter_rules",
    "for_role",
    "gate_approvals_from",
    "guardrail_changes_from",
    "identical_rules",
    "identity_from_claims",
    "live_per_week",
    "load_approvals",
    "load_arrivals",
    "load_board",
    "load_catalog",
    "load_change_requests",
    "load_draft_rule",
    "load_promotion_requests",
    "load_registry",
    "load_rejections",
    "load_role_mapping",
    "load_run",
    "load_spec",
    "load_settings",
    "load_trend",
    "load_whitelist",
    "load_whitelist_requests",
    "move",
    "promotion_requests_from",
    "reaches",
    "record_pair",
    "record_pairs",
    "reject",
    "reject_agent_review",
    "rejection_codes",
    "render_agent_review_diff_markdown",
    "render_agent_review_markdown",
    "render_approvals_markdown",
    "render_audit_log_markdown",
    "render_break_groups_markdown",
    "render_bulk_result",
    "render_change_requests_markdown",
    "render_commit_markdown",
    "render_custodian_page_markdown",
    "render_dashboard_markdown",
    "render_diff_markdown",
    "render_drift_markdown",
    "render_field_list",
    "render_golden_calendar_markdown",
    "render_levels_markdown",
    "render_markdown",
    "render_permissions_markdown",
    "render_queue_markdown",
    "render_record_pair_markdown",
    "render_record_pairs_markdown",
    "render_rejections_markdown",
    "render_rule_review_markdown",
    "render_spec_compare",
    "render_status_markdown",
    "render_thresholds_markdown",
    "render_throughput_markdown",
    "render_trend_markdown",
    "render_user_markdown",
    "render_verify_markdown",
    "render_whitelist_markdown",
    "render_whitelist_requests_markdown",
    "request_promotion",
    "request_whitelist_change",
    "require",
    "review",
    "review_drift",
    "rule_entry",
    "rule_recovery_item",
    "rule_status_changes_from",
    "save_board",
    "save_settings",
    "set_custodian_threshold",
    "set_level",
    "set_user_preferences",
    "set_wip_limit",
    "spec_citation_link",
    "start",
    "to_csv",
    "verify_committed",
    "whitelisted_codes",
    "write_csv",
    "write_provenance",
    "write_review",
    "write_throughput_report",
]
