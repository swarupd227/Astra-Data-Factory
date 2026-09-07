"""Historical migration with SnowConvert AI (S3.3.1): the factory drives the four phases and stores the results with the release."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.lint import lint_bundle
from astra_data.migration import CommandResult, load_migration, rewrite_ddl, run_migration, subprocess_runner, validate_paths

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "migrations" / "examples" / "loader_sqlserver.yaml"
TEXT = EXAMPLE.read_text(encoding="utf-8")

# A stand-in for the SnowConvert AI CLI: it answers --version, writes a T-SQL extract, converts it to
# Snowflake DDL with one EWI marker, and reports counts for migrate and validate. Every phase fails
# when FAKE_SNOWCT_FAIL names it.
FAKE_TOOL = '''
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
    print("SnowConvert AI CLI 3.2.0 (fake)"); sys.exit(0)
phase = args[0]
if os.environ.get("FAKE_SNOWCT_FAIL") == phase:
    print(f"{phase}: simulated failure; connection was " + os.environ.get("LOADER_SQLSERVER_CONNECTION", ""), file=sys.stderr); sys.exit(3)
out = Path(args[args.index("--output") + 1])
out.mkdir(parents=True, exist_ok=True)
if phase == "extract":
    (out / "dbo.LoaderPosition.sql").write_text("CREATE TABLE dbo.LoaderPosition (PositionId BIGINT NOT NULL, Quantity DECIMAL(18,5));", encoding="utf-8")
    print("extracted 3 objects from " + args[args.index("--database") + 1])
elif phase == "convert":
    src = Path(args[args.index("--input") + 1])
    (out / "dbo").mkdir(exist_ok=True)
    (out / "dbo" / "LoaderPosition.sql").write_text(
        "USE SCHEMA dbo;\\nCREATE OR REPLACE TABLE dbo.LoaderPosition (\\n  PositionId NUMBER(38,0) NOT NULL,\\n  Quantity NUMBER(18,5)\\n);\\n", encoding="utf-8")
    (out / "dbo" / "LoaderAccount.sql").write_text(
        "CREATE OR REPLACE TABLE \\"dbo\\".\\"LoaderAccount\\" (\\n  AccountId NUMBER(38,0) NOT NULL,\\n  LoadedAt TIMESTAMP_NTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP() /*** SSC-EWI-0001 - !!!RESOLVE EWI!!! SYSUTCDATETIME() ***/\\n);\\n", encoding="utf-8")
    (out / "dbo" / "vw_Positions.sql").write_text("CREATE OR REPLACE VIEW dbo.vw_Positions AS SELECT p.PositionId FROM dbo.LoaderPosition p JOIN dbo.LoaderAccount a ON a.AccountId = p.AccountId;\\n", encoding="utf-8")
    (out / "Reports").mkdir(exist_ok=True)
    (out / "Reports" / "AssessmentReport.json").write_text('{"objects": 3, "ewis": 1}', encoding="utf-8")
    print("converted 3 objects, 1 EWI")
elif phase == "migrate":
    (out / "migrate.json").write_text('{"rows": 3}', encoding="utf-8"); print("migrated 3 rows into " + args[args.index("--target-database") + 1])
elif phase == "validate":
    (out / "validate.json").write_text('{"tables": 2, "mismatches": 0}', encoding="utf-8"); print("validated 2 tables, 0 mismatches")
sys.exit(0)
'''


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def tool(tmp_path) -> list[str]:
    script = tmp_path / "fake_snowct.py"
    script.write_text(FAKE_TOOL, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def migration(tmp_path):
    m, problems = load_migration(EXAMPLE, REPO)
    assert problems == [] and m is not None
    return m


ENV = {"LOADER_SQLSERVER_CONNECTION": "Server=loader-sql;Database=LoaderDB;User Id=svc;Password=hunter2", "SNOWCONVERT_LICENSE": "LIC-123"}


def _env(**extra: str) -> dict[str, str]:
    """The secrets on top of the real environment: a subprocess needs the system variables to start."""
    import os

    return {**os.environ, **ENV, **extra}


def _clock(start: datetime):
    moments = [start + timedelta(seconds=i * 7) for i in range(20)]
    return lambda: moments.pop(0)


# ------------------------------------------------------------- the file


def test_the_example_migration_file_loads_and_names_no_secret(migration):
    assert (migration.id, migration.source.platform, migration.source.database, migration.source.schemas, migration.target_schema) == ("loader_sqlserver", "sql_server", "LoaderDB", ("dbo",), "ARCHIVE")
    assert migration.source.connection_env == "LOADER_SQLSERVER_CONNECTION" and migration.snowconvert.license_env == "SNOWCONVERT_LICENSE"
    assert set(migration.snowconvert.phases) == {"extract", "convert", "migrate", "validate"} and migration.bundle_name == "loader-sqlserver-migration"
    assert "Password" not in TEXT and "Server=" not in TEXT


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda t: t.replace("connection_env: LOADER_SQLSERVER_CONNECTION", "connection_env: Server=x;Database=y"), "source.connection_env: 'Server=x;Database=y' does not match"),
        (lambda t: t.replace("{extract_dir}", "{output_dir}"), "snowconvert.phases.extract[9]: unknown placeholder {output_dir}; placeholders are"),
        (lambda t: t.replace("  license_env: SNOWCONVERT_LICENSE\n", ""), "snowconvert.phases.extract[11] uses {license} but snowconvert.license_env names no variable"),
        (lambda t: t.replace("  schema: ARCHIVE\n", ""), "target: missing required field schema"),
        (lambda t: t.replace("    validate: [", "    verify: ["), "snowconvert.phases: unknown field verify"),
    ],
)
def test_the_validator_names_what_is_wrong(tmp_path, mutate, expected):
    path = tmp_path / "loader_sqlserver.yaml"
    path.write_text(mutate(TEXT), encoding="utf-8")
    m, problems = load_migration(path, tmp_path)
    assert m is None and any(p.message.startswith(expected) for p in problems), [p.message for p in problems]


def test_validate_paths_walks_a_directory(tmp_path):
    migrations, problems = validate_paths([REPO / "migrations"], REPO)
    assert problems == [] and [m.id for m in migrations] == ["loader_sqlserver"]


# ------------------------------------------------------------ the DDL


def test_converted_ddl_is_pointed_at_the_archive_store(migration):
    sql, created = rewrite_ddl(
        migration,
        'USE SCHEMA dbo;\nCREATE OR REPLACE TABLE dbo.LoaderPosition (Id NUMBER);\nCREATE OR REPLACE VIEW "dbo"."vw_Positions" AS SELECT * FROM dbo.LoaderPosition p JOIN other.Thing t ON t.id = p.id;\n',
    )
    assert "USE SCHEMA" not in sql
    assert 'CREATE OR REPLACE TABLE {{ DATABASE }}."ARCHIVE"."LOADERPOSITION" (Id NUMBER);' in sql  # unquoted in the source: upper case, as Snowflake would resolve it
    assert 'CREATE OR REPLACE VIEW {{ DATABASE }}."ARCHIVE"."vw_Positions" AS SELECT * FROM {{ DATABASE }}."ARCHIVE"."LOADERPOSITION" p JOIN other.Thing t' in sql  # quoted keeps its case; other schemas are left alone
    assert created == [("dbo", "LoaderPosition", "LOADERPOSITION"), ("dbo", "vw_Positions", "vw_Positions")]


def test_several_source_schemas_keep_their_names_apart(tmp_path):
    path = tmp_path / "two.yaml"
    path.write_text(TEXT.replace("schemas: [dbo]", "schemas: [dbo, hist]").replace('table_prefix: ""', 'table_prefix: "LEGACY_"'), encoding="utf-8")
    m, problems = load_migration(path, tmp_path)
    assert problems == []
    sql, created = rewrite_ddl(m, "CREATE TABLE dbo.Position (Id NUMBER);\nCREATE TABLE hist.Position (Id NUMBER);\n")
    assert [c[2] for c in created] == ["LEGACY_DBO_POSITION", "LEGACY_HIST_POSITION"] and '"ARCHIVE"."LEGACY_HIST_POSITION"' in sql


# ------------------------------------------------------------- the run


def test_the_four_phases_run_end_to_end_and_the_results_live_with_the_release(migration, tool, tmp_path):
    releases = tmp_path / "releases"
    executor = FakeExecutor()
    run = run_migration(migration, Target("dev"), tmp_path / "work", releases, runner=subprocess_runner, environ=_env(), executor=executor, repo_root=tmp_path, clock=_clock(datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)), tool_override=tool)
    assert run.status == "succeeded" and [p.status for p in run.phases] == ["succeeded"] * 4
    assert run.tool_version == "SnowConvert AI CLI 3.2.0 (fake)"
    assert run.run_id.startswith("20260907T080000Z-") and run.phases[0].seconds == 7.0
    # the converted DDL is the bundle's DDL step, pointed at the archive store, and it was deployed before the migrate phase
    bundle_dir = releases / "loader-sqlserver-migration"
    ddl = (bundle_dir / "ddl" / "archive_loader_sqlserver.sql").read_text(encoding="utf-8")
    assert 'CREATE OR REPLACE TABLE {{ DATABASE }}."ARCHIVE"."LOADERPOSITION"' in ddl and 'CREATE OR REPLACE TABLE {{ DATABASE }}."ARCHIVE"."LoaderAccount"' in ddl
    assert 'CREATE OR REPLACE VIEW {{ DATABASE }}."ARCHIVE"."VW_POSITIONS" AS SELECT p.PositionId FROM {{ DATABASE }}."ARCHIVE"."LOADERPOSITION" p JOIN {{ DATABASE }}."ARCHIVE"."LoaderAccount" a' in ddl
    assert "-- dbo/LoaderAccount.sql (1 EWI marker to resolve)" in ddl and "USE SCHEMA" not in ddl
    assert run.deployed and len(executor.scripts) == 1 and 'ASTRA_DEV."ARCHIVE"."LOADERPOSITION"' in executor.scripts[0] and "{{" not in executor.scripts[0]
    assert [t.table for t in run.tables] == ["LoaderAccount", "LOADERPOSITION", "VW_POSITIONS"] and run.ewis == 1
    # logs, reports and the run record travel with the release; the connection string never does
    run_dir = bundle_dir / "migration" / "runs" / run.run_id
    assert {p.name for p in run_dir.iterdir()} == {"extract.log", "convert.log", "migrate.log", "validate.log", "reports", "run.json"}
    extract = (run_dir / "extract.log").read_text(encoding="utf-8")
    assert "--connection-string <LOADER_SQLSERVER_CONNECTION>" in extract and "--license <SNOWCONVERT_LICENSE>" in extract and "# exit 0 after 7.0s" in extract and "extracted 3 objects from LoaderDB" in extract
    everything = "".join(p.read_text(encoding="utf-8") for p in run_dir.rglob("*") if p.is_file())
    assert "hunter2" not in everything and "LIC-123" not in everything
    assert json.loads((run_dir / "reports" / "Reports" / "AssessmentReport.json").read_text(encoding="utf-8")) == {"objects": 3, "ewis": 1}
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "succeeded" and record["converted"]["ewis"] == 1 and record["deployed"] is True and record["tool"]["version"].startswith("SnowConvert AI CLI")
    assert [p["phase"] for p in record["phases"]] == ["extract", "convert", "migrate", "validate"] and record["phases"][2]["command"].index("sql-server") == record["phases"][2]["command"].index("migrate") + 1 and "<LOADER_SQLSERVER_CONNECTION>" in record["phases"][2]["command"]
    # the bundle is a release bundle like any other: it checks, lints, deploys and tests
    bundles, problems = check_bundles(releases, tmp_path)
    assert problems == [] and bundles[0].name == "loader-sqlserver-migration" and bundles[0].source == "loader_sqlserver"
    bundle = load_bundle(bundle_dir, tmp_path)
    assert lint_bundle(bundle)[1] == []
    test_executor = FakeExecutor()
    results = run_tests(bundle, Target("dev"), test_executor)
    assert len(results) == 1 and results[0].passed and "('LoaderAccount'), ('LOADERPOSITION'), ('VW_POSITIONS')" in test_executor.queries[0] and "TABLE_SCHEMA\" = 'ARCHIVE'" in test_executor.queries[0]
    provenance = json.loads((bundle_dir / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["latest_run"] == run.run_id and provenance["runs"] == [run.run_id] and provenance["inputs"]["migration"]["path"].endswith("loader_sqlserver.yaml")
    assert set(provenance["files"]) >= {"manifest.yaml", "ddl/archive_loader_sqlserver.sql", f"migration/runs/{run.run_id}/run.json"}


def test_a_failed_phase_stops_the_run_and_keeps_its_log(migration, tool, tmp_path):
    run = run_migration(migration, Target("dev"), tmp_path / "work", tmp_path / "releases", runner=subprocess_runner, environ=_env(FAKE_SNOWCT_FAIL="migrate"), repo_root=tmp_path, tool_override=tool)
    assert run.status == "failed" and [p.status for p in run.phases] == ["succeeded", "succeeded", "failed", "skipped"]
    assert run.phases[2].exit_code == 3 and not run.deployed
    log = (tmp_path / "releases" / "loader-sqlserver-migration" / run.phases[2].log).read_text(encoding="utf-8")
    assert "simulated failure" in log and "hunter2" not in log and "<LOADER_SQLSERVER_CONNECTION>" in log  # the tool echoed the secret; the log does not


def test_a_second_run_keeps_the_first_runs_results(migration, tool, tmp_path):
    first = run_migration(migration, Target("dev"), tmp_path / "work", tmp_path / "releases", runner=subprocess_runner, environ=_env(), repo_root=tmp_path, tool_override=tool, clock=_clock(datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)))
    second = run_migration(migration, Target("dev"), tmp_path / "work", tmp_path / "releases", runner=subprocess_runner, environ=_env(), repo_root=tmp_path, tool_override=tool, clock=_clock(datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)))
    runs = tmp_path / "releases" / "loader-sqlserver-migration" / "migration" / "runs"
    assert {p.name for p in runs.iterdir()} == {first.run_id, second.run_id}
    provenance = json.loads((tmp_path / "releases" / "loader-sqlserver-migration" / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["latest_run"] == second.run_id and provenance["runs"] == sorted([first.run_id, second.run_id])


def test_a_missing_secret_stops_before_anything_runs(migration, tool, tmp_path):
    calls: list[list[str]] = []

    def runner(argv, cwd, env):
        calls.append(argv)
        return CommandResult(0, "")

    with pytest.raises(EnvironmentError) as excinfo:
        run_migration(migration, Target("dev"), tmp_path / "work", tmp_path / "releases", runner=runner, environ={"SNOWCONVERT_LICENSE": "x"}, tool_override=tool)
    assert "LOADER_SQLSERVER_CONNECTION not set" in str(excinfo.value) and calls == []


def test_only_the_requested_phases_run(migration, tmp_path):
    seen: list[str] = []

    def runner(argv, cwd, env):
        if argv[-1] != "--version":
            seen.append(argv[1])
            if argv[1] == "convert":
                out = Path(argv[argv.index("--output") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "t.sql").write_text("CREATE TABLE dbo.T (Id NUMBER);", encoding="utf-8")
        return CommandResult(0, "ok")

    run = run_migration(migration, Target("dev"), tmp_path / "work", tmp_path / "releases", phases=["extract", "convert"], runner=runner, environ=_env(), repo_root=tmp_path)
    assert seen == ["extract", "convert"] and [p.status for p in run.phases] == ["succeeded", "succeeded", "skipped", "skipped"] and run.status == "partial"
    assert [t.table for t in run.tables] == ["T"]


# ------------------------------------------------------------- the CLI


def test_cli_validates_plans_and_runs(tmp_path, capsys, tool):
    assert main(["--root", str(REPO), "migrate", "validate", str(REPO / "migrations")]) == 0
    out = capsys.readouterr().out
    assert "loader_sqlserver: sql_server LoaderDB (dbo) on loader-sql.example.internal -> ARCHIVE; tool snowct" in out and "checked 1 migration file: no problems" in out

    assert main(["--root", str(REPO), "migrate", "plan", "--migration", str(EXAMPLE), "--environment", "qa", "--work", str(tmp_path / "work")]) == 0
    out = capsys.readouterr().out
    assert "extract   snowct extract sql-server --connection-string <LOADER_SQLSERVER_CONNECTION> --database LoaderDB --schemas dbo --output" in out
    assert "--target-database ASTRA_QA --target-schema ARCHIVE" in out and "4 phases of loader_sqlserver would run against ASTRA_QA; secrets shown as <VARIABLE>" in out
    assert "hunter2" not in out

    plan_json = json.loads(subprocess_capture(["--format", "json", "--root", str(REPO), "migrate", "plan", "--migration", str(EXAMPLE), "--environment", "dev", "--phase", "convert"], capsys))
    assert [p["phase"] for p in plan_json["phases"]] == ["convert"]

    import os

    os.environ.update(ENV)
    try:
        code = main(["--root", str(tmp_path), "migrate", "run", "--migration", str(EXAMPLE), "--environment", "dev", "--work", str(tmp_path / "work"), "--releases", str(tmp_path / "releases"), "--no-deploy", "--tool", " ".join(f'"{a}"' for a in tool)])
    finally:
        for key in ENV:
            os.environ.pop(key, None)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "extract   succeeded  exit 0 in" in out and "loader_sqlserver run " in out and "3 tables converted into ASTRA_DEV.ARCHIVE, 1 EWI marker to resolve, not deployed; results under releases/loader-sqlserver-migration/migration/runs/" in out


def subprocess_capture(argv: list[str], capsys) -> str:
    assert main(argv) == 0
    return capsys.readouterr().out


def test_a_failing_run_returns_one_and_names_the_phase(tmp_path, capsys, tool):
    import os

    os.environ.update({**ENV, "FAKE_SNOWCT_FAIL": "validate"})
    try:
        code = main(["--format", "github", "--root", str(tmp_path), "migrate", "run", "--migration", str(EXAMPLE), "--environment", "dev", "--work", str(tmp_path / "work"), "--releases", str(tmp_path / "releases"), "--no-deploy", "--tool", " ".join(f'"{a}"' for a in tool)])
    finally:
        for key in (*ENV, "FAKE_SNOWCT_FAIL"):
            os.environ.pop(key, None)
    assert code == 1 and "::error title=Migration phase failed::loader_sqlserver validate: exit 3, see migration/runs/" in capsys.readouterr().out
