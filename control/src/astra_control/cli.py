"""Command line: `astra-control board add|move|set-wip-limit|show`, `astra-control config-studio
start|advance|request-promotion|show-requests`, `astra-control diff-review run`, `astra-control
permissions show|resolve`, `astra-control queue show`, `astra-control custodian-page show`,
`astra-control spec-viewer show|compare`, `astra-control rule-review show|set-status|
bulk-confirm`, `astra-control agent-review show|edit|accept|reject`, `astra-control
parity-viewer trend|breaks|records|record`, `astra-control run-status show|dashboard`,
`astra-control drift-review show|approve|show-requests`, `astra-control golden-viewer show` and
`astra-control audit-log show|export`, `astra-control autonomy-admin show-levels|set-level|
show-whitelist|request-whitelist-change|show-whitelist-requests` and `astra-control
notification-preferences show|set|show-thresholds|set-threshold|reaches` and `astra-control
approvals approve|reject|show` and `astra-control git-provenance commit|verify` and `astra-control throughput-metrics show|export` and `astra-control gate-evidence-pack show|export`.

No credentials, no live Postgres: `board.yaml` and the promotion-requests log (named on every
command with `--board`/`--requests`) are the whole state, read fresh and rewritten on every
command — see astra_control.board's and astra_control.config_studio's own module docstrings for
why. `diff-review run` needs no credentials either — it reads two config files and the spec
registry, rule catalog and domain packs already on disk; nothing it does needs Snowflake or a
model call. `permissions resolve` needs no live identity provider either — see
astra_control.permissions' own module docstring for why real SSO cannot honestly run here, and
what this module owns instead.

Every write command (`board add|move|set-wip-limit`, `config-studio start|advance|
request-promotion`) and every read command (`board show`, `config-studio show-requests`,
`diff-review run`) takes an optional `--role`: given, it is checked against that command's own
action (`astra_control.permissions.Action`) before anything runs — refused (exit 2) before a
single line is written when the role is not allowed; omitted, the command behaves exactly as it
did before this story. Exit codes otherwise: every write command is 0 on success, 2 when the
change is rejected (a blank field, an unknown custodian, a move that would exceed a stream's WIP
limit, a promotion requested out of order, a medium/complex request missing its reviewer, or a
role not authorized for the action) — the same shape `astra_agents.guardrails`'s own `set-level`
uses for a rejected change: refused outright, never applied and flagged. `board show` is 0 when
every stream is within its own WIP limit, 1 when at least one stream is over (a real condition to
look at, not a start error), 2 for a bad `--board` argument or an unauthorized role.
`config-studio show-requests` and `diff-review run` are 0 unless the role check itself fails (2).
`permissions show` is always 0; `permissions resolve` is 0 when the claims resolve to exactly one
role, 2 otherwise. `queue show` reads whichever report and gate-pack paths are given — a missing
or unreadable one contributes nothing, never an error, the same "no evidence" shape
`astra_agents.gate_evidence_compiler` already established — and is 0 when nothing needs the given
role (or, unfiltered, nobody), 1 when the queue is non-empty (a real condition to look at), 2 only
for an unrecognized `--role`. `custodian-page show` reads a compiled config plus whichever of
`--board`/`--parity-report`/`--exception-report`/`--arrivals`/`--cost` are given — every one
optional, each field shown honestly as "no data" rather than fabricated when its own source is
missing (`astra_control.custodian_page`'s own module docstring) — and is 0 unless the config
itself fails to compile or the role check fails (2). `spec-viewer show` is 0 unless the spec
itself is not found or the role check fails (2). `spec-viewer compare` is 0 when the two versions
have no differences, 1 when they do (a real condition to look at, the same shape `board show`
already uses for a stream over its limit), 2 for a missing spec version or the role check.
`rule-review show` is always 0 unless the role check fails (2) or `--status` names something that
is not a real status. `rule-review set-status` is 0 on success, 2 when the change is refused (an
unknown rule id, a status this screen does not set, or anything `astra_knowledge.rules.set_status`
itself refuses — already at that status, no `--by` given) or the role check fails. `rule-review
bulk-confirm` confirms the named rule and every rule sharing its exact text (`astra_control.
rule_review`'s own module docstring); one rule's own refusal (already confirmed, say) never blocks
the others — 0 when every rule in the batch confirmed, 1 when at least one did not (shown, not
silently dropped), 2 for an unknown rule id or the role check.
`agent-review show|edit` are 0 unless the draft file itself fails to load or the role check fails
(2); `edit` writes nothing, it only shows a diff. `agent-review accept|reject` are 0 on success, 2
when the draft fails to load, the gold set does not exist or is for a different agent, the case id
already exists, the given tier has no threshold in that gold set, or the role check fails
(`astra_control.agent_review`'s own module docstring). `parity-viewer trend|breaks|records` are 0
unless the given report file is missing, unreadable, or (for `trend`) not a parity report at all,
or the role check fails (2). `parity-viewer record` is the same, plus 2 when no record matches the
given `--key`. `run-status show` is 0 unless the config fails to compile or the role check fails
(2), 1 when the custodian is late as of `--as-of` (default: now) — a real condition to look at,
the same shape `board show` already uses for a stream over its limit. `run-status dashboard` is 2
when `--config` and `--run` are not given the same number of times or any config fails to compile,
otherwise 0 unless at least one custodian is late (1). `drift-review show` is 0 unless the drift
report or the given spec version is not found, a config diff is asked for and fails, or the role
check fails (2). `drift-review approve` is 0 on success, 2 when the same review fails, `--approved
-by` is blank (nothing written), or the role check fails; it never writes to `specs/` or
`configs/`, only the given `--requests` log (`astra_control.drift_review`'s own module docstring).
`drift-review show-requests` is always 0 unless the role check fails.
`golden-viewer show` is 0 when every business day in the window is captured, 1 when at least one
is a gap (a real condition to look at, the same shape `board show` already uses for a stream over
its limit), 2 for a bad capture file, an inverted `--from`/`--to`, or the role check.
`audit-log show` is always 0 unless the role check fails; every source it is given is read
defensively — a missing or unreadable one contributes nothing, never an error (`astra_control.
audit_log`'s own module docstring). `audit-log export` is the same, plus 2 when the given `--out`
path cannot be written.
`autonomy-admin show-levels|show-whitelist|show-whitelist-requests` are always 0 unless the role
check fails, or (`show-whitelist`) the given `--rejections` file does not exist (2). `autonomy-
admin set-level` is 0 on success, 2 when the change is refused (a blank field, an unknown level, a
change to L3 without evidence clearing the threshold) or the role check fails — refused outright,
nothing written, the same shape `astra_agents.guardrails.record_change` itself uses (`astra_
control.autonomy_admin`'s own module docstring). `autonomy-admin request-whitelist-change` is the
same, plus 2 when `--rejections` is given and the code is not real or the request is a no-op; it
never writes to `--rejections` itself, only the given `--requests` log.
`notification-preferences show|show-thresholds|reaches` are always 0 unless the role check fails
(2), except `reaches` itself, which is 1 when the alert would not reach the given user (a real
condition to look at, the same shape `board show` already uses for a stream over its limit).
`notification-preferences set|set-threshold` are 0 on success, 2 when the change is refused (an
unknown channel or severity, a blank field) or the role check fails; `set` is available to every
role, including auditor, since it only ever changes the caller's own preference, never this
plane's own shared state (`astra_control.notification_preferences`'s own module docstring).
`approvals show` is always 0 unless the role check fails. `approvals approve|reject` are 0 on
success, 2 when the change is refused (a blank field, a rejection with no comment, an
`--evidence-path` or `--draft-dir` that does not exist) or the role check fails — refused
outright, nothing written (`astra_control.approvals`'s own module docstring).
`git-provenance verify` is 0 when every given artifact is tracked by git, 1 when at least one is
not (a real condition to look at, the same shape `board show` already uses for a stream over its
limit), 2 for a bad `--repo` or the role check. `git-provenance commit` is 0 on a real new commit,
2 when no approval matches `--subject`, an artifact is missing or outside the repo, the repo
already has something else staged, or the role check fails — refused outright, no commit made
(`astra_control.git_provenance`'s own module docstring: this is the first command in this whole
CLI that actually mutates git history).
`throughput-metrics show|export` are always 0 unless a `--cost` triple is malformed, a given
`--agent-eval-weekly` file cannot be read, or the role check fails (2) — both are reads; nothing
here mutates anything.
`gate-evidence-pack show` is 0 unless the given `--gate-pack` does not exist or is not real JSON,
or the role check fails (2); 1 when at least one criterion is NOT MET (a real condition to look
at, the same shape `board show` already uses for a stream over its limit). `gate-evidence-pack
export` is the same, plus 2 when the given `--out` path cannot be written — both are reads; a PDF
exported to a caller-given path is no more a factory-state write than a CSV or a weekly report
already were (`astra_control.gate_evidence_pack`'s own module docstring).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from astra_control.board import STATIONS, Board, BoardError, add_custodian, load_board, move, render_markdown, save_board, set_wip_limit
from astra_control.config_studio import SEQUENCE, TIERS, ConfigStudioError, advance, load_promotion_requests, request_promotion, start
from astra_control.diff_review import DiffReviewError
from astra_control.diff_review import render_markdown as render_diff_markdown
from astra_control.diff_review import review, write_review
from astra_control.permissions import Action, AuthorizationError, Role, identity_from_claims, load_role_mapping, render_permissions, require
from astra_control.queue import QueueSources, build_queue, for_role
from astra_control.queue import render_markdown as render_queue_markdown
from astra_control.custodian_page import CustodianPageError, load_arrivals
from astra_control.custodian_page import build as build_custodian_page
from astra_control.custodian_page import render_markdown as render_custodian_page_markdown
from astra_control.spec_viewer import SpecViewerError, compare, field_list, load_registry, load_spec
from astra_control.spec_viewer import render_compare as render_spec_compare
from astra_control.spec_viewer import render_field_list
from astra_control.rule_review import REVIEW_STATUSES, RuleReviewError, bulk_confirm, change_status, filter_rules, load_catalog, rule_entry
from astra_control.rule_review import render_bulk_result
from astra_control.rule_review import render_markdown as render_rule_review_markdown
from astra_verification.agent_eval import TIERS
from astra_knowledge.rules import Citation
from astra_control.agent_review import AgentReviewError, Edited, accept, edit_citation, edit_text, load_draft_rule, reject, rule_recovery_item
from astra_control.agent_review import render_diff_markdown as render_agent_review_diff_markdown
from astra_control.agent_review import render_markdown as render_agent_review_markdown
from astra_control.parity_viewer import ParityViewerError, break_groups, load_trend, record_pair, record_pairs
from astra_control.parity_viewer import render_break_groups_markdown, render_record_pair_markdown, render_record_pairs_markdown, render_trend_markdown
from astra_control.run_status import RunStatusError, build as build_run_status, build_dashboard
from astra_control.run_status import render_dashboard_markdown, render_status_markdown
from astra_control.drift_review import DriftReviewError, approve as approve_drift, load_change_requests, review as review_drift
from astra_control.drift_review import render_change_requests_markdown, render_markdown as render_drift_markdown
from astra_control.golden_viewer import GoldenViewerError, build as build_golden_calendar
from astra_control.golden_viewer import render_markdown as render_golden_calendar_markdown
from astra_control.audit_log import AuditLogError, AuditSources, build_audit_log, filter_records, rule_status_changes_from, write_csv
from astra_control.autonomy_admin import LEVELS, AutonomyAdminError, Evidence, load_changes, load_whitelist, load_whitelist_requests, request_whitelist_change, set_level, whitelisted_codes
from astra_control.autonomy_admin import render_levels_markdown, render_whitelist_markdown, render_whitelist_requests_markdown
from astra_control.notification_preferences import CHANNELS, SEVERITIES, NotificationPreferencesError, load_settings, reaches, save_settings, set_custodian_threshold, set_user_preferences
from astra_control.notification_preferences import render_thresholds_markdown, render_user_markdown
from astra_control.approvals import ApprovalError, approve, load_approvals, load_rejections, reject
from astra_control.approvals import render_approvals_markdown, render_rejections_markdown
from astra_control.git_provenance import GitProvenanceError, commit_approval, verify_committed
from astra_control.git_provenance import render_commit_markdown, render_verify_markdown
from astra_control.throughput_metrics import build_report, render_markdown as render_throughput_markdown, write_report
from astra_control.gate_evidence_pack import GateEvidencePackError, build_pack as build_gate_evidence_pack, render_markdown as render_gate_evidence_pack_markdown, write_pdf as write_gate_evidence_pdf
from astra_control.audit_log import render_markdown as render_audit_log_markdown


def _check(args: argparse.Namespace, action: Action) -> int | None:
    """When --role is given, enforce it against `action` before the command proceeds — returns
    an exit code to return immediately on refusal, or None to proceed. Omitted, a command behaves
    exactly as it did before this story (astra_control.permissions' own module docstring)."""
    role = getattr(args, "role", None)
    if not role:
        return None
    try:
        require(role, action)
    except AuthorizationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return None


def cmd_board_add(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.BOARD_ADD)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        board = add_custodian(board, args.custodian, args.stream, by=args.by)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added: {args.custodian} to {args.stream}, at profile")
    return 0


def cmd_board_move(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.BOARD_MOVE)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        board = move(board, args.custodian, args.to, by=args.by)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"moved: {args.custodian} -> {args.to}")
    return 0


