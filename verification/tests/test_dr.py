"""DR drill (S4.3.3): the standardized zone destroyed and restored from retained files, proving RTO and RPO."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.dryrun import DryRunError
from astra_verification.sandbox import SandboxSpec
from astra_verification.dr import (
    DrError,
    DrReport,
    bronze_count_query,
    drop_zone_statements,
    recreate_zone_statements,
    render_markdown,
    run,
    silver_count_query,
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


def _monotonic():
    ticks = iter(range(0, 100000, 4))
    return lambda: float(next(ticks))


class FakeExecutor:
    """Both the baseline and the restored row-count queries answer the same fixed counts unless overridden — a clean restore by default.

    `fail_on` names a marker and which occurrence of it should fail (1-based) —
    several statements this drill issues (creating BRONZE, staging) are issued
    once before the disaster and again during the restore, so a plain substring
    match cannot tell the two apart.
    """

    def __init__(self, bronze: int = 4, silver: int = 2, fail_on: tuple[str, int] | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.fail_on = fail_on
        self._fail_on_seen = 0
        self.bronze = bronze
        self.silver = silver

    def execute_script(self, sql: str) -> None:
        if self.fail_on and self.fail_on[0] in sql:
            self._fail_on_seen += 1
            if self._fail_on_seen == self.fail_on[1]:
                raise RuntimeError(f"SQL compilation error: {self.fail_on[0]}")
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        if '"BRONZE"."PERSHING_POSITION_PROCESS"()' in sql:
            return [("run-0001",)]
        if '"BRONZE"."PERSHING_POSITION_DETAIL"' in sql:
            return [(self.bronze,)]
        if '"SILVER"."PERSHING_POSITION_DETAIL"' in sql:
            return [(self.silver,)]
        return [(0,)]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- statement builders


def test_drop_zone_statements_drop_bronze_and_silver():
    spec = SandboxSpec("dr-t1", "dev")
    statements = drop_zone_statements(spec)
    assert statements == ['DROP SCHEMA IF EXISTS "ASTRA_DEV_SBX_DR_T1"."BRONZE"', 'DROP SCHEMA IF EXISTS "ASTRA_DEV_SBX_DR_T1"."SILVER"']


def test_recreate_zone_statements_match_how_the_sandbox_first_made_them():
    spec = SandboxSpec("dr-t1", "dev")
    statements = recreate_zone_statements(spec)
    assert all("WITH MANAGED ACCESS DATA_RETENTION_TIME_IN_DAYS = 0" in s for s in statements)
    assert '"ASTRA_DEV_SBX_DR_T1"."BRONZE"' in statements[0] and '"ASTRA_DEV_SBX_DR_T1"."SILVER"' in statements[1]


# ---------------------------------------------------------------- the report


def test_within_rto_needs_a_measured_restore_under_the_target():
    r = DrReport("pershing", "DB", "dr-t1", "2026-09-12T00:00:00Z", rto_minutes=5, rpo_minutes=0)
    assert r.within_rto is False
    r.seconds_to_restore = 299.0
    assert r.within_rto is True
    r.seconds_to_restore = 301.0
    assert r.within_rto is False


def test_within_rpo_needs_status_restored_and_no_rows_lost():
    r = DrReport("pershing", "DB", "dr-t1", "2026-09-12T00:00:00Z", rto_minutes=5, rpo_minutes=0)
    assert r.within_rpo is False  # nothing restored yet
    r.baseline = {"bronze": 4, "silver": 2}
    r.restored = {"bronze": 4, "silver": 2}
    assert r.within_rpo is True and r.rows_lost == {}
    r.restored = {"bronze": 4, "silver": 1}
    assert r.within_rpo is False
    assert r.rows_lost == {"silver": 1}


def test_proven_requires_both_rto_and_rpo():
    r = DrReport("pershing", "DB", "dr-t1", "2026-09-12T00:00:00Z", rto_minutes=5, rpo_minutes=0)
    r.baseline = r.restored = {"bronze": 4, "silver": 2}
    r.seconds_to_restore = 10.0
    assert r.proven is True
    r.seconds_to_restore = 1000.0
    assert r.proven is False


# ---------------------------------------------------------------- run()


def test_run_measures_a_clean_restore_and_destroys_the_sandbox(tmp_path, sample):
    executor = FakeExecutor(bronze=4, silver=2)
    spec = SandboxSpec("dr-t1", "dev")
    report = run(CONFIG, [sample], rto_minutes=5, rpo_minutes=0, spec=spec, executor=executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())

    assert report.status == "restored" and report.error is None and report.destroyed
    assert report.baseline == {"bronze": 4, "silver": 2}
    assert report.restored == {"bronze": 4, "silver": 2}
    assert report.rows_lost == {}
    assert report.within_rpo is True
    assert report.seconds_to_restore is not None and report.seconds_to_restore > 0
    assert report.proven is True

    scripts = executor.scripts
    assert 'DROP SCHEMA IF EXISTS "ASTRA_DEV_SBX_DR_T1"."BRONZE"' in scripts
    assert 'DROP SCHEMA IF EXISTS "ASTRA_DEV_SBX_DR_T1"."SILVER"' in scripts
    assert 'CREATE SCHEMA "ASTRA_DEV_SBX_DR_T1"."BRONZE" WITH MANAGED ACCESS DATA_RETENTION_TIME_IN_DAYS = 0' in scripts

    def index(marker: str) -> int:
        return next(i for i, s in enumerate(scripts) if marker in s)

    def last_index(marker: str) -> int:
        return max(i for i, s in enumerate(scripts) if marker in s)

    # CREATE SCHEMA "..."."BRONZE" is issued twice: once when the sandbox is first made, again in the restore.
    assert sum(1 for s in scripts if s.startswith('CREATE SCHEMA "ASTRA_DEV_SBX_DR_T1"."BRONZE"')) == 2
    assert index('DROP SCHEMA IF EXISTS "ASTRA_DEV_SBX_DR_T1"."SILVER"') < last_index('CREATE SCHEMA "ASTRA_DEV_SBX_DR_T1"."BRONZE"')
    assert not any("CREATE TASK" in s or "CREATE PIPE" in s or "DATA METRIC FUNCTION" in s for s in scripts)
    assert scripts[-2].startswith("DROP DATABASE IF EXISTS") and "'destroyed', 'task_done'" in scripts[-1]

    out = tmp_path / "out"
    markdown = (out / "dr.md").read_text(encoding="utf-8")
    assert markdown.startswith("# DR drill: pershing") and "RPO met" in markdown and "within RTO" in markdown
    data = json.loads((out / "dr.json").read_text(encoding="utf-8"))
    assert data["proven"] is True and data["rows_lost"] == {}


def test_run_flags_data_loss_when_the_restore_falls_short(tmp_path, sample, monkeypatch):
    spec = SandboxSpec("dr-t1", "dev")
    executor = FakeExecutor(bronze=4, silver=2)

    real_query = FakeExecutor.query
    calls = {"silver": 0}

    def flaky_query(self, sql):
        if '"SILVER"."PERSHING_POSITION_DETAIL"' in sql:
            calls["silver"] += 1
            return [(2,)] if calls["silver"] == 1 else [(1,)]  # baseline sees 2, restored sees only 1
        return real_query(self, sql)

    monkeypatch.setattr(FakeExecutor, "query", flaky_query)
    report = run(CONFIG, [sample], rto_minutes=5, rpo_minutes=0, spec=spec, executor=executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())
    assert report.within_rpo is False
    assert report.rows_lost == {"silver": 1}
    assert report.proven is False


def test_run_flags_rto_breach_when_the_restore_takes_too_long(tmp_path, sample):
    executor = FakeExecutor(bronze=4, silver=2)
    spec = SandboxSpec("dr-t1", "dev")
    report = run(CONFIG, [sample], rto_minutes=0.0001, rpo_minutes=0, spec=spec, executor=executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())
    assert report.within_rto is False
    assert report.proven is False


def test_run_records_an_error_and_still_destroys_the_sandbox(tmp_path, sample):
    executor = FakeExecutor(fail_on=('CREATE SCHEMA "ASTRA_DEV_SBX_DR_T1"."BRONZE"', 2))  # the restore's recreation, not the sandbox's own initial one
    spec = SandboxSpec("dr-t1", "dev")
    report = run(CONFIG, [sample], rto_minutes=5, rpo_minutes=0, spec=spec, executor=executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, monotonic=_monotonic())
    assert report.status == "failed" and report.error.startswith("RuntimeError: SQL compilation error")
    assert report.destroyed and report.proven is False
    assert "'destroyed', 'task_failed'" in executor.scripts[-1]


def test_run_raises_the_compiler_s_own_error_for_a_broken_config(tmp_path, sample):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    with pytest.raises(DryRunError):
        run(bad, [sample], rto_minutes=5, rpo_minutes=0, spec=SandboxSpec("dr-t1", "dev"), executor=FakeExecutor(), repo=REPO, out=tmp_path / "out")


def test_write_report_writes_markdown_and_json(tmp_path):
    r = DrReport("pershing", "DB", "dr-t1", "2026-09-12T00:00:00Z", rto_minutes=5, rpo_minutes=0, baseline={"bronze": 4, "silver": 2}, restored={"bronze": 4, "silver": 2}, seconds_to_restore=10.0, destroyed=True)
    markdown, data = write_report(r, tmp_path / "out")
    text = markdown.read_text(encoding="utf-8")
    assert "10.0 s** to restore" in text
    assert render_markdown(r) == text
    assert json.loads(data.read_text(encoding="utf-8"))["proven"] is True


# ---------------------------------------------------------------- CLI


def test_cli_runs_a_clean_restore(tmp_path, monkeypatch, sample):
    import astra_verification.cli as cli

    executor = FakeExecutor(bronze=4, silver=2)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    out = tmp_path / "out"
    code = main([
        "dr", "run",
        "--config", str(CONFIG), "--sample", str(sample),
        "--rto-minutes", "5", "--rpo-minutes", "0",
        "--environment", "dev", "--task", "dr-cli-t1",
        "--repo", str(REPO), "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL),
        "--out", str(out),
    ])
    assert code == 0
    assert (out / "dr-cli-t1" / "dr.md").exists()


def test_cli_json_output(tmp_path, monkeypatch, capsys, sample):
    import astra_verification.cli as cli

    executor = FakeExecutor(bronze=4, silver=2)
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main([
        "dr", "run",
        "--config", str(CONFIG), "--sample", str(sample),
        "--rto-minutes", "5", "--rpo-minutes", "0",
        "--environment", "dev", "--task", "dr-cli-t2",
        "--repo", str(REPO), "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL),
        "--out", str(tmp_path / "out"), "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["custodian"] == "pershing" and payload["proven"] is True


def test_cli_refuses_bad_inputs_before_touching_snowflake(tmp_path, monkeypatch, capsys):
    import astra_verification.cli as cli

    executor = FakeExecutor()
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    missing = tmp_path / "GCUS_20260829_POS_001.dat"
    code = main(["dr", "run", "--config", str(CONFIG), "--sample", str(missing), "--rto-minutes", "5", "--rpo-minutes", "0", "--environment", "dev", "--task", "dr-cli-t3", "--repo", str(REPO), "--out", str(tmp_path / "out")])
    assert code == 2
    assert "no such sample file" in capsys.readouterr().err
    assert executor.scripts == []
