"""Chaos scenarios (S4.3.2): late, malformed, duplicate and truncated files injected, recovery proven with no manual data surgery."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.dryrun import DryRunError, Sample, compile_and_render, samples_for
from astra_verification.sandbox import SandboxSpec
from astra_verification.chaos import (
    SCENARIOS,
    ChaosError,
    ChaosReport,
    ScenarioResult,
    control_total_gap_query,
    exception_count_query,
    inject_duplicate,
    inject_late,
    inject_malformed,
    inject_truncated,
    parse_problem_count_query,
    render_markdown,
    run,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
CDM_DDL = REPO / "domains" / "custodial" / "cdm" / "rendered" / "1.0" / "ddl.sql"
REFERENCE_BUNDLE = REPO / "releases" / "custodial-reference-data"


@pytest.fixture
def sample(tmp_path) -> Path:
    path = tmp_path / "GCUS_20260829_POS_001.dat"
    lines = ["HDR20260829RMT0000001R".ljust(120)] + ["DTL" + "12345678".ljust(10) + "037833100" + "000000000010000000+" + "000000227120000" + "20260829EQ".ljust(70)] * 4 + ["TRL000000004".ljust(120)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _compiled_and_base(sample_path: Path, tmp_path: Path):
    compiled, _ = compile_and_render(CONFIG, REPO, tmp_path / "bundle")
    base = samples_for(compiled, [sample_path])[0]
    return compiled, base


def _monotonic():
    ticks = iter(range(0, 100000, 4))
    return lambda: float(next(ticks))


class FakeExecutor:
    """process() returns an incrementing run id; every other query is answered by an exact-text lookup built from chaos.py's own query builders, so the fault and retry checks can never be confused."""

    def __init__(self, answers: dict[str, list[tuple]] | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.answers = answers or {}
        self._process_calls = 0

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        if '"BRONZE"."PERSHING_POSITION_PROCESS"()' in sql:
            self._process_calls += 1
            return [(f"run-{self._process_calls:04d}",)]
        if sql in self.answers:
            return self.answers[sql]
        return [(0,)]

    def close(self) -> None:
        pass


def _all_scenarios_answers(spec: SandboxSpec, compiled, base: Sample) -> dict[str, list[tuple]]:
    """Every scenario's fault is detected, every retry recovers — the default 'everything works' fixture other tests start from and override."""
    answers: dict[str, list[tuple]] = {}
    answers[parse_problem_count_query(spec, compiled, "RECORD_TYPE_UNKNOWN", "pershing/GCUS_20260829_POS_001-malformed.dat")] = [(1,)]
    answers[parse_problem_count_query(spec, compiled, "RECORD_TYPE_UNKNOWN", "pershing/GCUS_20260829_POS_001-malformed-retry.dat")] = [(0,)]
    answers[exception_count_query(spec, compiled, "run-0004", "MERGE_DUPLICATE_KEY")] = [(1,)]
    answers[exception_count_query(spec, compiled, "run-0005", "MERGE_DUPLICATE_KEY")] = [(0,)]
    rule = next(r for r in compiled.dq_rules if r.kind == "control_total")
    answers[control_total_gap_query(spec, compiled, rule, "pershing/GCUS_20260829_POS_001-truncated.dat")] = [(1,)]
    answers[control_total_gap_query(spec, compiled, rule, "pershing/GCUS_20260829_POS_001-truncated-retry.dat")] = [(0,)]
    return answers


# ---------------------------------------------------------------- injection


def test_inject_malformed_corrupts_the_detail_marker_only(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    fault = inject_malformed(compiled, base, tmp_path / "out")
    lines = fault.path.read_text(encoding="ascii").splitlines()
    assert lines[0] == base.path.read_text(encoding="ascii").splitlines()[0]  # header untouched
    assert lines[1].startswith("???")
    assert fault.lines == base.lines
    assert fault.file_name == "pershing/GCUS_20260829_POS_001-malformed.dat"


def test_inject_duplicate_repeats_one_line(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    fault = inject_duplicate(compiled, base, tmp_path / "out")
    lines = fault.path.read_text(encoding="ascii").splitlines()
    assert lines[1] == lines[2]
    assert fault.lines == base.lines + 1


def test_inject_truncated_drops_the_last_detail_line_leaving_the_trailer(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    fault = inject_truncated(compiled, base, tmp_path / "out")
    lines = fault.path.read_text(encoding="ascii").splitlines()
    assert lines[-1].startswith("TRL000000004")  # the original count, now one too many
    assert fault.lines == base.lines - 1


def test_inject_late_leaves_content_unchanged(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    fault = inject_late(base, tmp_path / "out")
    assert fault.path.read_text(encoding="ascii") == base.path.read_text(encoding="ascii")
    assert fault.file_name == "pershing/GCUS_20260829_POS_001-late.dat"


# ---------------------------------------------------------------- the report


def test_proven_requires_every_scenario_to_pass():
    r = ChaosReport("pershing", "DB", "chaos-t1", "2026-09-12T00:00:00Z", "2026-08-29")
    assert r.proven is False  # no scenarios yet
    r.scenarios = [
        ScenarioResult("late", "f", None, "info", 1, True, True, "custodian_late_arrival", False),
        ScenarioResult("malformed", "f", "RECORD_TYPE_UNKNOWN", "error", 1, True, True, "chaos_scenario", True, True, True),
    ]
    assert r.proven is True
    r.scenarios.append(ScenarioResult("duplicate", "f", "MERGE_DUPLICATE_KEY", "error", 1, True, True, "chaos_scenario", True, True, False))
    assert r.proven is False  # did not recover


def test_proven_is_false_when_a_fault_is_not_detected():
    r = ChaosReport("pershing", "DB", "chaos-t1", "2026-09-12T00:00:00Z", "2026-08-29")
    r.scenarios = [ScenarioResult("malformed", "f", "RECORD_TYPE_UNKNOWN", "error", 0, False, False, None, True, False, None)]
    assert r.proven is False


# ---------------------------------------------------------------- run()


def test_run_proves_every_scenario_and_destroys_the_sandbox(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    spec = SandboxSpec("chaos-t1", "dev")
    executor = FakeExecutor(_all_scenarios_answers(spec, compiled, base))
    report = run(CONFIG, sample, date(2026, 8, 29), spec, executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())

    assert report.status == "ran" and report.error is None and report.destroyed
    assert report.proven is True
    by_name = {s.scenario: s for s in report.scenarios}
    assert set(by_name) == set(SCENARIOS)

    late = by_name["late"]
    assert late.fault_detected and late.alert_kind == "custodian_late_arrival" and late.retry_applicable is False and late.recovered is None

    malformed = by_name["malformed"]
    assert malformed.code == "RECORD_TYPE_UNKNOWN" and malformed.fault_detected and malformed.alert_kind == "chaos_scenario"
    assert malformed.retry_applicable and malformed.retried and malformed.recovered is True

    duplicate = by_name["duplicate"]
    assert duplicate.code == "MERGE_DUPLICATE_KEY" and duplicate.fault_detected and duplicate.recovered is True

    truncated = by_name["truncated"]
    assert truncated.code == "trailer_control_total" and truncated.fault_detected and truncated.recovered is True

    scripts = executor.scripts
    assert not any("CREATE TASK" in s or "CREATE PIPE" in s or "DATA METRIC FUNCTION" in s for s in scripts)
    assert any('"CONTROL"."ALERTS"' in s and "'custodian_late_arrival'" in s for s in scripts)
    assert any('"CONTROL"."ALERTS"' in s and "'chaos_scenario'" in s for s in scripts)
    assert any('"CONTROL"."CUSTODIAN_RUNS"' in s and "'late_arrival'" in s for s in scripts)
    assert not any(s.strip().upper().startswith("DELETE") or s.strip().upper().startswith("UPDATE") for s in scripts)  # no manual data surgery, ever
    assert scripts[-2].startswith("DROP DATABASE IF EXISTS") and "'destroyed', 'task_done'" in scripts[-1]

    out = tmp_path / "out"
    markdown = (out / "chaos.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Chaos scenarios: pershing 2026-08-29") and "Proven: yes" in markdown
    data = json.loads((out / "chaos.json").read_text(encoding="utf-8"))
    assert data["proven"] is True and len(data["scenarios"]) == 4


def test_run_reports_unproven_when_a_retry_does_not_recover(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    spec = SandboxSpec("chaos-t1", "dev")
    answers = _all_scenarios_answers(spec, compiled, base)
    answers[parse_problem_count_query(spec, compiled, "RECORD_TYPE_UNKNOWN", "pershing/GCUS_20260829_POS_001-malformed-retry.dat")] = [(1,)]  # still there after "retry"
    executor = FakeExecutor(answers)
    report = run(CONFIG, sample, date(2026, 8, 29), spec, executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())
    assert report.proven is False
    malformed = next(s for s in report.scenarios if s.scenario == "malformed")
    assert malformed.retried is True and malformed.recovered is False


def test_run_can_be_limited_to_one_scenario(tmp_path, sample):
    compiled, base = _compiled_and_base(sample, tmp_path)
    spec = SandboxSpec("chaos-t1", "dev")
    executor = FakeExecutor(_all_scenarios_answers(spec, compiled, base))
    report = run(CONFIG, sample, date(2026, 8, 29), spec, executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, scenarios=("duplicate",), monotonic=_monotonic())
    assert [s.scenario for s in report.scenarios] == ["duplicate"]


def test_run_requires_a_control_total_rule_for_the_truncated_scenario(tmp_path, sample):
    bare = tmp_path / "pershing_position.yaml"
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index("  - id: trailer_control_total")
    end = text.index("  - id: cusip_present")
    bare.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(ChaosError, match="control_total"):
        run(bare, sample, date(2026, 8, 29), SandboxSpec("chaos-t1", "dev"), FakeExecutor(), repo=REPO, out=tmp_path / "out")


def test_run_raises_the_compiler_s_own_error_for_a_broken_config(tmp_path, sample):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    with pytest.raises(DryRunError):
        run(bad, sample, date(2026, 8, 29), SandboxSpec("chaos-t1", "dev"), FakeExecutor(), repo=REPO, out=tmp_path / "out")


def test_write_report_writes_markdown_and_json(tmp_path):
    r = ChaosReport("pershing", "DB", "chaos-t1", "2026-09-12T00:00:00Z", "2026-08-29", destroyed=True)
    r.scenarios = [ScenarioResult("late", "f", None, "info", 1, True, True, "custodian_late_arrival", False)]
    markdown, data = write_report(r, tmp_path / "out")
    assert "custodian_late_arrival" in markdown.read_text(encoding="utf-8")
    assert render_markdown(r).startswith("# Chaos scenarios: pershing 2026-08-29")
    assert json.loads(data.read_text(encoding="utf-8"))["scenarios"][0]["scenario"] == "late"


# ---------------------------------------------------------------- CLI


def test_cli_runs_every_scenario(tmp_path, monkeypatch, sample):
    import astra_verification.cli as cli

    compiled, base = _compiled_and_base(sample, tmp_path)
    spec = SandboxSpec("chaos-cli-t1", "dev")
    executor = FakeExecutor(_all_scenarios_answers(spec, compiled, base))
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "out"
    code = main(["chaos", "run", "--config", str(CONFIG), "--sample", str(sample), "--business-date", "2026-08-29", "--environment", "dev", "--task", "chaos-cli-t1", "--repo", str(REPO), "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL), "--out", str(out)])
    assert code == 0
    assert (out / "chaos-cli-t1" / "chaos.md").exists()


def test_cli_scenario_flag_limits_the_run(tmp_path, monkeypatch, sample):
    import astra_verification.cli as cli

    compiled, base = _compiled_and_base(sample, tmp_path)
    spec = SandboxSpec("chaos-cli-t2", "dev")
    executor = FakeExecutor(_all_scenarios_answers(spec, compiled, base))
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "out"
    code = main(["chaos", "run", "--config", str(CONFIG), "--sample", str(sample), "--scenario", "late", "--business-date", "2026-08-29", "--environment", "dev", "--task", "chaos-cli-t2", "--repo", str(REPO), "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL), "--out", str(out)])
    assert code == 0
    data = json.loads((out / "chaos-cli-t2" / "chaos.json").read_text(encoding="utf-8"))
    assert [s["scenario"] for s in data["scenarios"]] == ["late"]


def test_cli_refuses_bad_inputs_before_touching_snowflake(tmp_path, monkeypatch, capsys):
    import astra_verification.cli as cli

    executor = FakeExecutor()
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    missing = tmp_path / "GCUS_20260829_POS_001.dat"
    code = main(["chaos", "run", "--config", str(CONFIG), "--sample", str(missing), "--business-date", "2026-08-29", "--environment", "dev", "--task", "chaos-cli-t3", "--repo", str(REPO), "--out", str(tmp_path / "out")])
    assert code == 2
    assert "no such sample file" in capsys.readouterr().err
    assert executor.scripts == []