def cmd_board_set_wip_limit(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.BOARD_SET_WIP_LIMIT)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        board = set_wip_limit(board, args.stream, args.limit)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set: {args.stream} WIP limit -> {args.limit}")
    return 0


def cmd_board_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.BOARD_SHOW)) is not None:
        return code
    board = load_board(Path(args.board))
    if args.json:
        print(json.dumps(board.to_dict(), indent=2))
    else:
        print(render_markdown(board))
        over = board.over_limit_streams()
        if over:
            print(f"over limit: {', '.join(over)}")
    return 1 if board.over_limit_streams() else 0


def cmd_config_studio_start(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.CONFIG_STUDIO_START)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        board = start(board, args.custodian, args.stream, by=args.by)
        save_board(board, Path(args.board))
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"started: {args.custodian} ({args.stream}), profiled by {args.by}")
    return 0


def cmd_config_studio_advance(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.CONFIG_STUDIO_ADVANCE)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        board = advance(board, args.custodian, args.to, by=args.by)
        save_board(board, Path(args.board))
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"advanced: {args.custodian} -> {args.to}, by {args.by}")
    return 0


def cmd_config_studio_request_promotion(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.CONFIG_STUDIO_REQUEST_PROMOTION)) is not None:
        return code
    try:
        board = load_board(Path(args.board))
        request_promotion(
            board,
            Path(args.requests),
            args.custodian,
            tier=args.tier,
            requested_by=args.requested_by,
            reviewed_by=args.reviewed_by,
            note=args.note,
        )
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    self_service = args.tier == "simple"
    print(f"requested: promotion for {args.custodian} ({args.tier} tier){' — self-service, no engineer' if self_service else f', reviewed by {args.reviewed_by}'}")
    return 0


def cmd_config_studio_show_requests(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.CONFIG_STUDIO_SHOW_REQUESTS)) is not None:
        return code
    requests = load_promotion_requests(Path(args.requests) if args.requests else None)
    if args.custodian:
        requests = tuple(r for r in requests if r.custodian_id == args.custodian)
    if args.json:
        print(json.dumps([r.to_dict() for r in requests], indent=2))
    else:
        if not requests:
            print("No promotion requests recorded yet.")
        for r in requests:
            reviewer = "self-service, no engineer" if r.self_service else f"reviewed by {r.reviewed_by}"
            print(f"{r.at}  {r.custodian_id}  {r.tier}  requested by {r.requested_by}  ({reviewer})")
    return 0


def cmd_diff_review_run(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.DIFF_REVIEW_RUN)) is not None:
        return code
    try:
        result = review(
            Path(args.old),
            Path(args.new),
            other_configs=[Path(p) for p in args.other_config],
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
        )
    except DiffReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    report_path, _data_path = write_review(result, out)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_diff_markdown(result))
        print(f"  report: {report_path}")
    return 0


def cmd_permissions_show(args: argparse.Namespace) -> int:
    try:
        role = Role(args.role) if args.role else None
    except ValueError:
        print(f"error: '{args.role}' is not a role; roles are {', '.join(r.value for r in Role)}", file=sys.stderr)
        return 2
    print(render_permissions(role))
    return 0


def cmd_permissions_resolve(args: argparse.Namespace) -> int:
    try:
        mapping = load_role_mapping(Path(args.mapping))
        identity = identity_from_claims({"email": args.email, "name": args.name, "groups": args.group}, mapping)
    except AuthorizationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{identity.email} ({identity.name}) -> {identity.role.value}")
    return 0


def cmd_queue_show(args: argparse.Namespace) -> int:
    sources = QueueSources(
        gate_packs=tuple(Path(p) for p in args.gate_pack),
        exception_triage_reports=tuple(Path(p) for p in args.exception_report),
        break_explainer_reports=tuple(Path(p) for p in args.break_report),
        drift_watcher_reports=tuple(Path(p) for p in args.drift_report),
    )
    items = build_queue(sources)
    role = None
    if args.role:
        try:
            role = Role(args.role)
        except ValueError:
            print(f"error: '{args.role}' is not a role; roles are {', '.join(r.value for r in Role)}", file=sys.stderr)
            return 2
        items = for_role(items, role)
    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2))
    else:
        print(render_queue_markdown(items, role=role))
    return 1 if items else 0


def cmd_custodian_page_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.CUSTODIAN_PAGE_SHOW)) is not None:
        return code
    try:
        arrivals = load_arrivals(Path(args.arrivals) if args.arrivals else None)
        page = build_custodian_page(
            Path(args.config),
            board_path=Path(args.board) if args.board else None,
            parity_report=Path(args.parity_report) if args.parity_report else None,
            exception_report=Path(args.exception_report) if args.exception_report else None,
            arrivals=arrivals,
            cost=args.cost,
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
        )
    except CustodianPageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(page.to_dict(), indent=2))
    else:
        print(render_custodian_page_markdown(page))
    return 0


def cmd_spec_viewer_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.SPEC_VIEWER_SHOW)) is not None:
        return code
    try:
        registry = load_registry(Path(args.specs))
        spec = load_spec(registry, args.id, args.version)
    except SpecViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([f.to_dict() for f in field_list(spec)], indent=2))
    else:
        print(render_field_list(spec))
    return 0


