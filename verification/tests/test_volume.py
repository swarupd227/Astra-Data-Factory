"""3x volume test (S4.3.1): a custodian's daily set inflated N times, proving the 20-minute window end to end."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.dryrun import DryRunError, Sample, compile_and_render
from astra_verification.sandbox import SandboxSpec
from astra_verification.volume import (
    VOLUME_BUDGET_SECONDS,
    VolumeError,
    VolumeReport,
    SourceRun,
    custodian_run_statement,
    inflate,
    publish_statement,
    render_markdown,
    run,
    single_custodian,
    watermark_query,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
CDM_DDL = REPO / "domains" / "custodial" / "cdm" / "rendered" / "1.0" / "ddl.sql"
REFERENCE_BUNDLE = REPO / "releases" / "custodial-reference-data"
GOLD_BUNDLE = REPO / "releases" / "custodial-gold"


class FakeExecutor:
    def __init__(self, fail_on: str | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.fail_on = fail_on
        self.answers: list[tuple[str, list[tuple]]] = [
            ('"BRONZE"."PERSHING_POSITION_PROCESS"()', [("run-0001",)]),
            ('"CONTROL"."PUBLISH_GOLD"', [("1 business date(s) published for pershing after run r1 (publish p1)",)]),
            ('"GOLD"."WATERMARK"', [(4, "2026-09-11 12:00:00")]),
        ]

    def execute_script(self, sql: str) -> None:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"SQL compilation error: {self.fail_on}")
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"SQL compilation error: {self.fail_on}")
        self.queries.append(sql)
        for marker, rows in self.answers:
            if marker in sql:
                return rows
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def sample(tmp_path) -> Path:
    path = tmp_path / "GCUS_20260829_POS_001.dat"
    lines = ["HDR20260829RMT0000001R".ljust(120)] + ["DTL" + "12345678".ljust(10) + "037833100" + "000000000010000000+" + "000000227120000" + "20260829EQ".ljust(70)] * 4 + ["TRL000000004".ljust(120)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _monotonic():
    ticks = iter(range(0, 100000, 4))
    return lambda: float(next(ticks))


def _advancing_clock(start: datetime, step_seconds: float):
    calls = {"n": 0}

    def clock() -> datetime:
        t = start + timedelta(seconds=step_seconds * calls["n"])
        calls["n"] += 1
        return t

    return clock


# ---------------------------------------------------------------- inflate


def test_inflate_makes_factor_byte_identical_copies_renamed(tmp_path):
    compiled, _ = compile_and_render(CONFIG, REPO, tmp_path / "bundle")
    src = tmp_path / "GCUS_20260829_POS_001.dat"
    src.write_text("hello\n", encoding="ascii")
    sample = Sample(src, "pershing/GCUS_20260829_POS_001.dat", 1)
    inflated = inflate([sample], 3, tmp_path / "inflated")
    assert len(inflated) == 3
    assert [s.file_name for s in inflated] == [
        "pershing/GCUS_20260829_POS_001-x1.dat",
        "pershing/GCUS_20260829_POS_001-x2.dat",
        "pershing/GCUS_20260829_POS_001-x3.dat",
    ]
    assert all(s.path.read_text(encoding="ascii") == "hello\n" for s in inflated)
    assert all(s.lines == 1 for s in inflated)


def test_inflate_rejects_a_factor_below_one(tmp_path):
    with pytest.raises(VolumeError, match="factor must be at least 1"):
        inflate([], 0, tmp_path)


# ---------------------------------------------------------------- statement builders


def test_custodian_run_statement_shape():
    spec = SandboxSpec("vol-t1", "dev")
    stmt = custodian_run_statement(spec, "pershing", date(2026, 8, 29), "run-1", 6, datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc))
    assert 'INSERT INTO "ASTRA_DEV_SBX_VOL_T1"."CONTROL"."CUSTODIAN_RUNS"' in stmt
    assert "'run-1', 'pershing', '2026-08-29'::DATE, SYSDATE(), 'complete', 6," in stmt
    assert "'2026-09-11 12:00:00'::TIMESTAMP_NTZ, FALSE, NULL" in stmt


def test_publish_statement_shape():
    spec = SandboxSpec("vol-t1", "dev")
    assert publish_statement(spec, "pershing") == 'CALL "ASTRA_DEV_SBX_VOL_T1"."CONTROL"."PUBLISH_GOLD"(\'pershing\')'


def test_watermark_query_shape():
    spec = SandboxSpec("vol-t1", "dev")
    q = watermark_query(spec, "pershing", date(2026, 8, 29))
    assert '"GOLD"."WATERMARK"' in q and "CUSTODIAN_ID\" = 'pershing'" in q and "BUSINESS_DATE\" = '2026-08-29'::DATE" in q


# ---------------------------------------------------------------- the report


def test_within_budget_is_false_until_measured():
    r = VolumeReport("pershing", "DB", "MEDIUM", "vol-t1", "2026-09-11T00:00:00Z", 3, "2026-08-29")
    assert r.within_budget is False
    r.seconds_end_to_end = 1199.9
    assert r.within_budget is True
    r.seconds_end_to_end = 1200.1
    assert r.within_budget is False


def test_totals_sum_across_sources():
    r = VolumeReport("pershing", "DB", "MEDIUM", "vol-t1", "2026-09-11T00:00:00Z", 3, "2026-08-29", sources=[SourceRun("c1", "pershing_position", 3, 21), SourceRun("c2", "pershing_transaction", 3, 18)])
    assert r.total_files == 6 and r.total_lines == 39


# ---------------------------------------------------------------- run()


def test_run_end_to_end_within_budget(tmp_path, sample):
    executor = FakeExecutor()
    spec = SandboxSpec("vol-t1", "dev", ttl_minutes=60, warehouse_size="MEDIUM")
    report = run(
        [CONFIG], [sample], 3, date(2026, 8, 29), spec, executor,
        repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], gold_bundle=GOLD_BUNDLE, cdm_ddl=CDM_DDL,
        clock=_advancing_clock(datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc), 5.0), monotonic=_monotonic(),
    )
    assert report.status == "published" and report.error is None and report.destroyed
    assert report.custodian == "pershing" and report.warehouse_size == "MEDIUM" and report.factor == 3
    assert report.sources == [SourceRun(str(CONFIG), "pershing_position", 3, 18, "run-0001")]
    assert report.total_files == 3 and report.total_lines == 18
    assert report.publish_result == "1 business date(s) published for pershing after run r1 (publish p1)"
    assert report.gold_rows == 4 and report.watermark_published_at == "2026-09-11 12:00:00"
    assert report.seconds_end_to_end == 5.0 and report.within_budget
    assert [p.name for p in report.phases] == ["sandbox created", "deployed", "samples loaded", "processed", "published", "sandbox destroyed"]

    scripts = executor.scripts
    assert any("PUT file://" in s and "-x1.dat" in s for s in scripts)
    assert any("PUT file://" in s and "-x3.dat" in s for s in scripts)
    assert sum(1 for s in scripts if s.startswith("PUT file://")) == 3
    assert any('"CONTROL"."CUSTODIAN_RUNS"' in s for s in scripts)
    assert not any("CREATE TASK" in s or "CREATE PIPE" in s for s in scripts)
    assert scripts[-2].startswith('DROP DATABASE IF EXISTS') and "'destroyed', 'task_done'" in scripts[-1]

    out = tmp_path / "out"
    markdown = (out / "volume.md").read_text(encoding="utf-8")
    assert markdown.startswith("# 3x volume test: pershing 2026-08-29")
    assert "5.0 s** of a 1200 s (20-minute) budget — within budget" in markdown
    assert "warehouse size **MEDIUM**" in markdown
    data = json.loads((out / "volume.json").read_text(encoding="utf-8"))
    assert data["within_budget"] is True and data["warehouse_size"] == "MEDIUM"


def test_run_flags_a_result_over_the_twenty_minute_budget(tmp_path, sample):
    executor = FakeExecutor()
    spec = SandboxSpec("vol-t1", "dev")
    report = run(
        [CONFIG], [sample], 3, date(2026, 8, 29), spec, executor,
        repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], gold_bundle=GOLD_BUNDLE, cdm_ddl=CDM_DDL,
        clock=_advancing_clock(datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc), 1300.0), monotonic=_monotonic(),
    )
    assert report.seconds_end_to_end == 1300.0
    assert report.within_budget is False
    markdown = (tmp_path / "out" / "volume.md").read_text(encoding="utf-8")
    assert "OVER BUDGET" in markdown


def test_run_records_an_error_and_still_destroys_the_sandbox(tmp_path, sample):
    executor = FakeExecutor(fail_on='CALL "ASTRA_DEV_SBX_VOL_T1"."CONTROL"."PUBLISH_GOLD"')
    spec = SandboxSpec("vol-t1", "dev")
    report = run([CONFIG], [sample], 3, date(2026, 8, 29), spec, executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], gold_bundle=GOLD_BUNDLE, cdm_ddl=CDM_DDL, monotonic=_monotonic())
    assert report.status == "failed" and report.error.startswith("RuntimeError: SQL compilation error")
    assert report.destroyed and report.seconds_end_to_end is None and report.within_budget is False
    assert "'destroyed', 'task_failed'" in executor.scripts[-1]


def test_single_custodian_accepts_agreement():
    class _Compiled:
        def __init__(self, custodian: str) -> None:
            self.source = {"custodian": custodian}

    assert single_custodian([_Compiled("pershing"), _Compiled("pershing")]) == "pershing"


def test_single_custodian_rejects_disagreement():
    class _Compiled:
        def __init__(self, custodian: str) -> None:
            self.source = {"custodian": custodian}

    with pytest.raises(VolumeError, match="same custodian"):
        single_custodian([_Compiled("pershing"), _Compiled("otherco")])


def test_run_raises_the_compiler_s_own_error_for_a_broken_config(tmp_path, sample):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    with pytest.raises(DryRunError):
        run([bad], [sample], 3, date(2026, 8, 29), SandboxSpec("vol-t1", "dev"), FakeExecutor(), repo=REPO, out=tmp_path / "out")


def test_write_report_writes_markdown_and_json(tmp_path):
    r = VolumeReport("pershing", "DB", "MEDIUM", "vol-t1", "2026-09-11T00:00:00Z", 3, "2026-08-29", sources=[SourceRun("c", "pershing_position", 3, 21, "run-1")], seconds_end_to_end=100.0, publish_result="ok", destroyed=True)
    markdown, data = write_report(r, tmp_path / "out")
    text = markdown.read_text(encoding="utf-8")
    assert "100.0 s** of a 1200 s" in text and "within budget" in render_markdown(r)
    assert json.loads(data.read_text(encoding="utf-8"))["publish_result"] == "ok"


# ---------------------------------------------------------------- CLI


def test_cli_runs_within_budget(tmp_path, monkeypatch, sample):
    executor = FakeExecutor()
    monkeypatch.setattr("astra_verification.cli._executor", lambda: executor)
    out = tmp_path / "out"
    code = main([
        "volume", "run",
        "--config", str(CONFIG),
        "--sample", str(sample),
        "--factor", "3",
        "--business-date", "2026-08-29",
        "--environment", "dev",
        "--repo", str(REPO),
        "--out", str(out),
        "--task", "vol-cli-t1",
        "--gold-bundle", str(GOLD_BUNDLE),
    ])
    assert code == 0
    assert (out / "vol-cli-t1" / "volume.md").exists()


def test_cli_refuses_a_broken_config_before_touching_snowflake(tmp_path, monkeypatch, capsys, sample):
    executor = FakeExecutor()
    monkeypatch.setattr("astra_verification.cli._executor", lambda: executor)
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    code = main([
        "volume", "run",
        "--config", str(bad),
        "--sample", str(sample),
        "--business-date", "2026-08-29",
        "--environment", "dev",
        "--repo", str(REPO),
        "--out", str(tmp_path / "out"),
        "--task", "vol-cli-t2",
    ])
    assert code == 2
    assert "target_lag_minutes" in capsys.readouterr().err
    assert executor.scripts == []


def test_cli_json_output(tmp_path, monkeypatch, capsys, sample):
    executor = FakeExecutor()
    monkeypatch.setattr("astra_verification.cli._executor", lambda: executor)
    code = main([
        "volume", "run",
        "--config", str(CONFIG),
        "--sample", str(sample),
        "--business-date", "2026-08-29",
        "--environment", "dev",
        "--repo", str(REPO),
        "--out", str(tmp_path / "out"),
        "--task", "vol-cli-t3",
        "--gold-bundle", str(GOLD_BUNDLE),
        "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["custodian"] == "pershing" and payload["within_budget"] is True
