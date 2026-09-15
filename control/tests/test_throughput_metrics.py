from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.audit_log import AuditRecord, rule_status_changes_from
from astra_control.board import Board, add_custodian, move
from astra_control.throughput_metrics import (
    COUNTED_STATUSES,
    CustodianDayAcceptance,
    CustodianDayCost,
    ThroughputReport,
    acceptance_by_custodian_day,
    build_report,
    render_markdown,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "rules"


def _record(custodian: str, at: str, subject: str, kind: str = "rule_status_change") -> AuditRecord:
    return AuditRecord(kind=kind, action="rule-review.set-status", user="steward@example.com", at=at, custodian=custodian, subject=subject, evidence="e", agent_version=None, source="x")


def _real_board() -> Board:
    board = add_custodian(Board(), "pershing", "envestnet-custodial", by="ops@example.com", at=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc))
    for station in ("draft", "dry_run", "dual_run", "cutover"):
        board = move(board, "pershing", station, by="pm@example.com", at=datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc))
    return board


# ---------------------------------------------------------------- AC1, first third: custodians live per week (reused directly)


def test_build_report_reuses_board_live_per_week_directly():
    report = build_report(_real_board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.custodians_live_per_week == {"2026-W37": 1}  # 2026-09-09 is in ISO week 37


def test_build_report_with_no_cutovers_is_empty():
    report = build_report(Board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.custodians_live_per_week == {}


# ---------------------------------------------------------------- AC1, second third: agent acceptance -- a real event ratio


def test_acceptance_counts_confirmed_and_rejected_separately():
    records = (
        _record("pershing", "2026-09-10T09:00:00Z", "pershing_gcus.a -> confirmed"),
        _record("pershing", "2026-09-10T10:00:00Z", "pershing_gcus.b -> rejected"),
        _record("pershing", "2026-09-10T11:00:00Z", "pershing_gcus.c -> legacy_defect"),
    )
    result = acceptance_by_custodian_day(records)
    assert len(result) == 1
    a = result[0]
    assert a.custodian == "pershing" and a.business_date == "2026-09-10"
    assert a.accepted == 1 and a.rejected == 2
    assert a.acceptance_rate == pytest.approx(1 / 3)


def test_acceptance_excludes_recovered_not_a_review_decision():
    records = (_record("pershing", "2026-09-10T09:00:00Z", "pershing_gcus.a -> recovered"),)
    assert acceptance_by_custodian_day(records) == ()


def test_acceptance_excludes_non_rule_status_change_records():
    records = (_record("pershing", "2026-09-10T09:00:00Z", "pershing_gcus_full -> confirmed", kind="promotion_request"),)
    assert acceptance_by_custodian_day(records) == ()


def test_acceptance_skips_records_with_no_custodian():
    records = (AuditRecord(kind="rule_status_change", action="a", user="u", at="2026-09-10T09:00:00Z", custodian=None, subject="x -> confirmed", evidence="e", agent_version=None, source="s"),)
    assert acceptance_by_custodian_day(records) == ()


def test_acceptance_groups_by_business_date_not_full_timestamp():
    records = (
        _record("pershing", "2026-09-10T09:00:00Z", "a -> confirmed"),
        _record("pershing", "2026-09-10T23:59:59Z", "b -> confirmed"),
    )
    result = acceptance_by_custodian_day(records)
    assert len(result) == 1 and result[0].accepted == 2


def test_acceptance_groups_separately_by_different_days_and_custodians():
    records = (
        _record("pershing", "2026-09-10T09:00:00Z", "a -> confirmed"),
        _record("pershing", "2026-09-11T09:00:00Z", "b -> confirmed"),
        _record("fidelity", "2026-09-10T09:00:00Z", "c -> rejected"),
    )
    result = acceptance_by_custodian_day(records)
    assert {(a.custodian, a.business_date, a.accepted, a.rejected) for a in result} == {
        ("pershing", "2026-09-10", 1, 0),
        ("pershing", "2026-09-11", 1, 0),
        ("fidelity", "2026-09-10", 0, 1),
    }


def test_acceptance_rate_is_none_with_zero_total():
    assert CustodianDayAcceptance("pershing", "2026-09-10", 0, 0).acceptance_rate is None


def test_acceptance_against_the_real_rule_catalog():
    """Grounded against real committed rule history: pershing_gcus.quantity_sign has exactly one
    real confirm; nothing in the real catalog has a rejection or legacy_defect yet."""
    records = rule_status_changes_from(RULES, REPO)
    result = acceptance_by_custodian_day(records)
    pershing = [a for a in result if a.custodian == "pershing"]
    assert sum(a.accepted for a in pershing) == 1
    assert sum(a.rejected for a in pershing) == 0


def test_counted_statuses_never_includes_recovered():
    assert "recovered" not in COUNTED_STATUSES
    assert set(COUNTED_STATUSES) == {"confirmed", "rejected", "legacy_defect"}


# ---------------------------------------------------------------- AC2: cost per custodian per day, caller-supplied only


def test_build_report_with_no_costs_is_honestly_empty():
    report = build_report(Board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.costs == ()


def test_build_report_with_costs_given():
    report = build_report(Board(), (), costs={("pershing", "2026-09-10"): 42.5}, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.costs == (CustodianDayCost("pershing", "2026-09-10", 42.5),)


# ---------------------------------------------------------------- agent_eval's own weekly report, a distinct section


def test_build_report_carries_a_given_agent_eval_weekly_report_as_is():
    weekly = {"agents_scored": ["pattern_matcher"], "all_passed": True}
    report = build_report(Board(), (), agent_eval_weekly=weekly, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.agent_eval_weekly == weekly


def test_build_report_with_no_agent_eval_weekly_is_none():
    report = build_report(Board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report.agent_eval_weekly is None


# ---------------------------------------------------------------- to_dict / render_markdown / write_report


def test_to_dict_round_trips_through_json():
    report = build_report(_real_board(), rule_status_changes_from(RULES, REPO), costs={("pershing", "2026-09-10"): 10.0}, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    data = json.loads(json.dumps(report.to_dict()))
    assert data["custodians_live_per_week"] == {"2026-W37": 1}
    assert data["costs"][0]["cost"] == 10.0


def test_render_markdown_shows_every_section():
    report = build_report(
        _real_board(),
        (_record("pershing", "2026-09-10T09:00:00Z", "a -> confirmed"),),
        costs={("pershing", "2026-09-10"): 10.0},
        agent_eval_weekly={"agents_scored": ["pattern_matcher"], "all_passed": True},
        clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    text = render_markdown(report)
    assert "2026-W37" in text
    assert "pershing" in text and "Accepted" in text  # acceptance section present
    assert "$10.00" in text
    assert "pattern_matcher" in text
    assert "different metric" in text.lower()


def test_render_markdown_with_no_costs_says_so():
    report = build_report(Board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert "no query-tag mechanism" in render_markdown(report)


def test_render_markdown_with_no_custodians_live_says_so():
    report = build_report(Board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert "No custodian has reached cutover yet." in render_markdown(report)


def test_write_report_writes_real_markdown_and_json(tmp_path):
    report = build_report(_real_board(), (), clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))
    md_path, json_path = write_report(report, tmp_path / "weekly")
    assert md_path.is_file() and json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["custodians_live_per_week"] == {"2026-W37": 1}


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: custodians live per week (reused), agent acceptance per custodian per day (a real
    event ratio), assembled and exported for the client cadence. AC2: cost per custodian visible
    when given, honestly absent otherwise -- no query-tag mechanism exists to compute it."""
    board = _real_board()
    records = rule_status_changes_from(RULES, REPO)
    report = build_report(board, records, costs={("pershing", "2026-09-10"): 12.34}, clock=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))

    assert report.custodians_live_per_week  # AC1: custodians live per week
    assert any(a.accepted > 0 for a in report.acceptance)  # AC1: real agent acceptance data
    assert any(c.cost == 12.34 for c in report.costs)  # AC2: cost per custodian, visible when given

    md_path, json_path = write_report(report, tmp_path / "weekly")
    assert md_path.is_file() and json_path.is_file()  # AC1: exported for the client cadence
