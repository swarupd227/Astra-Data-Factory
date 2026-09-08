"""Dry-run a config in a sandbox (S4.1.1): sample files in, a report out, the sandbox gone."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.dryrun import BUDGET_SECONDS, DryRunError, compile_and_render, dry_run, load_statements, render_markdown, samples_for, sandbox_bundle
from astra_verification.sandbox import SandboxSpec

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
CDM_DDL = REPO / "domains" / "custodial" / "cdm" / "rendered" / "1.0" / "ddl.sql"
REFERENCE_BUNDLE = REPO / "releases" / "custodial-reference-data"

METADATA_COLUMNS = ["FILE_NAME", "LINE_COUNT", "HEADER_COUNT", "DETAIL_COUNT", "TRAILER_COUNT", "EXCLUDED_ROWS", "FIELD_PROBLEMS", "FILE_PROBLEMS", "HEADER_FILE_DATE", "HEADER_REMOTE_ID", "HEADER_REFRESH_FLAG", "TRAILER_DETAIL_COUNT", "FIRST_LINE_AT", "LAST_LINE_AT"]


class FakeExecutor:
    """Answers the dry run's queries from markers; records every statement in order."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.fail_on = fail_on
        self.answers: list[tuple[str, list[tuple]]] = [
            ('"BRONZE"."PERSHING_POSITION_PROCESS"()', [("run-0001",)]),
            ("INFORMATION_SCHEMA.COLUMNS", [(c,) for c in METADATA_COLUMNS]),
            ('COUNT(*) FROM "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_DETAIL"', [(4,)]),
            ('SELECT * FROM "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_FILE_METADATA"', [("pershing/GCUS_20260829_POS_001.dat", 7, 1, 4, 1, 1, 0, 0, "2026-08-29", "RMT0000001", "R", 5, "2026-09-08 07:00:00", "2026-09-08 07:00:00")]),
            ('"PERSHING_POSITION_PARSE_PROBLEMS" GROUP BY', [("RECORD_TYPE_UNKNOWN", "record", 1)]),
            ('"EXCEPTIONS"."PERSHING_POSITION" WHERE "RUN_ID"', [("RECORD_TYPE_UNKNOWN", "record", "parse", 1), ("ACCOUNT_NOT_FOUND", "record", "resolution", 2), ("MERGE_MODE_UNKNOWN", "file", "merge", 0)]),
            ('"BRONZE"."PERSHING_POSITION_RUNS" WHERE "RUN_ID"', [(1, 1, 0, 4, 2, 3, 0, 3, 0)]),
            ('"BRONZE"."PERSHING_POSITION_FILES" ORDER BY', [("pershing/GCUS_20260829_POS_001.dat", "merged", 7)]),
            ('"SILVER"."PERSHING_POSITION_DETAIL" WHERE "RETIRED_AT" IS NULL', [(4,)]),
            ('"SILVER"."POSITION" WHERE "CUSTODIAN_ID"', [(2,)]),
            ('COALESCE("TRAILER_DETAIL_COUNT", 0) - COALESCE("DETAIL_COUNT", 0) FROM', [("pershing/GCUS_20260829_POS_001.dat", 5, 4, 1)]),
            ('AS "GAP"', [("pershing/GCUS_20260829_POS_001.dat", 5, 4, 1)]),  # the rendered control-total test returns the file with its gap
        ]

    def execute_script(self, sql: str) -> None:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"SQL compilation error: {self.fail_on}")
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
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
    lines = ["HDR20260829RMT0000001R".ljust(120)] + ["DTL" + "12345678".ljust(10) + "037833100" + "000000000010000000+" + "000000227120000" + "20260829EQ".ljust(70)] * 4 + ["XXX".ljust(120), "TRL000000005".ljust(120)]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _clock():
    ticks = iter(range(0, 10000, 4))
    return lambda: float(next(ticks))


# ---------------------------------------------------------------- inputs


