from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from astra_control.board import Board, add_custodian, move, save_board
from astra_control.audit_log import (
    AuditRecord,
    AuditSources,
    board_moves_from,
    build_audit_log,
    drift_approvals_from,
    filter_records,
    gate_approvals_from,
    guardrail_changes_from,
    promotion_requests_from,
    render_markdown,
    rule_status_changes_from,
    to_csv,
    write_csv,
)

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
RULES = REPO / "rules"
PROMOTION_REQUESTS = REPO / "control" / "examples" / "promotion-requests.yaml"
GATE_APPROVALS = REPO / "agents" / "examples" / "gate_evidence_compiler" / "approvals.yaml"


def _write_guardrail_log(tmp_path: Path) -> Path:
    """The real astra_agents.guardrails.record_change's own exact YAML shape -- no illustrative
    example is committed anywhere yet, so this mirrors the writer directly (module docstring)."""
    path = tmp_path / "guardrails-changes.yaml"
    path.write_text(
        "changes_version: 0\n\nchanges:\n"
        '  - { agent: exception_triage, task_class: rejection_resolution, level: L2, approver: "pm@example.com", reason: "Whitelisted classes only.", at: "2026-09-10T09:00:00Z" }\n'
        '  - { agent: exception_triage, task_class: rejection_resolution, level: L3, approver: "pm@example.com", reason: "Acceptance rate cleared threshold.", at: "2026-09-14T09:00:00Z", '
        "evidence: { acceptance_rate: 0.97, sample_size: 120, window: \"trailing 90 days\" } }\n",
        encoding="utf-8",
    )
    return path


def _write_drift_change_requests(tmp_path: Path) -> Path:
    path = tmp_path / "drift-change-requests.yaml"
    path.write_text(
        "requests_version: 0\n\nrequests:\n"
        '  - { spec_id: pershing_gcus, spec_version: "2017-07-25", approved_by: "engineer@example.com", note: "Widen to 125.", at: "2026-09-15T09:00:00Z" }\n',
        encoding="utf-8",
    )
    return path


def _real_board(tmp_path: Path) -> Path:
    board = add_custodian(Board(), "pershing", "envestnet-custodial", by="ops@example.com")
    board = move(board, "pershing", "draft", by="bsa@example.com")
    path = tmp_path / "board.yaml"
    save_board(board, path)
    return path


# ---------------------------------------------------------------- promotion_requests_from: real committed data


def test_promotion_requests_from_the_real_committed_example():
    records = promotion_requests_from(PROMOTION_REQUESTS)
    assert len(records) == 1
    r = records[0]
    assert r.kind == "promotion_request"
    assert r.user == "bsa@example.com"
    assert r.custodian == "tableau-gl"
    assert "simple" in r.subject
    assert r.evidence == "profiled and dry-run clean; no engineer needed"
    assert r.agent_version is None