def cmd_spec_viewer_compare(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.SPEC_VIEWER_COMPARE)) is not None:
        return code
    try:
        registry = load_registry(Path(args.specs))
        old = load_spec(registry, args.id, args.old)
        new = load_spec(registry, args.id, args.new)
    except SpecViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    diffs = compare(old, new)
    if args.json:
        print(json.dumps([d.to_dict() for d in diffs], indent=2))
    else:
        print(render_spec_compare(old, new, diffs))
    return 1 if diffs else 0


def cmd_rule_review_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.RULE_REVIEW_SHOW)) is not None:
        return code
    try:
        catalog = load_catalog(Path(args.rules))
        rules = filter_rules(catalog, status=args.status, custodian=args.custodian, rejection_code=args.rejection_code)
    except RuleReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entries = tuple(rule_entry(r) for r in rules)
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2))
    else:
        print(render_rule_review_markdown(entries))
    return 0


def cmd_rule_review_set_status(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.RULE_REVIEW_SET_STATUS)) is not None:
        return code
    try:
        catalog = load_catalog(Path(args.rules))
        rule = change_status(catalog, args.id, args.status, by=args.by, note=args.note)
    except RuleReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{rule.id}: {args.status}, by {args.by}")
    return 0


def cmd_rule_review_bulk_confirm(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.RULE_REVIEW_BULK_CONFIRM)) is not None:
        return code
    try:
        catalog = load_catalog(Path(args.rules))
        results = bulk_confirm(catalog, args.id, by=args.by, note=args.note)
    except RuleReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print(render_bulk_result(results))
    return 1 if any(not r.ok for r in results) else 0


def _agent_review_item(args: argparse.Namespace):
    try:
        rule = load_draft_rule(Path(args.draft))
        item = rule_recovery_item(rule, case_input=args.input, tier=args.tier)
    except AgentReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, None
    return rule, item


def _apply_edit_args(item, rule, args: argparse.Namespace):
    """--text and/or --citation-file/--citation-line/--citation-end-line/--citation-repository,
    applied in order; returns the item unchanged if none were given."""
    if args.text:
        item = edit_text(item, args.text).edited
    if args.citation_file or args.citation_line:
        if not (args.citation_file and args.citation_line):
            raise AgentReviewError("--citation-file and --citation-line must be given together")
        citation = Citation(kind="code", file=args.citation_file, line=args.citation_line, end_line=args.citation_end_line, repository=args.citation_repository)
        item = edit_citation(item, rule, citation).edited
    return item


def cmd_agent_review_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AGENT_REVIEW_SHOW)) is not None:
        return code
    rule, item = _agent_review_item(args)
    if item is None:
        return 2
    if args.json:
        print(json.dumps(item.to_dict(), indent=2))
    else:
        print(render_agent_review_markdown(item))
    return 0


def cmd_agent_review_edit(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AGENT_REVIEW_EDIT)) is not None:
        return code
    rule, item = _agent_review_item(args)
    if item is None:
        return 2
    try:
        edited_item = _apply_edit_args(item, rule, args)
    except AgentReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    edited = Edited(original=item, edited=edited_item)
    if args.json:
        print(json.dumps(edited.to_dict(), indent=2))
    else:
        print(render_agent_review_diff_markdown(edited))
    return 0


def cmd_agent_review_accept(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AGENT_REVIEW_ACCEPT)) is not None:
        return code
    rule, item = _agent_review_item(args)
    if item is None:
        return 2
    try:
        final_item = _apply_edit_args(item, rule, args)
        case = accept(Path(args.gold_set), final_item, case_id=args.case_id)
    except AgentReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"accepted: {args.case_id} -> {', '.join(case.expected) if case.expected else '(nothing)'}")
    return 0


def cmd_agent_review_reject(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AGENT_REVIEW_REJECT)) is not None:
        return code
    rule, item = _agent_review_item(args)
    if item is None:
        return 2
    try:
        case = reject(Path(args.gold_set), item, case_id=args.case_id)
    except AgentReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"rejected: {args.case_id}")
    return 0


def cmd_parity_viewer_trend(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.PARITY_VIEWER_TREND)) is not None:
        return code
    try:
        trend = load_trend(Path(args.parity_report))
    except ParityViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(trend.to_dict(), indent=2))
    else:
        print(render_trend_markdown(trend))
    return 0


def cmd_parity_viewer_breaks(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.PARITY_VIEWER_BREAKS)) is not None:
        return code
    try:
        groups = break_groups(Path(args.break_report))
    except ParityViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([g.to_dict() for g in groups], indent=2))
    else:
        print(render_break_groups_markdown(groups))
    return 0


def cmd_parity_viewer_records(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.PARITY_VIEWER_RECORDS)) is not None:
        return code
    try:
        pairs = record_pairs(Path(args.break_report))
    except ParityViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([p.to_dict() for p in pairs], indent=2))
    else:
        print(render_record_pairs_markdown(pairs))
    return 0


def cmd_parity_viewer_record(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.PARITY_VIEWER_RECORD)) is not None:
        return code
    try:
        pair = record_pair(Path(args.break_report), tuple(args.key))
    except ParityViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(pair.to_dict(), indent=2))
    else:
        print(render_record_pair_markdown(pair))
    return 0


def cmd_run_status_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.RUN_STATUS_SHOW)) is not None:
        return code
    try:
        status = build_run_status(
            Path(args.config),
            run_path=Path(args.run) if args.run else None,
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
        )
    except RunStatusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(status.to_dict(), indent=2))
    else:
        print(render_status_markdown(status))
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    return 1 if status.is_late(as_of) else 0


def cmd_run_status_dashboard(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.RUN_STATUS_DASHBOARD)) is not None:
        return code
    if len(args.config) != len(args.run):
        print("error: --config and --run must be given the same number of times, in matching order", file=sys.stderr)
        return 2
    try:
        statuses = tuple(
            build_run_status(Path(c), run_path=Path(r) if r else None, specs_dir=Path(args.specs), rules_dir=Path(args.rules), domains_dir=Path(args.domains))
            for c, r in zip(args.config, args.run)
        )
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        dashboard = build_dashboard(statuses, as_of=as_of)
    except RunStatusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(dashboard.to_dict(), indent=2))
    else:
        print(render_dashboard_markdown(dashboard))
    return 1 if dashboard.late else 0


def cmd_drift_review_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.DRIFT_REVIEW_SHOW)) is not None:
        return code
    try:
        result = review_drift(
            Path(args.drift_report),
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
            old_config=Path(args.old_config) if args.old_config else None,
            new_config=Path(args.new_config) if args.new_config else None,
            other_configs=[Path(p) for p in args.other_config],
            consumers=tuple(args.consumer),
        )
    except DriftReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_drift_markdown(result))
    return 0


def cmd_drift_review_approve(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.DRIFT_REVIEW_APPROVE)) is not None:
        return code
    try:
        result = review_drift(
            Path(args.drift_report),
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
            old_config=Path(args.old_config) if args.old_config else None,
            new_config=Path(args.new_config) if args.new_config else None,
            other_configs=[Path(p) for p in args.other_config],
        )
        approve_drift(result, Path(args.requests), approved_by=args.approved_by, note=args.note)
    except DriftReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"approved (non-prod): {result.spec_id} {result.spec_version}, by {args.approved_by}")
    return 0


def cmd_drift_review_show_requests(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.DRIFT_REVIEW_SHOW_REQUESTS)) is not None:
        return code
    requests = load_change_requests(Path(args.requests) if args.requests else None)
    if args.json:
        print(json.dumps([r.to_dict() for r in requests], indent=2))
    else:
        print(render_change_requests_markdown(requests))
    return 0


def cmd_golden_viewer_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.GOLDEN_VIEWER_SHOW)) is not None:
        return code
    try:
        calendar = build_golden_calendar(
            Path(args.capture),
            Path(args.golden_dir),
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            store=args.store,
        )
    except GoldenViewerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(calendar.to_dict(), indent=2))
    else:
        print(render_golden_calendar_markdown(calendar))
    return 1 if calendar.gaps else 0


def _audit_sources(args: argparse.Namespace) -> AuditSources:
    return AuditSources(
        promotion_requests=tuple(Path(p) for p in args.promotion_requests),
        drift_change_requests=tuple(Path(p) for p in args.drift_change_requests),
        rules_dir=Path(args.rules) if args.rules else None,
        rules_root=Path(args.root) if args.root else None,
        specs_dir=Path(args.specs) if args.specs else None,
        guardrail_logs=tuple(Path(p) for p in args.guardrail_log),
        gate_approval_logs=tuple(Path(p) for p in args.gate_approval_log),
        boards=tuple(Path(p) for p in args.board),
    )


def _filtered_records(args: argparse.Namespace):
    records = build_audit_log(_audit_sources(args))
    return filter_records(records, user=args.user, custodian=args.custodian, action=args.action, start=args.start, end=args.end)


def cmd_audit_log_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUDIT_LOG_SHOW)) is not None:
        return code
    records = _filtered_records(args)
    if args.json:
        print(json.dumps([r.to_dict() for r in records], indent=2))
    else:
        print(render_audit_log_markdown(records))
    return 0


def cmd_audit_log_export(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUDIT_LOG_EXPORT)) is not None:
        return code
    records = _filtered_records(args)
    try:
        write_csv(records, Path(args.out))
    except AuditLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exported: {len(records)} record(s) -> {args.out}")
    return 0


