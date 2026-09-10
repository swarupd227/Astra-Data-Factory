"""DQ runner and scores (S4.2.3): DMF results collected into a score per entity per business date, alerting on breach."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_verification import cli
from astra_verification.cli import main
from astra_verification.dq import (
    DQ_SCORE_TARGET,
    DqError,
    EntityScore,
    RuleScore,
    alert_statements,
    collect,
    compile_for_dq,
    measurement_query,
    raise_alerts,
    render_markdown,
    run,
    run_and_alert,
    write_report,
)
from astra_data.bundle import Target

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"


class FakeExecutor:
    """Answers query() by the first marker found in the SQL text; records every execute_script() call."""

    def __init__(self, answers: dict[str, list[tuple]] | None = None) -> None:
        self.answers = answers or {}
        self.queries: list[str] = []
        self.scripts: list[str] = []

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        for marker, rows in self.answers.items():
            if marker in sql:
                return rows
        return []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def close(self) -> None:
        pass


def _compiled():
    return compile_for_dq(CONFIG, REPO)


# All three example rules hold: trailer_control_total (error), cusip_present (warning), price_not_negative (info).
ALL_PASS = {
    "'PERSHING_POSITION_TRAILER_CONTROL_TOTAL'": [(0,)],
    "'CUSIP'": [(0,)],
    "'PERSHING_POSITION_PRICE_NOT_NEGATIVE'": [(0,)],
}

# cusip_present (warning, weight 2) fails once; the other two hold. Weighted score (4*1 + 2*0 + 1*1) / 7.
CUSIP_BREACH = {
    "'PERSHING_POSITION_TRAILER_CONTROL_TOTAL'": [(0,)],
    "'CUSIP'": [(1,)],
    "'PERSHING_POSITION_PRICE_NOT_NEGATIVE'": [(0,)],
}


# ---------------------------------------------------------------- compiling


def test_the_config_compiles_for_its_dq_rules_alone(tmp_path):
    compiled = _compiled()
    assert compiled.id == "pershing_position"
    assert [r.id for r in compiled.dq_rules] == ["trailer_control_total", "cusip_present", "price_not_negative"]
    assert all(r.record == "detail" for r in compiled.dq_rules)


def test_a_broken_config_is_refused_with_its_problems(tmp_path):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    with pytest.raises(DqError) as excinfo:
        compile_for_dq(bad, REPO)
    assert any("target_lag_minutes" in p.message for p in excinfo.value.problems)


# ---------------------------------------------------------------- collecting


def test_measurement_query_identifies_a_system_function_rule_by_table_and_column():
    compiled = _compiled()
    cusip_present = compiled.dq_rules[1]
    q = measurement_query(compiled, cusip_present, "ASTRA_DEV", date(2026, 8, 29))
    assert "TABLE_DATABASE = 'ASTRA_DEV' AND TABLE_SCHEMA = 'BRONZE' AND TABLE_NAME = 'PERSHING_POSITION_DETAIL'" in q
    assert "METRIC_DATABASE = 'SNOWFLAKE' AND METRIC_SCHEMA = 'CORE' AND METRIC_NAME = 'NULL_COUNT'" in q
    assert "ARGUMENT_NAMES, ',') = 'CUSIP'" in q
    assert "MEASUREMENT_TIME::DATE = '2026-08-29'" in q


def test_measurement_query_identifies_a_custom_dmf_rule_by_its_rendered_name():
    compiled = _compiled()
    control_total = compiled.dq_rules[0]
    q = measurement_query(compiled, control_total, "ASTRA_DEV", date(2026, 8, 29))
    assert "TABLE_SCHEMA = 'BRONZE' AND TABLE_NAME = 'PERSHING_POSITION_FILE_METADATA'" in q
    assert "METRIC_DATABASE = 'ASTRA_DEV' AND METRIC_SCHEMA = 'CONTROL' AND METRIC_NAME = 'PERSHING_POSITION_TRAILER_CONTROL_TOTAL'" in q
    assert "ARGUMENT_NAMES, ',') = 'TRAILER_DETAIL_COUNT,DETAIL_COUNT'" in q


def test_collect_reads_every_value_the_dmf_recorded_that_day():
    compiled = _compiled()
    executor = FakeExecutor({"'CUSIP'": [(0,), (1,), (0,)]})
    values = collect(executor, compiled, compiled.dq_rules[1], "ASTRA_DEV", date(2026, 8, 29))
    assert values == [0.0, 1.0, 0.0]


def test_collect_is_empty_when_the_dmf_has_not_triggered_that_day():
    compiled = _compiled()
    executor = FakeExecutor({})
    assert collect(executor, compiled, compiled.dq_rules[1], "ASTRA_DEV", date(2026, 8, 29)) == []


# ---------------------------------------------------------------- pure scoring


def test_a_rule_with_no_measurements_has_no_pass_rate_or_holds():
    r = RuleScore("x", "not_null", "record", "warning", "check", measurements=0, failing=0)
    assert r.pass_rate is None
    assert r.holds is None


def test_a_rule_with_measurements_has_a_pass_rate_and_holds():
    r = RuleScore("x", "not_null", "record", "warning", "check", measurements=4, failing=1)
    assert r.pass_rate == 0.75
    assert r.holds is False


def test_entity_score_is_severity_weighted_across_scored_rules():
    rules = (
        RuleScore("a", "control_total", "file", "error", "c1", measurements=1, failing=0),  # holds, weight 4
        RuleScore("b", "not_null", "record", "warning", "c2", measurements=1, failing=1),  # fails, weight 2
        RuleScore("c", "range", "record", "info", "c3", measurements=1, failing=0),  # holds, weight 1
    )
    s = EntityScore("detail", "pershing", "2026-08-29", DQ_SCORE_TARGET, rules)
    assert s.scored is True
    assert s.score == pytest.approx(5 / 7)
    assert s.meets_target is False
    assert [r.rule_id for r in s.breached_rules] == ["b"]
    assert s.worst_breached_severity == "warning"


def test_a_rule_with_no_measurements_does_not_count_against_the_score():
    rules = (
        RuleScore("a", "not_null", "record", "error", "c1", measurements=1, failing=0),
        RuleScore("b", "not_null", "record", "error", "c2", measurements=0, failing=0),
    )
    s = EntityScore("detail", "pershing", "2026-08-29", DQ_SCORE_TARGET, rules)
    assert s.score == 1.0
    assert len(s.scored_rules) == 1


def test_an_entity_with_no_scored_rule_is_unscored_not_zero():
    rules = (RuleScore("a", "not_null", "record", "error", "c1", measurements=0, failing=0),)
    s = EntityScore("detail", "pershing", "2026-08-29", DQ_SCORE_TARGET, rules)
    assert s.scored is False
    assert s.score is None
    assert s.meets_target is False


def test_worst_breached_severity_is_the_highest_among_failing_rules():
    rules = (
        RuleScore("a", "not_null", "record", "info", "c1", measurements=1, failing=1),
        RuleScore("b", "not_null", "record", "error", "c2", measurements=1, failing=1),
        RuleScore("c", "not_null", "record", "warning", "c3", measurements=1, failing=1),
    )
    s = EntityScore("detail", "pershing", "2026-08-29", DQ_SCORE_TARGET, rules)
    assert s.worst_breached_severity == "error"


# ---------------------------------------------------------------- run()


def test_run_groups_rules_by_entity_and_scores_them():
    compiled = _compiled()
    executor = FakeExecutor(ALL_PASS)
    scores = run(compiled, executor, "ASTRA_DEV", date(2026, 8, 29))
    assert [s.entity for s in scores] == ["detail"]
    assert scores[0].score == 1.0
    assert scores[0].meets_target is True
    assert [r.rule_id for r in scores[0].rules] == ["trailer_control_total", "cusip_present", "price_not_negative"]


def test_run_reflects_a_breach_from_one_rules_failing_measurements():
    compiled = _compiled()
    executor = FakeExecutor(CUSIP_BREACH)
    scores = run(compiled, executor, "ASTRA_DEV", date(2026, 8, 29))
    assert scores[0].score == pytest.approx(5 / 7)
    assert scores[0].meets_target is False
    assert [r.rule_id for r in scores[0].breached_rules] == ["cusip_present"]


def test_run_honours_a_custom_score_target():
    compiled = _compiled()
    executor = FakeExecutor(CUSIP_BREACH)
    scores = run(compiled, executor, "ASTRA_DEV", date(2026, 8, 29), score_target=0.5)
    assert scores[0].meets_target is True


# ---------------------------------------------------------------- alerting


def test_alert_statements_are_empty_when_every_scored_entity_meets_target():
    compiled = _compiled()
    scores = run(compiled, FakeExecutor(ALL_PASS), "ASTRA_DEV", date(2026, 8, 29))
    assert alert_statements(scores, Target("dev")) == []


def test_alert_statements_are_empty_for_an_unscored_entity():
    compiled = _compiled()
    scores = run(compiled, FakeExecutor({}), "ASTRA_DEV", date(2026, 8, 29))
    assert scores[0].scored is False
    assert alert_statements(scores, Target("dev")) == []


def test_a_breach_raises_one_deduplicated_alert_at_the_worst_breached_severity():
    compiled = _compiled()
    scores = run(compiled, FakeExecutor(CUSIP_BREACH), "ASTRA_DEV", date(2026, 8, 29))
    statements = alert_statements(scores, Target("dev"))
    assert len(statements) == 1
    stmt = statements[0]
    assert '"ASTRA_DEV"."CONTROL"."ALERTS"' in stmt
    assert "'dq_score_breach'" in stmt
    assert "'warning'" in stmt
    assert "'pershing'" in stmt
    assert "SOURCE_KEY = 'dq:pershing:detail:2026-08-29'" in stmt
    assert "WHERE NOT EXISTS" in stmt


def test_raise_alerts_executes_only_when_there_is_something_to_raise():
    compiled = _compiled()
    executor = FakeExecutor(ALL_PASS)
    scores = run(compiled, executor, "ASTRA_DEV", date(2026, 8, 29))
    assert raise_alerts(scores, executor, Target("dev")) == []
    assert executor.scripts == []

    executor2 = FakeExecutor(CUSIP_BREACH)
    scores2 = run(compiled, executor2, "ASTRA_DEV", date(2026, 8, 29))
    raised = raise_alerts(scores2, executor2, Target("dev"))
    assert len(raised) == 1
    assert len(executor2.scripts) == 1


# ---------------------------------------------------------------- report


def test_render_markdown_lists_entities_and_breached_rules():
    compiled = _compiled()
    scores = run(compiled, FakeExecutor(CUSIP_BREACH), "ASTRA_DEV", date(2026, 8, 29))
    text = render_markdown(scores)
    assert "# DQ score: pershing 2026-08-29" in text
    assert "`detail`" in text and "below target" in text
    assert "Breached: `cusip_present`" in text


def test_write_report_writes_markdown_and_json(tmp_path):
    compiled = _compiled()
    scores = run(compiled, FakeExecutor(ALL_PASS), "ASTRA_DEV", date(2026, 8, 29))
    markdown, data = write_report(scores, tmp_path / "pershing_position-2026-08-29")
    assert markdown.read_text(encoding="utf-8").startswith("# DQ score")
    loaded = json.loads(data.read_text(encoding="utf-8"))
    assert loaded[0]["entity"] == "detail" and loaded[0]["meets_target"] is True


def test_run_and_alert_wires_scoring_report_and_alerting_together(tmp_path):
    compiled = _compiled()
    executor = FakeExecutor(CUSIP_BREACH)
    scores, alerts = run_and_alert(compiled, executor, "ASTRA_DEV", Target("dev"), date(2026, 8, 29), out=tmp_path / "out")
    assert len(alerts) == 1
    assert len(executor.scripts) == 1
    assert (tmp_path / "out" / "dq.md").exists()
    assert (tmp_path / "out" / "dq.json").exists()


def test_run_and_alert_can_skip_alerting_for_a_trial_run(tmp_path):
    compiled = _compiled()
    executor = FakeExecutor(CUSIP_BREACH)
    scores, alerts = run_and_alert(compiled, executor, "ASTRA_DEV", Target("dev"), date(2026, 8, 29), alert=False)
    assert alerts == []
    assert executor.scripts == []


# ---------------------------------------------------------------- CLI


def test_cli_scores_a_source_and_meets_target(tmp_path, monkeypatch):
    executor = FakeExecutor(ALL_PASS)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "dq"
    code = main(["dq", "run", "--config", str(CONFIG), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO), "--out", str(out)])
    assert code == 0
    assert (out / "pershing_position-2026-08-29" / "dq.md").exists()
    assert executor.scripts == []


def test_cli_reports_a_breach_and_raises_an_alert(tmp_path, monkeypatch):
    executor = FakeExecutor(CUSIP_BREACH)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "dq"
    code = main(["dq", "run", "--config", str(CONFIG), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO), "--out", str(out)])
    assert code == 1
    assert len(executor.scripts) == 1
    assert "dq_score_breach" in executor.scripts[0]


def test_cli_no_alert_skips_raising_one(tmp_path, monkeypatch):
    executor = FakeExecutor(CUSIP_BREACH)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "dq"
    code = main(["dq", "run", "--config", str(CONFIG), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO), "--out", str(out), "--no-alert"])
    assert code == 1
    assert executor.scripts == []


def test_cli_json_output(tmp_path, monkeypatch, capsys):
    executor = FakeExecutor(ALL_PASS)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "dq"
    code = main(["dq", "run", "--config", str(CONFIG), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO), "--out", str(out), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["entity"] == "detail"


def test_cli_a_custom_target_can_turn_a_breach_into_a_pass(tmp_path, monkeypatch):
    executor = FakeExecutor(CUSIP_BREACH)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "dq"
    code = main(["dq", "run", "--config", str(CONFIG), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO), "--out", str(out), "--target", "0.5"])
    assert code == 0
    assert executor.scripts == []


def test_cli_a_source_with_no_dq_rules_has_nothing_to_score(tmp_path, capsys):
    bare = tmp_path / "pershing_position.yaml"
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index("dq_rules:")
    end = text.index("\n\n", start)
    bare.write_text(text[:start] + text[end + 2 :], encoding="utf-8")
    code = main(["dq", "run", "--config", str(bare), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO)])
    assert code == 0
    assert "nothing to score" in capsys.readouterr().out


def test_cli_a_broken_config_is_refused_before_any_snowflake_call(tmp_path, capsys):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    code = main(["dq", "run", "--config", str(bad), "--business-date", "2026-08-29", "--environment", "dev", "--repo", str(REPO)])
    assert code == 2
    assert "target_lag_minutes" in capsys.readouterr().err
