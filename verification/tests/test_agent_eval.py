"""Agent evaluation harness (S4.3.4): every agent scored against its own gold set, by tier, weekly published."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.agent_eval import (
    AgentEvalError,
    AgentEvalResult,
    CaseScore,
    GoldSet,
    Predictions,
    Threshold,
    TierScore,
    WeeklyReport,
    check,
    discover,
    load_gold_set,
    load_predictions,
    render_markdown,
    render_weekly_markdown,
    run_weekly,
    score,
    write_report,
    write_weekly_report,
)

REPO = Path(__file__).resolve().parents[2]
AGENTS_EXAMPLES = REPO / "agents" / "examples"
EVAL = AGENTS_EXAMPLES / "spec_reader" / "eval.yaml"
PREDICTIONS = AGENTS_EXAMPLES / "spec_reader" / "predictions.yaml"
EVAL_TEXT = EVAL.read_text(encoding="utf-8")
PREDICTIONS_TEXT = PREDICTIONS.read_text(encoding="utf-8")


def _gold() -> GoldSet:
    g, problems = load_gold_set(EVAL, REPO)
    assert problems == [], [p.format() for p in problems]
    return g


def _predictions() -> Predictions:
    p, problems = load_predictions(PREDICTIONS, REPO)
    assert problems == [], [p.format() for p in problems]
    return p


# ---------------------------------------------------------------- loading the gold set


def test_the_committed_example_gold_set_loads_clean():
    g = _gold()
    assert g.agent == "spec_reader"
    assert {c.id for c in g.cases} == {"pershing_gcus_header", "pershing_gcus_detail"}
    assert g.thresholds["simple"] == Threshold(0.95, 0.95)
    assert g.case("pershing_gcus_detail").tier == "medium"


def test_no_such_file(tmp_path):
    g, problems = load_gold_set(tmp_path / "missing.yaml", tmp_path)
    assert g is None and "no such file" in problems[0].message


def test_a_duplicate_case_id_is_refused(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(EVAL_TEXT.replace("id: pershing_gcus_detail", "id: pershing_gcus_header"), encoding="utf-8")
    g, problems = load_gold_set(path, tmp_path)
    assert g is None and any("already used" in p.message for p in problems)


def test_a_tier_with_no_threshold_is_refused(tmp_path):
    path = tmp_path / "eval.yaml"
    text = EVAL_TEXT.replace("  medium:\n    precision: 0.9\n    recall: 0.9\n", "")
    path.write_text(text, encoding="utf-8")
    g, problems = load_gold_set(path, tmp_path)
    assert g is None and any("no threshold" in p.message for p in problems)


def test_an_unknown_tier_is_refused(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(EVAL_TEXT.replace("tier: simple", "tier: extreme"), encoding="utf-8")
    g, problems = load_gold_set(path, tmp_path)
    assert g is None and problems


# ---------------------------------------------------------------- discover / check


def test_discover_finds_the_example_under_its_own_directory():
    assert EVAL in discover(AGENTS_EXAMPLES)


def test_check_reports_no_problems_for_the_example():
    gold_sets, problems = check(AGENTS_EXAMPLES, REPO)
    assert problems == []
    assert any(g.agent == "spec_reader" for g in gold_sets)


def test_check_flags_an_agent_directory_mismatch(tmp_path):
    other = tmp_path / "other_agent"
    other.mkdir()
    (other / "eval.yaml").write_text(EVAL_TEXT, encoding="utf-8")
    gold_sets, problems = check(tmp_path, tmp_path)
    assert any("must match the directory" in p.message for p in problems)


# ---------------------------------------------------------------- loading predictions


def test_the_committed_example_predictions_load_clean():
    p = _predictions()
    assert p.agent == "spec_reader" and p.run == "illustrative-example"
    assert "field:detail.filler@p14l8" not in p.items["pershing_gcus_detail"]


def test_predictions_no_such_file(tmp_path):
    p, problems = load_predictions(tmp_path / "missing.yaml", tmp_path)
    assert p is None and "no such file" in problems[0].message


# ---------------------------------------------------------------- pure scoring


def test_case_score_true_positives_false_positives_false_negatives():
    c = CaseScore("c1", "medium", expected=frozenset({"a", "b", "c"}), predicted=frozenset({"b", "c", "d"}))
    assert c.true_positives == {"b", "c"}
    assert c.false_positives == {"d"}
    assert c.false_negatives == {"a"}


def test_tier_score_is_micro_averaged_not_case_averaged():
    """A large easy case and a small hard case: micro-averaging weighs by item count, not by case count."""
    s = TierScore("medium")
    s.cases, s.true_positives, s.predicted, s.expected = 2, 8, 10, 10
    assert s.precision == pytest.approx(0.8)
    assert s.recall == pytest.approx(0.8)


def test_tier_score_precision_is_none_with_nothing_predicted():
    s = TierScore("medium", cases=1, true_positives=0, predicted=0, expected=3)
    assert s.precision is None
    assert s.meets(Threshold(0.9, 0.9)) is False


def test_tier_score_meets_requires_both_precision_and_recall():
    s = TierScore("medium", cases=1, true_positives=9, predicted=10, expected=10)
    assert s.meets(Threshold(0.9, 0.9)) is True
    assert s.meets(Threshold(0.95, 0.9)) is False


def test_score_raises_when_predictions_are_for_a_different_agent():
    gold = _gold()
    bad = Predictions("other_agent", "run", {}, Path("x"))
    with pytest.raises(AgentEvalError, match="different agent|not 'spec_reader'"):
        score(gold, bad)


def test_score_reports_a_gold_case_with_no_prediction_as_missing_not_zero():
    gold = _gold()
    partial = Predictions("spec_reader", "run", {"pershing_gcus_header": frozenset({"field:header.record_type@p4l3"})}, Path("x"))
    result = score(gold, partial)
    assert result.missing_cases == ("pershing_gcus_detail",)
    assert result.passed is False
    assert len(result.cases) == 1


def test_the_example_shows_a_realistic_recall_regression():
    result = score(_gold(), _predictions())
    assert result.missing_cases == ()
    assert result.by_tier["simple"].precision == 1.0 and result.by_tier["simple"].recall == 1.0
    medium = result.by_tier["medium"]
    assert medium.precision == 1.0
    assert medium.recall == pytest.approx(8 / 9)
    assert medium.meets(result.thresholds["medium"]) is False
    assert result.passed is False


# ---------------------------------------------------------------- the report


def test_render_markdown_names_the_missed_field():
    result = score(_gold(), _predictions())
    text = render_markdown(result)
    assert "# Agent evaluation: spec_reader" in text
    assert "field:detail.filler@p14l8" in text
    assert "NO" in text  # medium tier does not meet its threshold


def test_write_report_writes_markdown_and_json(tmp_path):
    result = score(_gold(), _predictions())
    markdown, data = write_report(result, tmp_path / "out")
    assert markdown.read_text(encoding="utf-8") == render_markdown(result)
    assert json.loads(data.read_text(encoding="utf-8"))["passed"] is False


# ---------------------------------------------------------------- the weekly report


def test_weekly_by_tier_aggregates_across_every_agent():
    r1 = AgentEvalResult("agent_a", "run1", {"simple": Threshold(0.9, 0.9)}, (CaseScore("c1", "simple", frozenset({"a", "b"}), frozenset({"a", "b"})),))
    r2 = AgentEvalResult("agent_b", "run2", {"simple": Threshold(0.9, 0.9)}, (CaseScore("c2", "simple", frozenset({"a", "b"}), frozenset({"a"})),))
    weekly = WeeklyReport("2026-09-12T00:00:00Z", (r1, r2))
    tier = weekly.by_tier["simple"]
    assert tier.true_positives == 3 and tier.predicted == 3 and tier.expected == 4
    assert weekly.all_passed is False  # r2 does not meet threshold


def test_run_weekly_skips_an_agent_with_no_predictions_yet(tmp_path):
    agents = tmp_path / "agents"
    (agents / "spec_reader").mkdir(parents=True)
    (agents / "spec_reader" / "eval.yaml").write_text(EVAL_TEXT, encoding="utf-8")
    report, problems = run_weekly(agents, tmp_path)
    assert problems == []
    assert report.results == () and report.skipped == ("spec_reader",)
    assert report.all_passed is True  # nothing scored is not a failure; the agent just has not been run yet


def test_run_weekly_scores_the_example_and_reports_the_regression():
    report, problems = run_weekly(AGENTS_EXAMPLES, REPO)
    assert problems == []
    assert [r.agent for r in report.results] == ["spec_reader"]
    assert report.skipped == ()
    assert report.all_passed is False
    assert report.by_tier["medium"].recall == pytest.approx(8 / 9)


def test_write_weekly_report_writes_markdown_and_json(tmp_path):
    report, _ = run_weekly(AGENTS_EXAMPLES, REPO)
    markdown, data = write_weekly_report(report, tmp_path / "out")
    assert markdown.read_text(encoding="utf-8") == render_weekly_markdown(report)
    assert json.loads(data.read_text(encoding="utf-8"))["all_passed"] is False


# ---------------------------------------------------------------- CLI


def test_cli_check(capsys):
    code = main(["agent-eval", "check", str(AGENTS_EXAMPLES)])
    assert code == 0
    assert "spec_reader:" in capsys.readouterr().out


def test_cli_score_needs_no_snowflake_connection(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["agent-eval", "score", "--gold", str(EVAL), "--predictions", str(PREDICTIONS), "--out", str(out)])
    assert code == 1  # the medium tier's recall regression fails the gate
    assert (out / "eval.md").exists()
    text = capsys.readouterr().out
    assert "passed: no" in text


def test_cli_score_json(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["agent-eval", "score", "--gold", str(EVAL), "--predictions", str(PREDICTIONS), "--out", str(out), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == "spec_reader" and payload["passed"] is False


def test_cli_report(tmp_path, capsys):
    out = tmp_path / "weekly"
    code = main(["agent-eval", "report", str(AGENTS_EXAMPLES), "--out", str(out)])
    assert code == 1
    assert (out / "weekly.md").exists()
    assert "all passed: no" in capsys.readouterr().out


def test_cli_score_refuses_a_bad_gold_set(tmp_path, capsys):
    bad = tmp_path / "eval.yaml"
    bad.write_text(EVAL_TEXT.replace("tier: simple", "tier: extreme"), encoding="utf-8")
    code = main(["agent-eval", "score", "--gold", str(bad), "--predictions", str(PREDICTIONS), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err