def cmd_autonomy_admin_show_levels(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUTONOMY_ADMIN_SHOW_LEVELS)) is not None:
        return code
    changes = load_changes(Path(args.changes) if args.changes else None)
    if args.json:
        print(json.dumps([c.to_dict() for c in changes], indent=2))
    else:
        print(render_levels_markdown(changes))
    return 0


def cmd_autonomy_admin_set_level(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUTONOMY_ADMIN_SET_LEVEL)) is not None:
        return code
    evidence = None
    if args.acceptance_rate is not None or args.sample_size is not None or args.window is not None:
        if args.acceptance_rate is None or args.sample_size is None or args.window is None:
            print("error: --acceptance-rate, --sample-size and --window must be given together", file=sys.stderr)
            return 2
        evidence = Evidence(args.acceptance_rate, args.sample_size, args.window)
    try:
        set_level(Path(args.changes), agent=args.agent, task_class=args.task_class, level=args.level, approver=args.approver, reason=args.reason, evidence=evidence)
    except AutonomyAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set: {args.agent}.{args.task_class} -> {args.level}, by {args.approver}")
    return 0


def cmd_autonomy_admin_show_whitelist(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUTONOMY_ADMIN_SHOW_WHITELIST)) is not None:
        return code
    try:
        taxonomy = load_whitelist(Path(args.rejections), Path(args.root) if args.root else None)
    except AutonomyAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(list(whitelisted_codes(taxonomy)), indent=2))
    else:
        print(render_whitelist_markdown(taxonomy))
    return 0


def cmd_autonomy_admin_request_whitelist_change(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUTONOMY_ADMIN_REQUEST_WHITELIST_CHANGE)) is not None:
        return code
    taxonomy = None
    if args.rejections:
        try:
            taxonomy = load_whitelist(Path(args.rejections), Path(args.root) if args.root else None)
        except AutonomyAdminError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        request_whitelist_change(Path(args.requests), code=args.code, auto_resolve=not args.remove, requested_by=args.requested_by, reason=args.reason, taxonomy=taxonomy)
    except AutonomyAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"requested: {'whitelist' if not args.remove else 'un-whitelist'} {args.code}, by {args.requested_by}")
    return 0


def cmd_autonomy_admin_show_whitelist_requests(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.AUTONOMY_ADMIN_SHOW_WHITELIST_REQUESTS)) is not None:
        return code
    requests = load_whitelist_requests(Path(args.requests) if args.requests else None)
    if args.json:
        print(json.dumps([r.to_dict() for r in requests], indent=2))
    else:
        print(render_whitelist_requests_markdown(requests))
    return 0


def _parse_channel_pairs(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        if ":" not in pair:
            raise NotificationPreferencesError(f"'{pair}' is not a channel:severity pair (for example slack:warning)")
        channel, severity = pair.split(":", 1)
        result[channel.strip()] = severity.strip()
    return result


def cmd_notification_preferences_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.NOTIFICATION_PREFERENCES_SHOW)) is not None:
        return code
    try:
        settings = load_settings(Path(args.settings) if args.settings else None)
    except NotificationPreferencesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    prefs = settings.preferences_for(args.user)
    if args.json:
        print(json.dumps(prefs, indent=2))
    else:
        print(render_user_markdown(args.user, prefs))
    return 0


def cmd_notification_preferences_set(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.NOTIFICATION_PREFERENCES_SET)) is not None:
        return code
    try:
        channel_thresholds = _parse_channel_pairs(args.channel)
        settings = load_settings(Path(args.settings) if Path(args.settings).is_file() else None)
        settings = set_user_preferences(settings, email=args.user, channel_thresholds=channel_thresholds)
        save_settings(settings, Path(args.settings))
    except NotificationPreferencesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set: {args.user} -> {channel_thresholds or '(no channels)'}")
    return 0


def cmd_notification_preferences_show_thresholds(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.NOTIFICATION_PREFERENCES_SHOW_THRESHOLDS)) is not None:
        return code
    try:
        settings = load_settings(Path(args.settings) if args.settings else None)
    except NotificationPreferencesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(settings.custodian_thresholds, indent=2))
    else:
        print(render_thresholds_markdown(settings))
    return 0


def cmd_notification_preferences_set_threshold(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.NOTIFICATION_PREFERENCES_SET_THRESHOLD)) is not None:
        return code
    try:
        settings = load_settings(Path(args.settings) if Path(args.settings).is_file() else None)
        settings = set_custodian_threshold(settings, custodian_id=args.custodian, minimum_severity=args.severity)
        save_settings(settings, Path(args.settings))
    except NotificationPreferencesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set: {args.custodian} threshold -> {args.severity}")
    return 0


def cmd_notification_preferences_reaches(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.NOTIFICATION_PREFERENCES_SHOW)) is not None:
        return code
    try:
        settings = load_settings(Path(args.settings) if args.settings else None)
        result = reaches(settings, email=args.user, custodian_id=args.custodian, channel=args.channel, severity=args.severity)
    except NotificationPreferencesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("yes" if result else "no")
    return 0 if result else 1


def cmd_approvals_approve(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.APPROVALS_APPROVE)) is not None:
        return code
    try:
        changes = load_changes(Path(args.guardrails_changes)) if args.guardrails_changes else ()
        approve(
            Path(args.log),
            subject=args.subject,
            agent=args.agent,
            task_class=args.task_class,
            approved_by=args.approved_by,
            guardrails_changes=changes,
            evidence_path=args.evidence_path,
            agent_version=args.agent_version,
            comment=args.comment,
        )
    except ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"approved: {args.subject} ({args.agent}.{args.task_class}), by {args.approved_by}")
    return 0


def cmd_approvals_reject(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.APPROVALS_REJECT)) is not None:
        return code
    try:
        changes = load_changes(Path(args.guardrails_changes)) if args.guardrails_changes else ()
        reject(
            Path(args.log),
            subject=args.subject,
            agent=args.agent,
            task_class=args.task_class,
            rejected_by=args.rejected_by,
            comment=args.comment,
            draft_dir=Path(args.draft_dir) if args.draft_dir else None,
            guardrails_changes=changes,
            evidence_path=args.evidence_path,
            agent_version=args.agent_version,
        )
    except ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"rejected: {args.subject} ({args.agent}.{args.task_class}), by {args.rejected_by}" + (f" -- returned to {args.draft_dir}" if args.draft_dir else ""))
    return 0


def cmd_approvals_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.APPROVALS_SHOW)) is not None:
        return code
    approvals = load_approvals(Path(args.approvals) if args.approvals else None)
    rejections = load_rejections(Path(args.rejections) if args.rejections else None)
    if args.json:
        print(json.dumps({"approvals": [a.to_dict() for a in approvals], "rejections": [r.to_dict() for r in rejections]}, indent=2))
    else:
        print(render_approvals_markdown(approvals))
        print(render_rejections_markdown(rejections))
    return 0


def cmd_git_provenance_commit(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.GIT_PROVENANCE_COMMIT)) is not None:
        return code
    approvals = load_approvals(Path(args.approvals))
    approval = next((a for a in approvals if a.subject == args.subject), None)
    if approval is None:
        print(f"error: no approval for subject '{args.subject}' in {args.approvals}", file=sys.stderr)
        return 2
    try:
        commit = commit_approval(
            Path(args.repo),
            approval,
            tuple(Path(p) for p in args.artifact),
            provenance_path=Path(args.provenance_path),
            message=args.message,
        )
    except GitProvenanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(commit.to_dict(), indent=2))
    else:
        print(render_commit_markdown(commit))
    return 0


def cmd_git_provenance_verify(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.GIT_PROVENANCE_VERIFY)) is not None:
        return code
    try:
        untracked = verify_committed(Path(args.repo), tuple(Path(p) for p in args.artifact))
    except GitProvenanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([str(p) for p in untracked], indent=2))
    else:
        print(render_verify_markdown(untracked))
    return 1 if untracked else 0


def _parse_cost_triples(triples: list[str]) -> dict[tuple[str, str], float]:
    costs: dict[tuple[str, str], float] = {}
    for triple in triples:
        parts = triple.split(":")
        if len(parts) != 3:
            raise ValueError(f"'{triple}' is not a custodian:business_date:amount triple")
        custodian, business_date, amount = parts
        costs[(custodian.strip(), business_date.strip())] = float(amount)
    return costs


def _build_throughput_report(args: argparse.Namespace):
    board = load_board(Path(args.board)) if args.board else Board()
    records = rule_status_changes_from(Path(args.rules), Path(args.root) if args.root else None) if args.rules else ()
    costs = _parse_cost_triples(args.cost)
    agent_eval_weekly = json.loads(Path(args.agent_eval_weekly).read_text(encoding="utf-8")) if args.agent_eval_weekly else None
    return build_report(board, records, costs=costs, agent_eval_weekly=agent_eval_weekly)


def cmd_throughput_metrics_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.THROUGHPUT_METRICS_SHOW)) is not None:
        return code
    try:
        report = _build_throughput_report(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_throughput_markdown(report))
    return 0