def test_the_config_is_compiled_and_rendered_as_the_pipeline_would(tmp_path):
    compiled, bundle = compile_and_render(CONFIG, REPO, tmp_path / "bundle")
    assert compiled.id == "pershing_position" and bundle.name == "pershing-position" and len(bundle.steps) == 11
    trimmed = sandbox_bundle(bundle)
    assert [s.name for s in trimmed.steps] == ["bronze_pershing_position.sql", "silver_pershing_position.sql", "pershing_position_lines.sql", "pershing_position_parse.sql", "pershing_position_intake.sql", "pershing_position_merge.sql", "pershing_position_resolve.sql", "pershing_position_process.sql"]


def test_a_broken_config_is_refused_with_its_problems(tmp_path):
    bad = tmp_path / "pershing_position.yaml"
    bad.write_text(CONFIG.read_text(encoding="utf-8").replace("target_lag_minutes: 10", "target_lag_minutes: ten"), encoding="utf-8")
    with pytest.raises(DryRunError) as excinfo:
        compile_and_render(bad, REPO, tmp_path / "bundle")
    assert any("target_lag_minutes" in p.message for p in excinfo.value.problems)


def test_samples_are_named_as_the_pipe_would_see_them_and_must_match_a_pattern(tmp_path, sample):
    compiled, _ = compile_and_render(CONFIG, REPO, tmp_path / "bundle")
    files = samples_for(compiled, [sample])
    assert files[0].file_name == "pershing/GCUS_20260829_POS_001.dat" and files[0].lines == 7
    other = tmp_path / "positions.csv"
    other.write_text("x\n", encoding="ascii")
    with pytest.raises(DryRunError, match="matches none of the delivery patterns"):
        samples_for(compiled, [other])
    with pytest.raises(DryRunError, match="no such sample file"):
        samples_for(compiled, [tmp_path / "missing.dat"])


def test_samples_are_loaded_the_way_the_pipe_loads_them(tmp_path, sample):
    compiled, _ = compile_and_render(CONFIG, REPO, tmp_path / "bundle")
    spec = SandboxSpec("dryrun-t1", "dev")
    statements = load_statements(spec, compiled, samples_for(compiled, [sample]))
    copy, log = statements
    assert copy.startswith('COPY INTO "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_RAW_LINES" ("FILE_NAME", "ROW_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT")')
    assert "SELECT METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, $1, METADATA$FILE_CONTENT_KEY, METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME" in copy
    assert 'FROM @"ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."DRYRUN"/pershing/' in copy and "FILES = ('GCUS_20260829_POS_001.dat')" in copy
    assert "FIELD_DELIMITER = NONE" in copy and "SKIP_BLANK_LINES = FALSE" in copy and "TRIM_SPACE = FALSE" in copy  # the foundation's RAW_LINES format
    assert "INSERT INTO \"ASTRA_DEV_SBX_DRYRUN_T1\".\"CONTROL\".\"FILE_LOAD_LOG\"" in log and "'pershing/GCUS_20260829_POS_001.dat', SYSDATE(), 'LOADED'" in log and ", 7, 'Dry run" in log


# ------------------------------------------------------------------ the run


