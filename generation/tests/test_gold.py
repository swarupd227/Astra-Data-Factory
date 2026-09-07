"""Gold read models and the watermark, rendered as a bundle (S3.2.8)."""

from __future__ import annotations

import re
from pathlib import Path

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.gold import bundle_name, check_bundle, packs_with_read_models, render_bundle, write_bundle

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
RELEASES = REPO / "releases"


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
    packs, problems = packs_with_read_models(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]
    return packs[0]


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in re.split(r";\n", "\n".join(l for l in sql.splitlines() if not l.startswith("--"))) if s.strip()]


def test_the_bundle_has_tables_the_publish_procedure_views_and_tests():
    files = render_bundle(_pack())
    assert set(files) == {
        "manifest.yaml",
        "ddl/gold_tables.sql",
        "pipeline/publish.sql",
        "pipeline/published_views.sql",
        "tests/positions_days_have_a_watermark.sql",
        "tests/positions_watermark_written_last.sql",
        "tests/transactions_days_have_a_watermark.sql",
        "tests/transactions_watermark_written_last.sql",
        "tests/cash_balances_days_have_a_watermark.sql",
        "tests/cash_balances_watermark_written_last.sql",
        "tests/watermark_one_per_custodian_and_date.sql",
    }
    manifest = files["manifest.yaml"]
    assert "bundle: custodial-gold" in manifest and "source: custodial_gold" in manifest
    assert manifest.index("ddl/gold_tables.sql") < manifest.index("pipeline/publish.sql") < manifest.index("pipeline/published_views.sql")
    assert render_bundle(_pack()) == files  # deterministic


def test_gold_tables_are_the_consumers_shape_with_the_watermark_and_the_log():
    ddl = render_bundle(_pack())["ddl/gold_tables.sql"]
    tables = re.findall(r'CREATE ICEBERG TABLE IF NOT EXISTS \{\{ DATABASE \}\}\."(\w+)"\."(\w+)"', ddl)
    assert tables == [("GOLD", "POSITIONS"), ("GOLD", "TRANSACTIONS"), ("GOLD", "CASH_BALANCES"), ("GOLD", "ACCOUNTS"), ("GOLD", "WATERMARK"), ("CONTROL", "GOLD_PUBLISH_LOG")]
    positions = ddl[ddl.index('"GOLD"."POSITIONS"') : ddl.index('"GOLD"."TRANSACTIONS"')]
    assert re.search(r'"CUSTODIAN"\s+STRING NOT NULL COMMENT \'Custodian that reported the position\. From Position\.CUSTODIAN_ID\.\'', positions)
    assert re.search(r'"MARKET_VALUE"\s+NUMBER\(28,4\) COMMENT \'Market value as the custodian reported it, or quantity times price when it reported none\. From COALESCE\(MARKET_VALUE, QUANTITY \* PRICE\)\.\'', positions)
    assert re.search(r'"PUBLISH_ID"\s+STRING NOT NULL', positions) and re.search(r'"PUBLISHED_AT"\s+TIMESTAMP_NTZ\(6\) NOT NULL', positions)
    assert "BASE_LOCATION = 'gold/positions/'" in positions
    assert 'MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'account_number\';' in positions  # PII travels with the column
    watermark = ddl[ddl.index('"GOLD"."WATERMARK"') :]
    for column in ("CUSTODIAN_ID", "BUSINESS_DATE", "PUBLISHED_AT", "PUBLISH_ID", "RUN_ID", "ROWS", "DETAIL"):
        assert f'"{column}"' in watermark
    assert "BASE_LOCATION = 'gold/watermark/'" in watermark and "BASE_LOCATION = 'control/gold_publish_log/'" in watermark


