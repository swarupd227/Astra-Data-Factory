from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_verification.parity import compare_rows, load_parity
from astra_verification.parity_report import Cycle, aggregate, write_report

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
    render_break_groups_markdown,
    render_record_pair_markdown,
    render_record_pairs_markdown,
    render_trend_markdown,
)

REPO = Path(__file__).resolve().parents[2]
PARITY_MAPPING = REPO / "golden" / "pershing" / "parity.yaml"
BREAK_REPORT = REPO / "control" / "examples" / "queue" / "break-explainer" / "pershing" / "2026-09-01" / "report.json"


def _mapping():
    mapping, problems = load_parity(PARITY_MAPPING, REPO)
    assert problems == [], [p.format() for p in problems]
    return mapping


def _legacy_row(account: str, cusip: str, as_of: str, quantity: str, price: str, market_value: str) -> dict:
    return {"AccountNumber": account, "Cusip": cusip, "AsOfDate": as_of, "Quantity": quantity, "Price": price, "MarketValue": market_value}


def _lakehouse_row(account: str, cusip: str, as_of: str, quantity: str, price: str, market_value: str) -> dict:
    return {"ACCOUNT_NUMBER": account, "CUSTODIAN_SECURITY_ID": cusip, "AS_OF_DATE": as_of, "QUANTITY": quantity, "PRICE": price, "MARKET_VALUE": market_value}


def _three_day_report(tmp_path: Path) -> Path:
    """A real ParityReport, three real cycles, built with the real astra_verification.parity
    engine against the real committed golden/pershing/parity.yaml mapping -- no captured golden
    dataset exists in this repository (module docstring), so the rows themselves are the minimal
    illustrative data this test needs, in the mapping's own real column shapes (legacy and
    lakehouse column names genuinely differ, per the mapping's own keys/fields)."""
    mapping = _mapping()
    cycles = [
        Cycle("2026-09-01", compare_rows(
            mapping,
            [_legacy_row("ACC1", "CUSIP1", "2026-09-01", "100.00000", "50.1234", "5012.34")],
            [_lakehouse_row("ACC1", "CUSIP1", "2026-09-01", "100.00000", "50.1234", "5012.34")],
            "2026-09-01",
        )),
        Cycle("2026-09-02", compare_rows(
            mapping,
            [_legacy_row("ACC1", "CUSIP1", "2026-09-02", "100.00000", "50.1234", "5012.34"), _legacy_row("ACC2", "CUSIP2", "2026-09-02", "200.00000", "10.0000", "2000.00")],
            [_lakehouse_row("ACC1", "CUSIP1", "2026-09-02", "100.00200", "50.1234", "5012.34"), _lakehouse_row("ACC2", "CUSIP2", "2026-09-02", "200.00000", "10.0000", "2000.00")],
            "2026-09-02",
        )),  # ACC1's own Quantity differs beyond the mapping's own 5-decimal-place tolerance
        Cycle("2026-09-03", compare_rows(
            mapping,
            [_legacy_row("ACC1", "CUSIP1", "2026-09-03", "100.00000", "50.1234", "5012.34")],
            [_lakehouse_row("ACC1", "CUSIP1", "2026-09-03", "100.00000", "50.1234", "5012.34")],
            "2026-09-03",
        )),
    ]
    report = aggregate(mapping.custodian, mapping.legacy_output, mapping.table, cycles)
    _, data_path = write_report(report, tmp_path)
    return data_path


# ---------------------------------------------------------------- AC1: trend chart


def test_load_trend_reads_every_cycle_in_order(tmp_path):
    trend = load_trend(_three_day_report(tmp_path))
    assert trend.custodian == "pershing"
    assert [p.business_date for p in trend.points] == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_load_trend_carries_each_day_s_own_match_rate(tmp_path):
    trend = load_trend(_three_day_report(tmp_path))
    by_date = {p.business_date: p for p in trend.points}
    assert by_date["2026-09-01"].match_rate == 1.0
    assert by_date["2026-09-02"].match_rate == 0.5  # one of two rows mismatched
    assert by_date["2026-09-03"].match_rate == 1.0


def test_load_trend_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ParityViewerError, match="not found"):
        load_trend(tmp_path / "missing.json")


def test_load_trend_refuses_a_file_that_is_not_a_parity_report(tmp_path):
    path = tmp_path / "not_parity.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(ParityViewerError, match="not a parity report"):
        load_trend(path)


def test_render_trend_markdown_shows_every_day(tmp_path):
    text = render_trend_markdown(load_trend(_three_day_report(tmp_path)))
    assert "2026-09-01" in text and "2026-09-02" in text and "2026-09-03" in text
    assert "flat" in text or "improving" in text or "declining" in text  # a real .trend value is present


def test_render_trend_markdown_with_no_cycles_says_so():
    assert "No cycles" in render_trend_markdown(Trend("pershing", "n/a: one cycle", 1.0, True, ()))


# ---------------------------------------------------------------- AC2: break groups by rule and field, with counts