def test_promotion_requests_from_missing_file_is_empty(tmp_path):
    assert promotion_requests_from(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- drift_approvals_from


def test_drift_approvals_from_without_specs_dir_has_no_custodian(tmp_path):
    path = _write_drift_change_requests(tmp_path)
    records = drift_approvals_from(path)
    assert len(records) == 1
    assert records[0].custodian is None
    assert records[0].subject == "pershing_gcus 2017-07-25"
    assert records[0].user == "engineer@example.com"


def test_drift_approvals_from_with_specs_dir_resolves_the_real_custodian(tmp_path):
    path = _write_drift_change_requests(tmp_path)
    records = drift_approvals_from(path, specs_dir=SPECS, root=REPO)
    assert records[0].custodian == "pershing"


def test_drift_approvals_from_missing_file_is_empty(tmp_path):
    assert drift_approvals_from(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- rule_status_changes_from: the real rule catalog


def test_rule_status_changes_from_the_real_rule_catalog():
    records = rule_status_changes_from(RULES, REPO)
    quantity_sign = [r for r in records if r.subject.startswith("pershing_gcus.quantity_sign")]
    assert len(quantity_sign) == 2  # recovered, then confirmed
    confirmed = next(r for r in quantity_sign if "confirmed" in r.subject)
    assert confirmed.user == "steward@example.com"
    assert confirmed.custodian == "pershing"
    assert "Blank sign" in confirmed.evidence


def test_rule_status_changes_from_a_bad_rules_dir_is_empty(tmp_path):
    assert rule_status_changes_from(tmp_path / "missing") == ()


# ---------------------------------------------------------------- guardrail_changes_from


def test_guardrail_changes_from_includes_reason_and_structured_evidence(tmp_path):
    path = _write_guardrail_log(tmp_path)
    records = guardrail_changes_from(path)
    assert len(records) == 2
    l2, l3 = records
    assert l2.evidence == "Whitelisted classes only."
    assert "acceptance_rate=0.97" in l3.evidence and "Acceptance rate cleared threshold." in l3.evidence
    assert l3.subject == "exception_triage.rejection_resolution -> L3"
    assert l2.agent_version is None  # the confirmed, real gap


def test_guardrail_changes_from_missing_file_is_empty(tmp_path):
    assert guardrail_changes_from(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- gate_approvals_from: real committed data


def test_gate_approvals_from_the_real_committed_example():
    records = gate_approvals_from(GATE_APPROVALS)
    assert len(records) == 1
    r = records[0]
    assert r.user == "steward@example.com"
    assert r.subject == "pershing_position-2026-09-13"
    assert "Every gate criterion met" in r.evidence
    assert r.custodian is None  # not parsed out of the release name -- module docstring


def test_gate_approvals_from_missing_file_is_empty(tmp_path):
    assert gate_approvals_from(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- board_moves_from


def test_board_moves_from_a_real_board_with_recorded_actors(tmp_path):
    path = _real_board(tmp_path)
    records = board_moves_from(path)
    assert len(records) == 2  # add (profile) and move (draft), both with a real `by`
    assert {r.user for r in records} == {"ops@example.com", "bsa@example.com"}
    assert all(r.custodian == "pershing" for r in records)


def test_board_moves_from_the_real_committed_board_skips_anonymous_transitions():
    """The real committed control/examples/board.yaml records no `by` on any transition -- every
    one is skipped, never shown as an anonymous action (module docstring)."""
    assert board_moves_from(REPO / "control" / "examples" / "board.yaml") == ()


def test_board_moves_from_missing_file_is_empty(tmp_path):
    assert board_moves_from(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- build_audit_log: everything together


def test_build_audit_log_aggregates_every_source(tmp_path):
    board_path = _real_board(tmp_path)
    guardrails_path = _write_guardrail_log(tmp_path)
    drift_path = _write_drift_change_requests(tmp_path)
    sources = AuditSources(
        promotion_requests=(PROMOTION_REQUESTS,),
        drift_change_requests=(drift_path,),
        rules_dir=RULES,
        rules_root=REPO,
        specs_dir=SPECS,
        guardrail_logs=(guardrails_path,),
        gate_approval_logs=(GATE_APPROVALS,),
        boards=(board_path,),
    )
    records = build_audit_log(sources)
    assert {r.kind for r in records} == {"promotion_request", "drift_approval", "rule_status_change", "guardrail_change", "gate_approval", "board_move"}
    assert list(records) == sorted(records, key=lambda r: r.at)  # oldest first


def test_build_audit_log_with_no_sources_is_empty():
    assert build_audit_log(AuditSources()) == ()


# ---------------------------------------------------------------- AC1: filter by user, custodian, date, action


def test_filter_by_user_is_case_insensitive_substring():
    records = rule_status_changes_from(RULES, REPO)
    assert filter_records(records, user="STEWARD")
    assert filter_records(records, user="nobody") == ()


def test_filter_by_custodian():
    records = rule_status_changes_from(RULES, REPO)
    assert all(r.custodian == "pershing" for r in filter_records(records, custodian="pershing"))
    assert filter_records(records, custodian="fidelity") == ()


def test_filter_by_action():
    records = rule_status_changes_from(RULES, REPO) + gate_approvals_from(GATE_APPROVALS)
    only_rules = filter_records(records, action="rule-review")
    assert only_rules and all(r.kind == "rule_status_change" for r in only_rules)


def test_filter_by_date_range():
    records = rule_status_changes_from(RULES, REPO)
    early_only = filter_records(records, end="2026-09-06T10:00:00Z")
    assert early_only and all(r.at <= "2026-09-06T10:00:00Z" for r in early_only)
    assert filter_records(records, start="2099-01-01T00:00:00Z") == ()


def test_filters_combine():
    records = rule_status_changes_from(RULES, REPO)
    assert filter_records(records, custodian="pershing", user="steward") == filter_records(records, user="steward@example.com")


# ---------------------------------------------------------------- AC2: export to CSV


def test_to_csv_round_trips_through_the_real_csv_module():
    records = gate_approvals_from(GATE_APPROVALS)
    text = to_csv(records)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0]["user"] == "steward@example.com"
    assert rows[0]["agent_version"] == ""  # None round-trips as empty in real CSV


def test_to_csv_with_no_records_still_has_a_header():
    text = to_csv(())
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows == []
    assert "kind" in text and "action" in text


def test_write_csv_writes_a_real_file(tmp_path):
    path = write_csv(gate_approvals_from(GATE_APPROVALS), tmp_path / "audit.csv")
    assert path.is_file()
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert len(rows) == 1


def test_write_csv_to_an_unwritable_path_is_a_clear_error(tmp_path):
    from astra_control.audit_log import AuditLogError

    with pytest.raises(AuditLogError):
        write_csv(gate_approvals_from(GATE_APPROVALS), tmp_path / "no_such_directory" / "audit.csv")


# ---------------------------------------------------------------- render_markdown


def test_render_markdown_shows_every_record():
    records = gate_approvals_from(GATE_APPROVALS) + promotion_requests_from(PROMOTION_REQUESTS)
    text = render_markdown(records)
    assert "steward@example.com" in text and "bsa@example.com" in text


def test_render_markdown_with_no_records_says_so():
    assert "No records match." in render_markdown(())


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: search who approved what, when, across every real source, filtered by user,
    custodian, date and action. AC2: export to a real, parseable CSV."""
    board_path = _real_board(tmp_path)
    sources = AuditSources(
        promotion_requests=(PROMOTION_REQUESTS,),
        rules_dir=RULES,
        rules_root=REPO,
        gate_approval_logs=(GATE_APPROVALS,),
        boards=(board_path,),
    )
    records = build_audit_log(sources)
    assert len(records) >= 5  # 1 promotion + 2 rule history + 1 gate approval + 2 board moves (at least)

    by_steward = filter_records(records, user="steward")
    assert by_steward and all("steward" in r.user.lower() for r in by_steward)

    by_pershing = filter_records(records, custodian="pershing")
    assert by_pershing

    text = to_csv(records)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == len(records)