def test_the_dry_run_deploys_loads_parses_processes_reports_and_destroys(tmp_path, sample):
    executor = FakeExecutor()
    spec = SandboxSpec("dryrun-t1", "dev", ttl_minutes=30)
    report = dry_run(CONFIG, [sample], spec, executor, repo=REPO, out=tmp_path / "out", extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, clock=lambda: datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc), monotonic=_clock())
    scripts = executor.scripts
    # in order: sandbox, control tables, canonical model, reference bundle, source bundle, stage, put, copy, log, refresh, then destroy
    def index(marker: str) -> int:
        return next(i for i, s in enumerate(scripts) if marker in s)

    assert index('CREATE DATABASE "ASTRA_DEV_SBX_DRYRUN_T1"') < index('CREATE SCHEMA "ASTRA_DEV_SBX_DRYRUN_T1"."REFERENCE"') < index('CREATE ICEBERG TABLE "ASTRA_DEV_SBX_DRYRUN_T1"."CONTROL"."FILE_LOAD_LOG" LIKE "ASTRA_DEV"."CONTROL"."FILE_LOAD_LOG"')
    assert index('INSERT INTO "ASTRA_DEV_SBX_DRYRUN_T1"."CONTROL"."REJECTION_CODES" SELECT * FROM "ASTRA_DEV"."CONTROL"."REJECTION_CODES"') < index('"SILVER"."POSITION"')  # taxonomy copied, canonical model created
    assert index('"REFERENCE"."SECURITY_MASTER"') < index('"BRONZE"."PERSHING_POSITION_RAW_LINES"')  # reference tables before the source bundle
    assert not any("CREATE PIPE" in s or "CREATE TASK" in s or "DATA METRIC FUNCTION" in s for s in scripts)  # what a sandbox cannot take
    assert all("ASTRA_DEV_SBX_DRYRUN_T1_WH" in s for s in scripts if "CREATE OR REPLACE DYNAMIC ICEBERG TABLE" in s)  # every tier maps to the sandbox warehouse
    assert index('CREATE STAGE "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."DRYRUN"') < index("PUT file://") < index('COPY INTO "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_RAW_LINES"') < index('"CONTROL"."FILE_LOAD_LOG" ("FILE_NAME"')
    assert 'PUT file://' in scripts[index("PUT file://")] and '@"ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."DRYRUN"/pershing/ AUTO_COMPRESS = FALSE' in scripts[index("PUT file://")]
    refreshes = [s for s in scripts if s.endswith(" REFRESH")]
    assert refreshes == [f'ALTER DYNAMIC TABLE "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."{t}" REFRESH' for t in ("PERSHING_POSITION_DETAIL", "PERSHING_POSITION_PARSE_PROBLEMS", "PERSHING_POSITION_FILE_METADATA")]
    assert executor.queries[0] == 'CALL "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_PROCESS"()'
    assert scripts[-3].startswith('DROP WAREHOUSE IF EXISTS "ASTRA_DEV_SBX_DRYRUN_T1_WH"') and scripts[-2].startswith('DROP DATABASE IF EXISTS "ASTRA_DEV_SBX_DRYRUN_T1"') and "'destroyed', 'task_done'" in scripts[-1]

    assert report.status == "ran" and report.run_id == "run-0001" and report.destroyed and report.error is None
    assert report.parsed == {"detail": 4} and report.silver_rows == 4 and report.canonical_rows == 2
    assert report.parse_problems == [{"code": "RECORD_TYPE_UNKNOWN", "level": "record", "rows": 1}]
    assert report.rejected_rows == 3 and {e["code"] for e in report.exceptions} == {"RECORD_TYPE_UNKNOWN", "ACCOUNT_NOT_FOUND", "MERGE_MODE_UNKNOWN"}
    assert report.ledger == {"files_registered": 1, "files_merged": 1, "files_rejected": 0, "rows_merged": 4, "rows_projected": 2, "rows_rejected": 3, "exceptions_file": 0, "exceptions_record": 3, "exceptions_field": 0}
    assert report.files[0]["FILE_NAME"] == "pershing/GCUS_20260829_POS_001.dat" and report.files[0]["EXCLUDED_ROWS"] == "1" and report.files[0]["status"] == "merged"
    assert report.control_totals == [{"rule": "trailer_control_total", "file": "pershing/GCUS_20260829_POS_001.dat", "trailer": "5", "actual": "4", "gap": "1"}]
    tests = {t["test"]: t for t in report.tests}
    assert len(tests) == 12 and tests["tests/pershing_position_dq_trailer_control_total.sql"]["passed"] is False and tests["tests/pershing_position_files_not_stuck.sql"]["passed"] is True
    assert [p.name for p in report.phases] == ["sandbox created", "deployed", "samples loaded", "parsed", "processed", "reported", "sandbox destroyed"]
    assert report.seconds > 0 and report.within_budget and report.to_dict()["budget_seconds"] == BUDGET_SECONDS

    out = tmp_path / "out"
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Dry run: pershing_position\n") and "sandbox `ASTRA_DEV_SBX_DRYRUN_T1` (destroyed: yes)" in markdown
    assert "| detail | 4 |" in markdown and "| `ACCOUNT_NOT_FOUND` | record | resolution | 2 |" in markdown
    assert "| `trailer_control_total` | `pershing/GCUS_20260829_POS_001.dat` | 5 | 4 | 1 |" in markdown
    assert "| `tests/pershing_position_dq_trailer_control_total.sql` | FAIL | 1 |" in markdown and "| `pershing/GCUS_20260829_POS_001.dat` | 7 | merged | 7 | 1 | 0 | 0 |" in markdown
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["status"] == "ran" and data["rejected_rows"] == 3 and data["destroyed"] is True and data["samples"] == [{"file": "pershing/GCUS_20260829_POS_001.dat", "lines": 7}]