def cmd_throughput_metrics_export(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.THROUGHPUT_METRICS_EXPORT)) is not None:
        return code
    try:
        report = _build_throughput_report(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    markdown, data = write_report(report, Path(args.out))
    print(f"exported: {markdown}, {data}")
    return 0


def cmd_gate_evidence_pack_show(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.GATE_EVIDENCE_PACK_SHOW)) is not None:
        return code
    try:
        pack = build_gate_evidence_pack(Path(args.gate_pack), Path(args.approvals) if args.approvals else None)
    except GateEvidencePackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(pack.to_dict(), indent=2))
    else:
        print(render_gate_evidence_pack_markdown(pack))
    return 0 if pack.all_met else 1


def cmd_gate_evidence_pack_export(args: argparse.Namespace) -> int:
    if (code := _check(args, Action.GATE_EVIDENCE_PACK_EXPORT)) is not None:
        return code
    try:
        pack = build_gate_evidence_pack(Path(args.gate_pack), Path(args.approvals) if args.approvals else None)
    except GateEvidencePackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        out = write_gate_evidence_pdf(pack, Path(args.out))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exported: {out}")
    return 0 if pack.all_met else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-control", description="Astra Data Factory control plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    bd = sub.add_parser("board", help="the factory board: every custodian at its station, one WIP limit enforced per stream")
    bdsub = bd.add_subparsers(dest="board_command", required=True)

    bda = bdsub.add_parser("add", help="add a custodian to the board, at profile; blocked if it would exceed its stream's WIP limit")
    bda.add_argument("--board", required=True, help="path to the board state file (created on first write)")
    bda.add_argument("--custodian", required=True)
    bda.add_argument("--stream", required=True, help="the engagement this custodian belongs to, for example envestnet-custodial")
    bda.add_argument("--by", help="who added this custodian, recorded on the transition")
    bda.add_argument("--role", help="when given, checked against board.add before proceeding; omitted, unchecked (roles are steward, bsa, engineer, ops, pm, auditor)")
    bda.set_defaults(func=cmd_board_add)

    bdm = bdsub.add_parser("move", help="move a custodian to a station; blocked only when it would newly exceed its stream's WIP limit")
    bdm.add_argument("--board", required=True)
    bdm.add_argument("--custodian", required=True)
    bdm.add_argument("--to", required=True, help=f"the station to move to; stations are {', '.join(s.value for s in STATIONS)}")
    bdm.add_argument("--by", help="who made this move, recorded on the transition")
    bdm.add_argument("--role", help="when given, checked against board.move before proceeding")
    bdm.set_defaults(func=cmd_board_move)

    bdw = bdsub.add_parser("set-wip-limit", help="set one stream's WIP limit")
    bdw.add_argument("--board", required=True)
    bdw.add_argument("--stream", required=True)
    bdw.add_argument("--limit", required=True, type=int)
    bdw.add_argument("--role", help="when given, checked against board.set-wip-limit before proceeding")
    bdw.set_defaults(func=cmd_board_set_wip_limit)

    bds = bdsub.add_parser("show", help="every custodian at its station, WIP limits and custodians live per week")
    bds.add_argument("--board", required=True)
    bds.add_argument("--json", action="store_true")
    bds.add_argument("--role", help="when given, checked against board.show (every role may read)")
    bds.set_defaults(func=cmd_board_show)

    cs = sub.add_parser("config-studio", help="profile a sample, review the drafted config, dry-run it and request promotion — self-service for a simple-tier custodian, no engineer involved")
    cssub = cs.add_subparsers(dest="config_studio_command", required=True)

    css = cssub.add_parser("start", help="a sample arrives: the custodian enters the board at profile")
    css.add_argument("--board", required=True)
    css.add_argument("--custodian", required=True)
    css.add_argument("--stream", required=True)
    css.add_argument("--by", required=True, help="who is running this (a BSA, for a simple-tier custodian)")
    css.add_argument("--role", help="when given, checked against config-studio.start before proceeding")
    css.set_defaults(func=cmd_config_studio_start)

    csa = cssub.add_parser("advance", help=f"move a custodian one step forward, in order ({' -> '.join(s.value for s in SEQUENCE)})")
    csa.add_argument("--board", required=True)
    csa.add_argument("--custodian", required=True)
    csa.add_argument("--to", required=True, help=f"the next station; config studio's own sequence is {', '.join(s.value for s in SEQUENCE)}")
    csa.add_argument("--by", required=True)
    csa.add_argument("--role", help="when given, checked against config-studio.advance before proceeding")
    csa.set_defaults(func=cmd_config_studio_advance)

    csr = cssub.add_parser("request-promotion", help="request promotion once a custodian has reached dry_run; simple tier needs only its requester, medium/complex needs a reviewer too")
    csr.add_argument("--board", required=True)
    csr.add_argument("--requests", required=True, help="path to the promotion-requests log (created on first write)")
    csr.add_argument("--custodian", required=True)
    csr.add_argument("--tier", required=True, help=f"tiers are {', '.join(TIERS)}")
    csr.add_argument("--requested-by", required=True)
    csr.add_argument("--reviewed-by", help="required for medium or complex tier; not needed for simple")
    csr.add_argument("--note")
    csr.add_argument("--role", help="when given, checked against config-studio.request-promotion before proceeding")
    csr.set_defaults(func=cmd_config_studio_request_promotion)

    csw = cssub.add_parser("show-requests", help="every promotion request recorded")
    csw.add_argument("--requests", help="default: none recorded, so nothing is shown")
    csw.add_argument("--custodian", help="show only this custodian's requests")
    csw.add_argument("--json", action="store_true")
    csw.add_argument("--role", help="when given, checked against config-studio.show-requests (every role may read)")
    csw.set_defaults(func=cmd_config_studio_show_requests)

    dr = sub.add_parser("diff-review", help="a side-by-side diff of a config change with citations and impact")
    drsub = dr.add_subparsers(dest="diff_review_command", required=True)

    drr = drsub.add_parser("run", help="diff two versions of a config: changed fields, rules touched (with citation), and every other custodian a touched rule also affects")
    drr.add_argument("--old", required=True, help="the earlier version of the config")
    drr.add_argument("--new", required=True, help="the later version of the config")
    drr.add_argument("--other-config", action="append", default=[], help="another config to check for the same rule references, for affected custodians (repeatable)")
    drr.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    drr.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    drr.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
    drr.add_argument("--out", default="work/diff-review", help="the report is written under <out>/ (default: work/diff-review)")
    drr.add_argument("--json", action="store_true")
    drr.add_argument("--role", help="when given, checked against diff-review.run (every role may read)")
    drr.set_defaults(func=cmd_diff_review_run)

    pm = sub.add_parser("permissions", help="each role's allowed actions, and turning an identity provider's claims into an internal role")
    pmsub = pm.add_subparsers(dest="permissions_command", required=True)

    pms = pmsub.add_parser("show", help="every role's allowed actions, listed")
    pms.add_argument("--role", help="just this role's own actions (default: every role)")
    pms.set_defaults(func=cmd_permissions_show)

    pmr = pmsub.add_parser("resolve", help="turn an already-authenticated identity provider's claims into an internal role, via a platform administrator's own group mapping")
    pmr.add_argument("--mapping", required=True, help="path to the role mapping (groups: {<idp group>: <role>})")
    pmr.add_argument("--email", required=True)
    pmr.add_argument("--name", required=True)
    pmr.add_argument("--group", action="append", default=[], help="one of the identity provider's own group names for this person (repeatable)")
    pmr.set_defaults(func=cmd_permissions_resolve)

    qu = sub.add_parser("queue", help="home / my queue: what needs a person today, aggregated from what this factory's own agents and gate packs have already produced")
    qusub = qu.add_subparsers(dest="queue_command", required=True)

    qus = qusub.add_parser("show", help="approvals, exceptions assigned, breaks to explain, drift changes — filtered by --role when given")
    qus.add_argument("--gate-pack", action="append", default=[], help="a gate_pack.json to check for an outstanding approval (repeatable)")
    qus.add_argument("--exception-report", action="append", default=[], help="an exception-triage report.json (repeatable)")
    qus.add_argument("--break-report", action="append", default=[], help="a break-explainer report.json (repeatable)")
    qus.add_argument("--drift-report", action="append", default=[], help="a drift-watcher report.json (repeatable)")
    qus.add_argument("--role", help="show only this role's own items (default: everyone's)")
    qus.add_argument("--json", action="store_true")
    qus.set_defaults(func=cmd_queue_show)

    cp = sub.add_parser("custodian-page", help="one page per custodian: family, tier, config version, current station, files today, parity trend, open exceptions, cost")
    cpsub = cp.add_subparsers(dest="custodian_page_command", required=True)

    cps = cpsub.add_parser("show", help="assemble one custodian's page from its config, the board, and whichever reports are given")
    cps.add_argument("--config", required=True, help="the source config to build the page from")
    cps.add_argument("--board", help="path to a board.yaml, for the current station (default: not on the board)")
    cps.add_argument("--parity-report", help="a parity_report.json, for the parity trend (default: no parity data)")
    cps.add_argument("--exception-report", help="an exception-triage report.json, for the open-exceptions count (default: 0)")
    cps.add_argument("--arrivals", help="a file mapping expected file pattern to real arrival time -- a stand-in for a live file-load log this environment does not have (default: nothing arrived)")
    cps.add_argument("--cost", type=float, help="cost per day, if known -- no FinOps agent exists yet to compute one (default: no data)")
    cps.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    cps.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    cps.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
    cps.add_argument("--role", help="when given, checked against custodian-page.show (every role may read)")
    cps.add_argument("--json", action="store_true")
    cps.set_defaults(func=cmd_custodian_page_show)

    sv = sub.add_parser("spec-viewer", help="browse a Source Spec with its citations, and compare two versions")
    svsub = sv.add_subparsers(dest="spec_viewer_command", required=True)

    svs = svsub.add_parser("show", help="every field of one spec version: position, type, citation link")
    svs.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    svs.add_argument("--id", required=True, help="the spec id, for example pershing_gcus")
    svs.add_argument("--version", required=True)
    svs.add_argument("--role", help="when given, checked against spec-viewer.show (every role may read)")
    svs.add_argument("--json", action="store_true")
    svs.set_defaults(func=cmd_spec_viewer_show)

    svc = svsub.add_parser("compare", help="every field added, removed, shifted or otherwise changed between two versions of the same spec")
    svc.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    svc.add_argument("--id", required=True)
    svc.add_argument("--old", required=True, help="the earlier version")
    svc.add_argument("--new", required=True, help="the later version")
    svc.add_argument("--role", help="when given, checked against spec-viewer.compare (every role may read)")
    svc.add_argument("--json", action="store_true")
    svc.set_defaults(func=cmd_spec_viewer_compare)

    rv = sub.add_parser("rule-review", help="the rule catalog browser: a rule beside its citation, confirmed, rejected or marked a legacy defect, with a comment")
    rvsub = rv.add_subparsers(dest="rule_review_command", required=True)

    rvs = rvsub.add_parser("show", help="every rule matching a filter, beside its class and citation")
    rvs.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    rvs.add_argument("--status", help="only this status")
    rvs.add_argument("--custodian", help="only rules applying to this custodian")
    rvs.add_argument("--rejection-code", help="only rules tagged rejection-<code> with this code (case-insensitive)")
    rvs.add_argument("--role", help="when given, checked against rule-review.show (every role may read)")
    rvs.add_argument("--json", action="store_true")
    rvs.set_defaults(func=cmd_rule_review_show)

    rvss = rvsub.add_parser("set-status", help=f"confirm, reject or mark a legacy defect; statuses are {', '.join(REVIEW_STATUSES)}")
    rvss.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    rvss.add_argument("--id", required=True, help="the rule id, for example pershing_gcus.quantity_sign")
    rvss.add_argument("--status", required=True, help=f"statuses are {', '.join(REVIEW_STATUSES)}")
    rvss.add_argument("--by", required=True)
    rvss.add_argument("--note")
    rvss.add_argument("--role", help="when given, checked against rule-review.set-status before proceeding")
    rvss.set_defaults(func=cmd_rule_review_set_status)

    rvb = rvsub.add_parser("bulk-confirm", help="confirm one rule and every other rule in the catalog with exactly the same text")
    rvb.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    rvb.add_argument("--id", required=True, help="confirms this rule, and every rule identical to it")
    rvb.add_argument("--by", required=True)
    rvb.add_argument("--note")
    rvb.add_argument("--role", help="when given, checked against rule-review.bulk-confirm before proceeding")
    rvb.add_argument("--json", action="store_true")
    rvb.set_defaults(func=cmd_rule_review_bulk_confirm)

    ar = sub.add_parser("agent-review", help="an agent's draft beside its source evidence, edited with the original kept for comparison, accepted or rejected into the agent's own evaluation set")
    arsub = ar.add_subparsers(dest="agent_review_command", required=True)

    def _add_draft_args(p):
        p.add_argument("--draft", required=True, help="path to a draft rule file Rule Recovery already wrote (work/rule-recovery/rules/<group>/<name>.yaml)")
        p.add_argument("--tier", required=True, help=f"tiers are {', '.join(TIERS)}")
        p.add_argument("--input", required=True, help="where the reviewed evidence lives, for the gold set's own 'input' (a path, a citation)")

    def _add_edit_args(p):
        p.add_argument("--text", help="a corrected reading of the draft's own text")
        p.add_argument("--citation-file", help="a corrected citation: the legacy source file")
        p.add_argument("--citation-line", type=int, help="a corrected citation: the starting line")
        p.add_argument("--citation-end-line", type=int, help="a corrected citation: the ending line, if it spans more than one")
        p.add_argument("--citation-repository", help="a corrected citation: the repository, if known")

    ars = arsub.add_parser("show", help="the draft's own text (reasoning) beside its citation (source evidence), and what accepting it would record")
    _add_draft_args(ars)
    ars.add_argument("--role", help="when given, checked against agent-review.show (every role may read)")
    ars.add_argument("--json", action="store_true")
    ars.set_defaults(func=cmd_agent_review_show)

    are = arsub.add_parser("edit", help="a corrected text and/or citation, diffed against the original -- writes nothing")
    _add_draft_args(are)
    _add_edit_args(are)
    are.add_argument("--role", help="when given, checked against agent-review.edit (every role may read)")
    are.add_argument("--json", action="store_true")
    are.set_defaults(func=cmd_agent_review_edit)

    ara = arsub.add_parser("accept", help="the draft (as reviewed, possibly edited) is correct: records it as a new case in the agent's own gold set")
    _add_draft_args(ara)
    _add_edit_args(ara)
    ara.add_argument("--gold-set", required=True, help="path to the agent's own eval.yaml")
    ara.add_argument("--case-id", required=True)
    ara.add_argument("--role", help="when given, checked against agent-review.accept before proceeding")
    ara.set_defaults(func=cmd_agent_review_accept)

    arr = arsub.add_parser("reject", help="the draft is wrong: records a case in the agent's own gold set saying nothing should have been produced for this input")
    _add_draft_args(arr)
    arr.add_argument("--gold-set", required=True, help="path to the agent's own eval.yaml")
    arr.add_argument("--case-id", required=True)
    arr.add_argument("--role", help="when given, checked against agent-review.reject before proceeding")
    arr.set_defaults(func=cmd_agent_review_reject)

    pv = sub.add_parser("parity-viewer", help="dual-run gate evidence, inspected: match rate by day, break groups by rule and field, and a record pair drill-down")
    pvsub = pv.add_subparsers(dest="parity_viewer_command", required=True)

    pvt = pvsub.add_parser("trend", help="match rate per day, from an already-written astra_verification.parity_report.ParityReport")
    pvt.add_argument("--parity-report", required=True, help="path to a parity report.json (astra_verification.parity_report.write_report's own output)")
    pvt.add_argument("--role", help="when given, checked against parity-viewer.trend (every role may read)")
    pvt.add_argument("--json", action="store_true")
    pvt.set_defaults(func=cmd_parity_viewer_trend)

    pvb = pvsub.add_parser("breaks", help="every (rule, field, cause) an already-written break-explainer report touches, counted, busiest first")
    pvb.add_argument("--break-report", required=True, help="path to a break-explainer report.json")
    pvb.add_argument("--role", help="when given, checked against parity-viewer.breaks (every role may read)")
    pvb.add_argument("--json", action="store_true")
    pvb.set_defaults(func=cmd_parity_viewer_breaks)

    pvr = pvsub.add_parser("records", help="every differing record, its own explanations grouped by key")
    pvr.add_argument("--break-report", required=True)
    pvr.add_argument("--role", help="when given, checked against parity-viewer.records (every role may read)")
    pvr.add_argument("--json", action="store_true")
    pvr.set_defaults(func=cmd_parity_viewer_records)

    pvo = pvsub.add_parser("record", help="one record pair by its own key: legacy vs lakehouse, differing fields highlighted")
    pvo.add_argument("--break-report", required=True)
    pvo.add_argument("--key", action="append", required=True, help="one component of the record's own key, in order (repeatable)")
    pvo.add_argument("--role", help="when given, checked against parity-viewer.record (every role may read)")
    pvo.add_argument("--json", action="store_true")
    pvo.set_defaults(func=cmd_parity_viewer_record)

    rs = sub.add_parser("run-status", help="per custodian, file and run: expected, arrived, parsed, resolved, published, timed against the 20-minute window")
    rssub = rs.add_subparsers(dest="run_status_command", required=True)

    rss = rssub.add_parser("show", help="one custodian's own run: every expected file's current stage, missing files, the timing bar, and the run log reference")
    rss.add_argument("--config", required=True, help="the source config, for its own delivery.files and cutoff_time")
    rss.add_argument("--run", help="a run file giving arrived/parsed/resolved/published times per file pattern, and the run's own business date and run log (default: nothing has happened yet)")
    rss.add_argument("--as-of", help="ISO datetime to judge lateness against (default: now)")
    rss.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    rss.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    rss.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
    rss.add_argument("--role", help="when given, checked against run-status.show (every role may read)")
    rss.add_argument("--json", action="store_true")
    rss.set_defaults(func=cmd_run_status_show)

    rsd = rssub.add_parser("dashboard", help="every given custodian's own run, together: which are late, and each one's own timing")
    rsd.add_argument("--config", action="append", required=True, help="a source config (repeatable, one per custodian)")
    rsd.add_argument("--run", action="append", required=True, help="that custodian's own run file, in the same order as --config (repeatable; pass the same count as --config)")
    rsd.add_argument("--as-of", help="ISO datetime to judge lateness against (default: now)")
    rsd.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    rsd.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    rsd.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
    rsd.add_argument("--role", help="when given, checked against run-status.dashboard (every role may read)")
    rsd.add_argument("--json", action="store_true")
    rsd.set_defaults(func=cmd_run_status_dashboard)

    dr = sub.add_parser("drift-review", help="a detected layout change beside its proposed spec and config deltas and the impact list, approved into a non-prod change log")
    drsub = dr.add_subparsers(dest="drift_review_command", required=True)

    def _add_drift_args(p):
        p.add_argument("--drift-report", required=True, help="a drift-watcher report.json")
        p.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
        p.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
        p.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
        p.add_argument("--old-config", help="a real config file, for the optional config diff (given together with --new-config)")
        p.add_argument("--new-config", help="a real, already-drafted candidate config file reflecting the spec change (given together with --old-config)")
        p.add_argument("--other-config", action="append", default=[], help="another config to check for affected custodians in the config diff (repeatable)")

    drs = drsub.add_parser("show", help="the spec diff, optional config diff, and impact list for one detected drift")
    _add_drift_args(drs)
    drs.add_argument("--consumer", action="append", default=[], help="a downstream consumer of this spec, not tracked anywhere in this repository otherwise (repeatable)")
    drs.add_argument("--role", help="when given, checked against drift-review.show (every role may read)")
    drs.add_argument("--json", action="store_true")
    drs.set_defaults(func=cmd_drift_review_show)

    dra = drsub.add_parser("approve", help="approve this drift into a non-prod change-request log -- never a git commit, never a write to specs/ or configs/")
    _add_drift_args(dra)
    dra.add_argument("--requests", required=True, help="path to the change-request log (created on first write)")
    dra.add_argument("--approved-by", required=True)
    dra.add_argument("--note")
    dra.add_argument("--role", help="when given, checked against drift-review.approve before proceeding")
    dra.set_defaults(func=cmd_drift_review_approve)

    drw = drsub.add_parser("show-requests", help="every drift change request recorded")
    drw.add_argument("--requests", help="default: none recorded, so nothing is shown")
    drw.add_argument("--role", help="when given, checked against drift-review.show-requests (every role may read)")
    drw.add_argument("--json", action="store_true")
    drw.set_defaults(func=cmd_drift_review_show_requests)

    gv = sub.add_parser("golden-viewer", help="which business days are captured per custodian, with hashes and gaps, so parity runs are known to be complete")
    gvsub = gv.add_subparsers(dest="golden_viewer_command", required=True)

    gvs = gvsub.add_parser("show", help="the calendar for one custodian over a window: every captured version with its own hash, and every gap with a runnable capture command")
    gvs.add_argument("--capture", required=True, help="the custodian's capture.yaml (golden/<custodian>/capture.yaml)")
    gvs.add_argument("--golden-dir", default="golden", help="the golden directory holding <custodian>/datasets.json (default: golden)")
    gvs.add_argument("--from", dest="start", required=True, help="first business date, YYYY-MM-DD")
    gvs.add_argument("--to", dest="end", required=True, help="last business date, YYYY-MM-DD")
    gvs.add_argument("--store", help="the golden store, for a real --store on the shown capture command (default: a placeholder)")
    gvs.add_argument("--role", help="when given, checked against golden-viewer.show (every role may read)")
    gvs.add_argument("--json", action="store_true")
    gvs.set_defaults(func=cmd_golden_viewer_show)

    al = sub.add_parser("audit-log", help="who approved what, when, searched across every plane's own approval and change records, exported to CSV")
    alsub = al.add_subparsers(dest="audit_log_command", required=True)

    def _add_audit_source_args(p):
        p.add_argument("--promotion-requests", action="append", default=[], help="a promotion-requests.yaml (repeatable)")
        p.add_argument("--drift-change-requests", action="append", default=[], help="a drift-change-requests.yaml (repeatable)")
        p.add_argument("--rules", help="the rule catalog directory, for every rule's own status history")
        p.add_argument("--specs", help="the spec registry directory, to resolve a drift approval's own affected custodians")
        p.add_argument("--root", help="repo root, for display paths in rule/spec loading")
        p.add_argument("--guardrail-log", action="append", default=[], help="an astra_agents.guardrails changes log (repeatable)")
        p.add_argument("--gate-approval-log", action="append", default=[], help="an astra_agents.gate_evidence_compiler approvals log (repeatable)")
        p.add_argument("--board", action="append", default=[], help="a board.yaml, for board moves with a recorded actor (repeatable)")

    def _add_audit_filter_args(p):
        p.add_argument("--user", help="only records whose user contains this (case-insensitive)")
        p.add_argument("--custodian", help="only records whose custodian contains this (case-insensitive)")
        p.add_argument("--action", help="only records whose action contains this (case-insensitive)")
        p.add_argument("--start", help="only records at or after this ISO timestamp")
        p.add_argument("--end", help="only records at or before this ISO timestamp")

    als = alsub.add_parser("show", help="every matching record from every given source, oldest first")
    _add_audit_source_args(als)
    _add_audit_filter_args(als)
    als.add_argument("--role", help="when given, checked against audit-log.show (every role may read)")
    als.add_argument("--json", action="store_true")
    als.set_defaults(func=cmd_audit_log_show)

    ale = alsub.add_parser("export", help="every matching record, written to a real CSV file")
    _add_audit_source_args(ale)
    _add_audit_filter_args(ale)
    ale.add_argument("--out", required=True, help="path to write the CSV to")
    ale.add_argument("--role", help="when given, checked against audit-log.export before proceeding")
    ale.set_defaults(func=cmd_audit_log_export)

    aa = sub.add_parser("autonomy-admin", help="set L0-L3 per agent and task class, and manage the self-healing whitelist -- autonomy as a controlled setting")
    aasub = aa.add_subparsers(dest="autonomy_admin_command", required=True)

    aal = aasub.add_parser("show-levels", help="every (agent, task class)'s own current autonomy level, from its own change history")
    aal.add_argument("--changes", help="path to the guardrails changes log (default: none recorded, every level is L0)")
    aal.add_argument("--role", help="when given, checked against autonomy-admin.show-levels (every role may read)")
    aal.add_argument("--json", action="store_true")
    aal.set_defaults(func=cmd_autonomy_admin_show_levels)

    aas = aasub.add_parser("set-level", help=f"set one (agent, task class)'s own autonomy level; levels are {', '.join(LEVELS)}. A change to L3 needs --acceptance-rate/--sample-size/--window together, all clearing the threshold")
    aas.add_argument("--changes", required=True, help="path to the guardrails changes log (created on first write)")
    aas.add_argument("--agent", required=True)
    aas.add_argument("--task-class", required=True)
    aas.add_argument("--level", required=True, help=f"levels are {', '.join(LEVELS)}")
    aas.add_argument("--approver", required=True)
    aas.add_argument("--reason", required=True)
    aas.add_argument("--acceptance-rate", type=float, help="required for L3, together with --sample-size and --window")
    aas.add_argument("--sample-size", type=int)
    aas.add_argument("--window", help="what period or sample the evidence measures, for example 'trailing 90 days'")
    aas.add_argument("--role", help="when given, checked against autonomy-admin.set-level before proceeding")
    aas.set_defaults(func=cmd_autonomy_admin_set_level)

    aaw = aasub.add_parser("show-whitelist", help="every self-healing whitelisted code (auto_resolve: true) in a domain pack's own real rejection taxonomy")
    aaw.add_argument("--rejections", required=True, help="the domain pack's rejections.yaml")
    aaw.add_argument("--root", help="repo root, for display paths")
    aaw.add_argument("--role", help="when given, checked against autonomy-admin.show-whitelist (every role may read)")
    aaw.add_argument("--json", action="store_true")
    aaw.set_defaults(func=cmd_autonomy_admin_show_whitelist)

    aar = aasub.add_parser("request-whitelist-change", help="log a request to whitelist (or un-whitelist) a code -- never writes to the real rejections.yaml itself; a steward applies it by hand")
    aar.add_argument("--requests", required=True, help="path to the whitelist-requests log (created on first write)")
    aar.add_argument("--code", required=True, help="the rejection code")
    aar.add_argument("--remove", action="store_true", help="request removing the code from the whitelist, instead of adding it")
    aar.add_argument("--requested-by", required=True)
    aar.add_argument("--reason", required=True)
    aar.add_argument("--rejections", help="the domain pack's rejections.yaml, to validate the code is real and the request is not a no-op (default: not validated)")
    aar.add_argument("--root", help="repo root, for display paths")
    aar.add_argument("--role", help="when given, checked against autonomy-admin.request-whitelist-change before proceeding")
    aar.set_defaults(func=cmd_autonomy_admin_request_whitelist_change)

    aaq = aasub.add_parser("show-whitelist-requests", help="every whitelist change request recorded")
    aaq.add_argument("--requests", help="default: none recorded, so nothing is shown")
    aaq.add_argument("--role", help="when given, checked against autonomy-admin.show-whitelist-requests (every role may read)")
    aaq.add_argument("--json", action="store_true")
    aaq.set_defaults(func=cmd_autonomy_admin_show_whitelist_requests)

    np = sub.add_parser("notification-preferences", help="which alerts reach a person on which channel, and a per-custodian noise floor for ops")
    npsub = np.add_subparsers(dest="notification_preferences_command", required=True)

    nps = npsub.add_parser("show", help="one user's own channel preferences")
    nps.add_argument("--settings", help="path to the notification settings file (default: none recorded, no channel reaches anyone)")
    nps.add_argument("--user", required=True, help="the user's own email")
    nps.add_argument("--role", help="when given, checked against notification-preferences.show (every role may read)")
    nps.add_argument("--json", action="store_true")
    nps.set_defaults(func=cmd_notification_preferences_show)

    npset = npsub.add_parser("set", help="replace one user's own channel preferences entirely -- any role may set their own")
    npset.add_argument("--settings", required=True, help="path to the notification settings file (created on first write)")
    npset.add_argument("--user", required=True, help="the user's own email")
    npset.add_argument("--channel", action="append", default=[], help=f"a channel:severity pair (repeatable), channels are {', '.join(CHANNELS)}, severities are {', '.join(SEVERITIES)}; a channel not given never reaches this user")
    npset.add_argument("--role", help="when given, checked against notification-preferences.set (every role, including auditor, may set their own)")
    npset.set_defaults(func=cmd_notification_preferences_set)

    npt = npsub.add_parser("show-thresholds", help="every custodian's own severity floor")
    npt.add_argument("--settings", help="path to the notification settings file (default: none set, no floor anywhere)")
    npt.add_argument("--role", help="when given, checked against notification-preferences.show-thresholds (every role may read)")
    npt.add_argument("--json", action="store_true")
    npt.set_defaults(func=cmd_notification_preferences_show_thresholds)

    npst = npsub.add_parser("set-threshold", help="set one custodian's own severity floor -- an alert below it reaches no one, regardless of anyone's own channel choice")
    npst.add_argument("--settings", required=True, help="path to the notification settings file (created on first write)")
    npst.add_argument("--custodian", required=True)
    npst.add_argument("--severity", required=True, help=f"severities are {', '.join(SEVERITIES)}")
    npst.add_argument("--role", help="when given, checked against notification-preferences.set-threshold before proceeding")
    npst.set_defaults(func=cmd_notification_preferences_set_threshold)

    npr = npsub.add_parser("reaches", help="would an alert of this severity, for this custodian, reach this user on this channel?")
    npr.add_argument("--settings", help="path to the notification settings file")
    npr.add_argument("--user", required=True)
    npr.add_argument("--custodian", required=True)
    npr.add_argument("--channel", required=True, help=f"channels are {', '.join(CHANNELS)}")
    npr.add_argument("--severity", required=True, help=f"severities are {', '.join(SEVERITIES)}")
    npr.add_argument("--role", help="when given, checked against notification-preferences.show (every role may read)")
    npr.set_defaults(func=cmd_notification_preferences_reaches)

    ap = sub.add_parser("approvals", help="approve or reject a draft with a comment, and see the autonomy level of the agent that proposed it")
    apsub = ap.add_subparsers(dest="approvals_command", required=True)

    def _add_subject_args(p):
        p.add_argument("--subject", required=True, help="what is being approved or rejected -- a draft id, a rule id, whatever names it")
        p.add_argument("--agent", required=True, help="which agent proposed it")
        p.add_argument("--task-class", required=True)
        p.add_argument("--guardrails-changes", help="path to the guardrails changes log, to show the proposing agent's own real current autonomy level (default: not given, shown as L0)")
        p.add_argument("--evidence-path", help="a real file the reviewer was shown (checked to exist)")
        p.add_argument("--agent-version", help="the agent's own version, if known -- nothing in this repository tracks one automatically (default: not recorded)")

    apa = apsub.add_parser("approve", help="approve into the log -- who, when, the evidence seen, the agent version, and the proposer's own autonomy level")
    _add_subject_args(apa)
    apa.add_argument("--log", required=True, help="path to the approvals log (created on first write)")
    apa.add_argument("--approved-by", required=True)
    apa.add_argument("--comment")
    apa.add_argument("--role", help="when given, checked against approvals.approve before proceeding")
    apa.set_defaults(func=cmd_approvals_approve)

    apr = apsub.add_parser("reject", help="reject with a required comment -- logged, and, given --draft-dir, returned to the draft's own real directory as rejection.yaml")
    _add_subject_args(apr)
    apr.add_argument("--log", required=True, help="path to the rejections log (created on first write)")
    apr.add_argument("--rejected-by", required=True)
    apr.add_argument("--comment", required=True)
    apr.add_argument("--draft-dir", help="the draft's own real directory (work/<agent>/<subject>/) -- gets a rejection.yaml written into it")
    apr.add_argument("--role", help="when given, checked against approvals.reject before proceeding")
    apr.set_defaults(func=cmd_approvals_reject)

    aps = apsub.add_parser("show", help="every approval and rejection recorded")
    aps.add_argument("--approvals", help="path to the approvals log (default: none recorded)")
    aps.add_argument("--rejections", help="path to the rejections log (default: none recorded)")
    aps.add_argument("--role", help="when given, checked against approvals.show (every role may read)")
    aps.add_argument("--json", action="store_true")
    aps.set_defaults(func=cmd_approvals_show)

    gp = sub.add_parser("git-provenance", help="every approved change committed with a real PROVENANCE.json, so no artifact exists only in the factory's own local store")
    gpsub = gp.add_subparsers(dest="git_provenance_command", required=True)

    gpc = gpsub.add_parser("commit", help="one real commit for one already-recorded approval, staging exactly the given artifacts plus a real PROVENANCE.json -- never anything else in the working tree")
    gpc.add_argument("--repo", required=True, help="the git repository to commit into")
    gpc.add_argument("--approvals", required=True, help="the approvals log (astra-control approvals approve's own --log) to find --subject in")
    gpc.add_argument("--subject", required=True, help="the already-approved subject to commit")
    gpc.add_argument("--artifact", action="append", required=True, help="a real file inside --repo that is part of this change (repeatable)")
    gpc.add_argument("--provenance-path", required=True, help="where to write PROVENANCE.json, inside --repo")
    gpc.add_argument("--message", help="the commit message (default: composed from the approval's own subject and approver)")
    gpc.add_argument("--role", help="when given, checked against git-provenance.commit before proceeding")
    gpc.add_argument("--json", action="store_true")
    gpc.set_defaults(func=cmd_git_provenance_commit)

    gpv = gpsub.add_parser("verify", help="which of the given artifacts git does not actually track -- the concrete check behind \"no artifact exists only in the factory database\"")
    gpv.add_argument("--repo", required=True)
    gpv.add_argument("--artifact", action="append", required=True, help="a path to check (repeatable)")
    gpv.add_argument("--role", help="when given, checked against git-provenance.verify (every role may read)")
    gpv.add_argument("--json", action="store_true")
    gpv.set_defaults(func=cmd_git_provenance_verify)

    tm = sub.add_parser("throughput-metrics", help="custodians live per week, agent acceptance and cost per custodian per day, assembled into one weekly report")
    tmsub = tm.add_subparsers(dest="throughput_metrics_command", required=True)

    def _add_throughput_args(p):
        p.add_argument("--board", help="path to a board.yaml, for custodians live per week (default: none, so none are live)")
        p.add_argument("--rules", help="the rule catalog directory, for agent acceptance by custodian and day (default: none, so no acceptance data)")
        p.add_argument("--root", help="repo root, for display paths in rule loading")
        p.add_argument("--cost", action="append", default=[], help="a custodian:business_date:amount triple (repeatable) -- no query-tag mechanism exists anywhere in this repository, so cost is caller-supplied only")
        p.add_argument("--agent-eval-weekly", help="path to an astra_verification.agent_eval WeeklyReport.to_dict() JSON file, shown in its own separate section (a different metric than agent acceptance)")

    tms = tmsub.add_parser("show", help="the weekly report")
    _add_throughput_args(tms)
    tms.add_argument("--role", help="when given, checked against throughput-metrics.show (every role may read)")
    tms.add_argument("--json", action="store_true")
    tms.set_defaults(func=cmd_throughput_metrics_show)

    tme = tmsub.add_parser("export", help="the weekly report, written to weekly.md and weekly.json for the client's own reporting cadence")
    _add_throughput_args(tme)
    tme.add_argument("--out", required=True, help="directory to write weekly.md/weekly.json into")
    tme.add_argument("--role", help="when given, checked against throughput-metrics.export before proceeding")
    tme.set_defaults(func=cmd_throughput_metrics_export)

    gep = sub.add_parser("gate-evidence-pack", help="a gate's own criteria, evidence and approvals assembled into one exportable pack")
    gepsub = gep.add_subparsers(dest="gate_evidence_pack_command", required=True)

    def _add_gate_evidence_pack_args(p):
        p.add_argument("--gate-pack", required=True, help="path to a gate_pack.json (astra_agents.gate_evidence_compiler's own GatePack.to_dict() shape)")
        p.add_argument("--approvals", help="path to the gate's own approvals log (astra_agents.gate_evidence_compiler.record_approval's own shape); omitted, no approvals are shown")

    geps = gepsub.add_parser("show", help="the pack: every criterion, its evidence, and every real approval for this release")
    _add_gate_evidence_pack_args(geps)
    geps.add_argument("--role", help="when given, checked against gate-evidence-pack.show (every role may read)")
    geps.add_argument("--json", action="store_true")
    geps.set_defaults(func=cmd_gate_evidence_pack_show)

    gepe = gepsub.add_parser("export", help="the pack, rendered to a real PDF -- criteria, evidence and approvals")
    _add_gate_evidence_pack_args(gepe)
    gepe.add_argument("--out", required=True, help="path to write the PDF to")
    gepe.add_argument("--role", help="when given, checked against gate-evidence-pack.export before proceeding")
    gepe.set_defaults(func=cmd_gate_evidence_pack_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