def test_break_groups_against_the_real_committed_break_explainer_report():
    groups = break_groups(BREAK_REPORT)
    assert len(groups) == 3  # QUANTITY, PRICE, MARKET_VALUE -- three distinct (rule, field, cause) groups
    fields = {g.field for g in groups}
    assert fields == {"QUANTITY", "PRICE", "MARKET_VALUE"}
    quantity = next(g for g in groups if g.field == "QUANTITY")
    assert quantity.rule_id == "pershing_gcus.quantity_sign"
    assert quantity.cause == "transform"
    assert quantity.count == 1


def test_break_groups_counts_repeated_rule_field_pairs(tmp_path):
    data = {
        "explanations": [
            {"key": ["A"], "field": "QUANTITY", "legacy": "1", "lakehouse": "2", "cause": "transform", "rule_id": "r.q", "description": "d"},
            {"key": ["B"], "field": "QUANTITY", "legacy": "3", "lakehouse": "4", "cause": "transform", "rule_id": "r.q", "description": "d"},
            {"key": ["C"], "field": "PRICE", "legacy": "5", "lakehouse": "6", "cause": "unexplained", "rule_id": None, "description": "d"},
        ]
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    groups = break_groups(path)
    assert groups[0] == BreakGroup("r.q", "QUANTITY", "transform", 2)  # busiest group first
    assert groups[1] == BreakGroup(None, "PRICE", "unexplained", 1)


def test_break_groups_with_no_explanations_is_empty(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"explanations": []}), encoding="utf-8")
    assert break_groups(path) == ()


def test_render_break_groups_markdown_shows_rule_field_and_count():
    text = render_break_groups_markdown(break_groups(BREAK_REPORT))
    assert "pershing_gcus.quantity_sign" in text
    assert "QUANTITY" in text and "1" in text


def test_render_break_groups_markdown_with_none_says_so():
    assert "No differences." in render_break_groups_markdown(())


# ---------------------------------------------------------------- AC3: record pair, legacy vs lakehouse


def test_record_pairs_groups_by_key_against_the_real_report():
    pairs = record_pairs(BREAK_REPORT)
    assert len(pairs) == 3  # three distinct keys in the real committed example, one field each
    keys = {p.key for p in pairs}
    assert ("ACC0000002", "594918104", "2026-09-01") in keys


def test_record_pair_shows_legacy_and_lakehouse_values_with_the_differing_field():
    pair = record_pair(BREAK_REPORT, ("ACC0000002", "594918104", "2026-09-01"))
    assert len(pair.fields) == 1
    f = pair.fields[0]
    assert f.field == "QUANTITY"
    assert f.legacy == "100.00000"
    assert f.lakehouse == "100.00200"
    assert f.rule_id == "pershing_gcus.quantity_sign"


def test_record_pairs_groups_multiple_differing_fields_under_one_key(tmp_path):
    data = {
        "explanations": [
            {"key": ["ACC1", "CUSIP1", "2026-09-01"], "field": "QUANTITY", "legacy": "1", "lakehouse": "2", "cause": "transform", "rule_id": "r.q", "description": "d1"},
            {"key": ["ACC1", "CUSIP1", "2026-09-01"], "field": "PRICE", "legacy": "3", "lakehouse": "4", "cause": "unexplained", "rule_id": None, "description": "d2"},
        ]
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    pairs = record_pairs(path)
    assert len(pairs) == 1
    assert pairs[0].key == ("ACC1", "CUSIP1", "2026-09-01")
    assert {f.field for f in pairs[0].fields} == {"QUANTITY", "PRICE"}


def test_record_pair_unknown_key_is_a_clear_error():
    with pytest.raises(ParityViewerError, match="no record"):
        record_pair(BREAK_REPORT, ("nope", "nope", "nope"))


def test_render_record_pairs_markdown_shows_every_key():
    text = render_record_pairs_markdown(record_pairs(BREAK_REPORT))
    assert "ACC0000002" in text and "ACC0000003" in text and "ACC0000004" in text


def test_render_record_pairs_markdown_with_none_says_so():
    assert "No differing records." in render_record_pairs_markdown(())


def test_render_record_pair_markdown_highlights_the_differing_field():
    pair = record_pair(BREAK_REPORT, ("ACC0000002", "594918104", "2026-09-01"))
    text = render_record_pair_markdown(pair)
    assert "QUANTITY" in text and "100.00000" in text and "100.00200" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a real per-day trend, built from the real parity engine against the real golden
    mapping. AC2: break groups by rule and field, with counts, against the real committed
    break-explainer report. AC3: a record pair drill-down shows legacy vs lakehouse with the
    differing field."""
    trend = load_trend(_three_day_report(tmp_path))
    assert len(trend.points) == 3
    assert trend.points[1].match_rate < trend.points[0].match_rate  # a real dip on day 2

    groups = break_groups(BREAK_REPORT)
    assert all(g.count >= 1 for g in groups)
    assert any(g.rule_id is not None for g in groups)

    pair = record_pair(BREAK_REPORT, ("ACC0000002", "594918104", "2026-09-01"))
    assert pair.fields[0].legacy != pair.fields[0].lakehouse