def test_a_failure_still_destroys_the_sandbox_and_is_in_the_report(tmp_path, sample):
    executor = FakeExecutor(fail_on='ALTER DYNAMIC TABLE "ASTRA_DEV_SBX_DRYRUN_T1"."BRONZE"."PERSHING_POSITION_DETAIL" REFRESH')
    report = dry_run(CONFIG, [sample], SandboxSpec("dryrun-t1", "dev"), executor, repo=REPO, out=tmp_path / "out", monotonic=_clock())
    assert report.status == "failed" and report.error.startswith("RuntimeError: SQL compilation error") and report.destroyed and report.run_id == ""
    assert "'destroyed', 'task_failed'" in executor.scripts[-1] and "dry run pershing_position" in executor.scripts[-1]
    markdown = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "**Stopped:** RuntimeError: SQL compilation error" in markdown and "The run did not reach the exception store." in markdown
    assert [p.name for p in report.phases] == ["sandbox created", "deployed", "samples loaded", "sandbox destroyed"]


def test_over_budget_is_said_plainly(tmp_path, sample):
    slow = iter(range(0, 100000, 200))
    report = dry_run(CONFIG, [sample], SandboxSpec("dryrun-t1", "dev"), FakeExecutor(), repo=REPO, out=tmp_path / "out", monotonic=lambda: float(next(slow)))
    assert not report.within_budget and ", over budget" in render_markdown(report)


# ------------------------------------------------------------------ the CLI


def test_cli_runs_a_dry_run_and_points_at_the_report(tmp_path, sample, monkeypatch, capsys):
    import astra_verification.cli as cli

    executor = FakeExecutor()
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main(["dryrun", "--config", str(CONFIG), "--sample", str(sample), "--environment", "dev", "--task", "dryrun-t1", "--repo", str(REPO), "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "dry run of pershing_position in ASTRA_DEV_SBX_DRYRUN_T1: ran," in out and "sandbox destroyed: yes" in out
    assert "parsed: detail 4" in out and "rejected rows by code: RECORD_TYPE_UNKNOWN 1, ACCOUNT_NOT_FOUND 2" in out
    assert "control-total gaps: 1 file(s) with a gap" in out and "DQ: 11 of 12 tests passed; failing: tests/pershing_position_dq_trailer_control_total.sql" in out
    assert re.search(r"report: .*dryrun-t1[\\/]report\.md", out)
    assert (tmp_path / "out" / "dryrun-t1" / "report.json").is_file()


def test_cli_refuses_bad_inputs_before_touching_snowflake(tmp_path, monkeypatch, capsys):
    import astra_verification.cli as cli

    executor = FakeExecutor()
    monkeypatch.setattr(cli, "_executor", lambda: executor)
    missing = tmp_path / "GCUS_20260829_POS_001.dat"
    assert main(["dryrun", "--config", str(CONFIG), "--sample", str(missing), "--environment", "dev", "--task", "dryrun-t1", "--repo", str(REPO), "--out", str(tmp_path / "out")]) == 2
    assert "no such sample file" in capsys.readouterr().err and executor.scripts == []
