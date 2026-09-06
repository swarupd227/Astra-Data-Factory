"""Reference-data replication rendered as a bundle (S2.3.3)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.reference_data import bundle_name, check_bundle, packs_with_reference_data, render_bundle, sync, sync_statements, write_bundle

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
RELEASES = REPO / "releases"
NOW = datetime(2026, 9, 6, 9, 30, 0, tzinfo=timezone.utc)


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


def _pack():
    packs, problems = packs_with_reference_data(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]
    return packs[0]


def test_the_bundle_has_tables_a_procedure_per_feed_lookups_tasks_and_tests():
    files = render_bundle(_pack())
    assert set(files) == {
        "manifest.yaml",
        "ddl/reference_tables.sql",
        "pipeline/replicate_security_master.sql",
        "pipeline/replicate_account_xref.sql",
        "pipeline/lookups.sql",
        "pipeline/tasks.sql",
        "tests/security_master_key_unique.sql",
        "tests/security_master_last_run_not_failed.sql",
        "tests/security_master_fresh.sql",
        "tests/account_xref_key_unique.sql",
        "tests/account_xref_last_run_not_failed.sql",
        "tests/account_xref_fresh.sql",
    }
    manifest = files["manifest.yaml"]
    assert "bundle: custodial-reference-data" in manifest and "source: custodial_reference_data" in manifest
    assert re.search(r'version: "[0-9a-f]{12}"', manifest)
    assert "  - ddl/reference_tables.sql\n  - pipeline/replicate_security_master.sql\n  - pipeline/replicate_account_xref.sql\n  - pipeline/lookups.sql\n  - pipeline/tasks.sql" in manifest
    for text in files.values():
        assert set(re.findall(r"\{\{\s*([A-Z_]+)\s*\}\}", text)) <= {"DATABASE"}


def test_tables_are_iceberg_in_the_reference_schema_with_change_and_conflict_logs():
    ddl = render_bundle(_pack())["ddl/reference_tables.sql"]
    tables = re.findall(r'CREATE ICEBERG TABLE IF NOT EXISTS \{\{ DATABASE \}\}\."REFERENCE"\."([A-Z_]+)"', ddl)
    assert tables == ["SECURITY_MASTER", "SECURITY_MASTER_STAGING", "SECURITY_MASTER_CHANGES", "SECURITY_MASTER_CONFLICTS", "ACCOUNT_XREF", "ACCOUNT_XREF_STAGING", "ACCOUNT_XREF_CHANGES", "ACCOUNT_XREF_CONFLICTS"]
    assert re.search(r'"SECURITY_ID"\s+STRING NOT NULL COMMENT \'Security master identifier\.\'', ddl)
    assert re.search(r'"PRICE_FACTOR"\s+NUMBER\(18,8\) COMMENT', ddl)
    assert re.search(r'"CUSTODIAN_ACCOUNT_NUMBER"\s+STRING NOT NULL COMMENT \'Account number as the custodian assigns it\. PII: account_number\.\'', ddl)
    assert '"REPLICATED_AT"' in ddl and '"RUN_ID"' in ddl and "BASE_LOCATION = 'reference/security_master_changes/'" in ddl
    assert '"CHANGE"' in ddl and "'inserted, updated or deleted'" in ddl and '"BEFORE"' in ddl and '"AFTER"' in ddl
    assert '"REJECTION_CODE"' in ddl and "REFERENCE_DATA_CONFLICT" in ddl


def test_the_procedure_loads_the_newest_snapshot_computes_the_delta_and_records_the_run():
    sql = render_bundle(_pack())["pipeline/replicate_security_master.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."REFERENCE"."REPLICATE_SECURITY_MASTER"(SNAPSHOT_DATE DATE)' in sql
    assert 'TRUNCATE TABLE {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_STAGING";' in sql
    assert 'FROM (SELECT $1::STRING, $2::STRING, $3::STRING, $4::STRING, $5::STRING, $6::STRING, $7::STRING, $8::STRING, $9::STRING, $10::STRING, $11::NUMBER(18,8), $12::DATE, $13::STRING, $14::TIMESTAMP_NTZ(6), METADATA$FILENAME, METADATA$FILE_ROW_NUMBER FROM @{{ DATABASE }}."REFERENCE"."LANDING"/security_master/)' in sql
    assert "FILE_FORMAT = (FORMAT_NAME = '{{ DATABASE }}.REFERENCE.CSV')" in sql and "PATTERN = '.*[.]csv'" in sql
    assert "IF (files_loaded = 0) THEN" in sql and "'skipped'" in sql
    assert 'INSERT INTO {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_CONFLICTS"' in sql and "HAVING COUNT(*) > 1 OR NOT (\"SECURITY_ID\" IS NOT NULL)" in sql
    assert "QUALIFY COUNT(*) OVER (PARTITION BY \"SECURITY_ID\") = 1" in sql
    for change in ("'deleted'", "'inserted'", "'updated'"):
        assert change in sql
    assert 'OBJECT_CONSTRUCT_KEEP_NULL(\'SECURITY_ID\', r."SECURITY_ID"' in sql
    assert 's."CUSIP" IS DISTINCT FROM r."CUSIP"' in sql
    assert 'MERGE INTO {{ DATABASE }}."REFERENCE"."SECURITY_MASTER" r' in sql and '"REPLICATED_AT" = SYSDATE(), "RUN_ID" = :run_id' in sql
    assert 'INSERT INTO {{ DATABASE }}."CONTROL"."REFERENCE_DATA_RUNS" (RUN_ID, FEED_ID, DOMAIN, STARTED_AT, FINISHED_AT, STATUS, SNAPSHOT_DATE, FILES_LOADED, ROWS_SOURCE, ROWS_CONFLICT, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED, ROWS_UNCHANGED, ROWS_TOTAL)' in sql
    assert "'security_master', 'custodial'" in sql
    assert "EXCEPTION\n  WHEN OTHER THEN" in sql and "'failed'" in sql and ":SQLERRM" in sql and "RAISE;" in sql


def test_composite_keys_join_on_every_key_column():
    sql = render_bundle(_pack())["pipeline/replicate_account_xref.sql"]
    assert 'r."CUSTODIAN_ID" = s."CUSTODIAN_ID" AND r."CUSTODIAN_ACCOUNT_NUMBER" = s."CUSTODIAN_ACCOUNT_NUMBER"' in sql
    assert 'PARTITION BY "CUSTODIAN_ID", "CUSTODIAN_ACCOUNT_NUMBER"' in sql
    assert 'UPDATE SET "ACCOUNT_ID" = s."ACCOUNT_ID", "FIRM_ID" = s."FIRM_ID"' in sql


def test_lookups_unpivot_alternate_identifiers_and_tasks_follow_the_schedule():
    files = render_bundle(_pack())
    lookups = files["pipeline/lookups.sql"]
    assert 'CREATE OR REPLACE VIEW {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_IDENTIFIERS"' in lookups
    assert "SELECT \"SECURITY_ID\", 'CUSIP' AS IDENTIFIER_TYPE, \"CUSIP\" AS IDENTIFIER_VALUE, 1 AS PRIORITY FROM {{ DATABASE }}.\"REFERENCE\".\"SECURITY_MASTER\" WHERE \"CUSIP\" IS NOT NULL" in lookups
    assert "'TICKER' AS IDENTIFIER_TYPE, \"TICKER\" AS IDENTIFIER_VALUE, 4 AS PRIORITY" in lookups
    assert "Account cross-reference: records resolve by the key (CUSTODIAN_ID, CUSTODIAN_ACCOUNT_NUMBER) directly" in lookups and "ACCOUNT_XREF_IDENTIFIERS" not in lookups

    tasks = files["pipeline/tasks.sql"]
    assert 'CREATE OR REPLACE TASK {{ DATABASE }}."REFERENCE"."REFERENCE_SECURITY_MASTER_REPLICATE"' in tasks
    assert "SCHEDULE = 'USING CRON 0 2 * * * America/New_York'" in tasks and "SCHEDULE = 'USING CRON 30 2 * * * America/New_York'" in tasks
    assert "USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'" in tasks
    assert 'CALL {{ DATABASE }}."REFERENCE"."REPLICATE_SECURITY_MASTER"(NULL);' in tasks
    assert 'ALTER TASK {{ DATABASE }}."REFERENCE"."REFERENCE_ACCOUNT_XREF_REPLICATE" RESUME;' in tasks


def test_tests_check_keys_the_last_run_and_freshness():
    files = render_bundle(_pack())
    assert 'GROUP BY "SECURITY_ID"\nHAVING COUNT(*) > 1;' in files["tests/security_master_key_unique.sql"]
    last = files["tests/security_master_last_run_not_failed.sql"]
    assert "WHERE FEED_ID = 'security_master' QUALIFY ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) = 1" in last and "WHERE STATUS = 'failed';" in last
    fresh = files["tests/account_xref_fresh.sql"]
    assert "WHERE FEED_ID = 'account_xref' AND STATUS = 'succeeded'" in fresh and "HAVING MAX(FINISHED_AT) < DATEADD('hour', -26, SYSDATE());" in fresh


def test_the_committed_bundle_is_current_and_passes_the_bundle_check():
    pack = _pack()
    assert check_bundle(pack, RELEASES, REPO) == []
    bundles, problems = check_bundles(RELEASES, REPO)
    assert problems == [] and any(b.name == bundle_name(pack) for b in bundles)
    bundle = load_bundle(RELEASES / bundle_name(pack), REPO)
    assert len(bundle.steps) == 5 and len(bundle.tests) == 6


def test_the_bundle_deploys_and_tests_through_the_executor():
    bundle = load_bundle(RELEASES / "custodial-reference-data", REPO)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/reference_tables.sql", "pipeline/replicate_security_master.sql", "pipeline/replicate_account_xref.sql", "pipeline/lookups.sql", "pipeline/tasks.sql")
    assert all('ASTRA_QA."REFERENCE"' in script for script in executor.scripts) and "{{" not in "".join(executor.scripts)
    results = run_tests(bundle, Target("qa"), executor)
    assert len(results) == 6 and all(r.passed for r in results)


def test_write_removes_stale_files_and_check_reports_drift(tmp_path):
    releases = tmp_path / "releases"
    pack = _pack()
    root = write_bundle(pack, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(pack, releases)
    assert not stale.exists() and check_bundle(pack, releases, tmp_path) == []

    (root / "pipeline" / "tasks.sql").write_text("-- edited by hand\n", encoding="utf-8")
    (root / "tests" / "account_xref_fresh.sql").unlink()
    problems = check_bundle(pack, releases, tmp_path)
    assert [(p.path, p.message.split(";")[0]) for p in problems] == [
        ("releases/custodial-reference-data/pipeline/tasks.sql", "stale: the feeds changed since it was rendered"),
        ("releases/custodial-reference-data/tests/account_xref_fresh.sql", "not rendered"),
    ]


def test_feed_sync_merges_feeds_and_disables_the_rest():
    pack = _pack()
    statements = sync_statements([pack], Target("dev"), root=REPO, clock=lambda: NOW)
    assert statements[0] == "BEGIN TRANSACTION" and statements[-1] == "COMMIT"
    merge = statements[1]
    assert merge.startswith('MERGE INTO "ASTRA_DEV"."CONTROL"."REFERENCE_FEEDS" t USING (SELECT * FROM VALUES ')
    assert "('security_master', 'custodial', 'Security master', 'SOS', 'SECURITY_MASTER', '0 2 * * *', 'America/New_York', 26, 'error', 'domains/custodial/reference-data.yaml')" in merge
    assert "'2026-09-06 09:30:00'::TIMESTAMP_NTZ" in merge
    assert statements[2] == "UPDATE \"ASTRA_DEV\".\"CONTROL\".\"REFERENCE_FEEDS\" SET ENABLED = FALSE, UPDATED_AT = '2026-09-06 09:30:00'::TIMESTAMP_NTZ WHERE ENABLED AND DOMAIN = 'custodial' AND FEED_ID NOT IN ('security_master', 'account_xref')"
    executor = FakeExecutor()
    assert sync(executor, [pack], Target("dev"), root=REPO) == 2 and executor.scripts[0].startswith("BEGIN TRANSACTION;")


def test_cli_render_check_and_dry_run_sync(tmp_path, capsys):
    domains = tmp_path / "domains"
    shutil.copytree(DOMAINS, domains)
    releases = tmp_path / "releases"
    args = ["--root", str(tmp_path), "reference", "render", "--domains", str(domains), "--releases", str(releases)]
    assert main([*args, "--check"]) == 1
    assert "not rendered; run astra-data reference render" in capsys.readouterr().out
    assert main(args) == 0
    assert "rendered custodial-reference-data: 2 feeds -> releases/custodial-reference-data" in capsys.readouterr().out
    assert main([*args, "--check"]) == 0
    assert "reference-data bundles are current for 1 domain pack" in capsys.readouterr().out

    assert main(["--root", str(REPO), "reference", "sync", "--environment", "dev", "--domains", str(DOMAINS), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("BEGIN TRANSACTION;\nMERGE INTO \"ASTRA_DEV\".\"CONTROL\".\"REFERENCE_FEEDS\"")