def test_the_publish_rewrites_each_day_then_writes_its_watermark_in_one_transaction():
    sql = render_bundle(_pack())["pipeline/publish.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."PUBLISH_GOLD"("CUSTODIAN_ID" STRING)' in sql
    # only after a DAG run no publish has followed
    assert 'run_id := (SELECT MAX_BY("RUN_ID", "STARTED_AT") FROM {{ DATABASE }}."CONTROL"."CUSTODIAN_RUNS" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "STARTED_AT" > :since);' in sql
    assert "IF (run_id IS NULL) THEN\n    RETURN 'nothing to publish for ' || CUSTODIAN_ID;" in sql
    # the days to publish are those whose canonical rows changed since the previous publish started, across the dated models
    assert 'SELECT s."AS_OF_DATE" AS "D" FROM {{ DATABASE }}."SILVER"."POSITION" s WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND COALESCE(s."UPDATED_AT", s."LOADED_AT") > :since' in sql
    assert 'SELECT s."TRADE_DATE" AS "D" FROM {{ DATABASE }}."SILVER"."TRANSACTION" s' in sql
    # per day: delete and insert every dated model, then the watermark, then commit
    loop = sql[sql.index("FOR d IN dates DO") : sql.index("END FOR;")]
    assert loop.count("BEGIN TRANSACTION;") == 1 and loop.count("COMMIT;") == 1
    order = [loop.index(x) for x in ('DELETE FROM {{ DATABASE }}."GOLD"."POSITIONS"', 'INSERT INTO {{ DATABASE }}."GOLD"."POSITIONS"', 'DELETE FROM {{ DATABASE }}."GOLD"."TRANSACTIONS"', 'DELETE FROM {{ DATABASE }}."GOLD"."CASH_BALANCES"', 'MERGE INTO {{ DATABASE }}."GOLD"."WATERMARK" w', "COMMIT;")]
    assert order == sorted(order)
    assert 'DELETE FROM {{ DATABASE }}."GOLD"."POSITIONS" WHERE "CUSTODIAN" = :CUSTODIAN_ID AND "AS_OF_DATE" = :business_date;' in loop
    assert '(COALESCE(MARKET_VALUE, QUANTITY * PRICE)) AS "MARKET_VALUE"' in loop and ':publish_id, SYSDATE()' in loop
    assert 'WHEN MATCHED THEN UPDATE SET "PUBLISHED_AT" = SYSDATE(), "PUBLISH_ID" = :publish_id, "RUN_ID" = :run_id, "ROWS" = :n_positions + :n_transactions + :n_cash_balances' in loop
    # the snapshot without a business date is rewritten whole, before the days
    accounts = sql[sql.index("-- 2. Snapshots") : sql.index("-- 3. Each business date")]
    assert 'DELETE FROM {{ DATABASE }}."GOLD"."ACCOUNTS" WHERE "CUSTODIAN" = :CUSTODIAN_ID;' in accounts and "BEGIN TRANSACTION;" in accounts and "COMMIT;" in accounts
    # the publish is logged once, after the days; a failure rolls the open day back
    assert 'INSERT INTO {{ DATABASE }}."CONTROL"."GOLD_PUBLISH_LOG"' in sql and sql.index("GOLD_PUBLISH_LOG\" (\"PUBLISH_ID\"") > sql.index("END FOR;")
    assert "EXCEPTION\n  WHEN OTHER THEN\n    ROLLBACK;\n    RAISE;" in sql


def test_consumers_read_complete_days_through_the_published_views():
    views = render_bundle(_pack())["pipeline/published_views.sql"]
    assert 'CREATE OR REPLACE VIEW {{ DATABASE }}."GOLD"."POSITIONS_PUBLISHED"' in views
    assert 'FROM {{ DATABASE }}."GOLD"."POSITIONS" g\nJOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE";' in views
    assert 'JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."TRADE_DATE";' in views
    assert 'FROM {{ DATABASE }}."GOLD"."ACCOUNTS" g\nWHERE EXISTS (SELECT 1 FROM {{ DATABASE }}."GOLD"."WATERMARK" w WHERE w."CUSTODIAN_ID" = g."CUSTODIAN");' in views


def test_tests_check_that_every_day_has_a_watermark_written_last():
    files = render_bundle(_pack())
    covered = files["tests/positions_days_have_a_watermark.sql"]
    assert 'LEFT JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE"' in covered and 'WHERE w."CUSTODIAN_ID" IS NULL' in covered
    last = files["tests/positions_watermark_written_last.sql"]
    assert 'WHERE g."PUBLISHED_AT" > w."PUBLISHED_AT";' in last
    assert "HAVING COUNT(*) > 1;" in files["tests/watermark_one_per_custodian_and_date.sql"]


def test_the_committed_bundle_is_current_and_deploys_through_the_executor():
    pack = _pack()
    assert check_bundle(pack, RELEASES, REPO) == []
    bundles, problems = check_bundles(RELEASES, REPO)
    assert problems == [] and any(b.name == bundle_name(pack) for b in bundles)
    bundle = load_bundle(RELEASES / "custodial-gold", REPO)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/gold_tables.sql", "pipeline/publish.sql", "pipeline/published_views.sql")
    assert all('ASTRA_QA."GOLD"' in script for script in executor.scripts) and "{{" not in "".join(executor.scripts)
    results = run_tests(bundle, Target("qa"), executor)
    assert len(results) == 7 and all(r.passed for r in results)


def test_write_removes_stale_files_and_check_reports_drift(tmp_path):
    releases = tmp_path / "releases"
    pack = _pack()
    root = write_bundle(pack, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(pack, releases)
    assert not stale.exists() and check_bundle(pack, releases, tmp_path) == []
    (root / "pipeline" / "publish.sql").write_text("-- edited by hand\n", encoding="utf-8")
    problems = check_bundle(pack, releases, tmp_path)
    assert [(p.path, p.message.split(";")[0]) for p in problems] == [("releases/custodial-gold/pipeline/publish.sql", "stale: the read models changed since it was rendered")]


def test_cli_renders_and_checks(tmp_path, capsys):
    out = tmp_path / "releases"
    assert main(["--root", str(REPO), "gold", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 1
    assert "not rendered; run astra-data gold render" in capsys.readouterr().out
    assert main(["--root", str(REPO), "gold", "render", "--domains", str(DOMAINS), "--releases", str(out)]) == 0
    assert "rendered custodial-gold: 4 read models" in capsys.readouterr().out
    assert main(["--root", str(REPO), "gold", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 0
    assert "Gold bundles are current for 1 domain pack" in capsys.readouterr().out
