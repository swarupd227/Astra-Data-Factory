"""The rejection taxonomy synced to CONTROL.REJECTION_CODES (S2.3.2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from astra_data.bundle import Target
from astra_data.cli import main
from astra_data.rejections import sync, sync_statements, taxonomies_from_packs

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
NOW = datetime(2026, 9, 6, 9, 30, 0, tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        return []

    def close(self) -> None:
        pass


def test_taxonomies_come_from_the_domain_packs():
    taxonomies, problems = taxonomies_from_packs(DOMAINS, REPO)
    assert problems == [] and [t.domain for t in taxonomies] == ["custodial"]
    assert len(taxonomies[0].codes) >= 50

    _, problems = taxonomies_from_packs(DOMAINS, REPO, domain="insurance")
    assert [p.message for p in problems] == ["no domain pack named 'insurance'"]


def test_sync_is_one_transaction_that_merges_codes_and_retires_the_rest():
    taxonomies, _ = taxonomies_from_packs(DOMAINS, REPO)
    statements = sync_statements(taxonomies, Target("qa"), root=REPO, clock=lambda: NOW)
    assert statements[0] == "BEGIN TRANSACTION" and statements[-1] == "COMMIT"
    merge = statements[1]
    assert merge.startswith('MERGE INTO "ASTRA_QA"."CONTROL"."REJECTION_CODES" t USING (SELECT * FROM VALUES ')
    assert "ON t.CODE = s.CODE AND t.DOMAIN = s.DOMAIN" in merge
    assert "('ACCOUNT_NOT_FOUND', 'Account not found', " in merge and "'record', 'error', 'resolution', 'steward', 'Account', " in merge
    assert "'2026-09-06 09:30:00'::TIMESTAMP_NTZ" in merge
    assert "'domains/custodial/rejections.yaml')" in merge
    assert merge.count("(") >= 60

    retire = statements[2]
    assert retire.startswith('UPDATE "ASTRA_QA"."CONTROL"."REJECTION_CODES" SET ACTIVE = FALSE, UPDATED_AT = ')
    assert "WHERE ACTIVE AND DOMAIN = 'custodial' AND CODE NOT IN ('FILE_EMPTY', " in retire


def test_sync_escapes_quotes_and_carries_loader_codes_and_auto_resolve():
    taxonomies, _ = taxonomies_from_packs(DOMAINS, REPO)
    merge = sync_statements(taxonomies, Target("dev"), root=REPO, clock=lambda: NOW)[1]
    assert "custodian''s" in merge  # descriptions with apostrophes are escaped
    assert "('PRICE_MISSING', 'Price missing', " in merge and ", TRUE, NULL, 'custodial', TRUE, " in merge  # auto_resolve, no loader codes, active


def test_sync_runs_the_script_and_reports_the_count():
    taxonomies, _ = taxonomies_from_packs(DOMAINS, REPO)
    executor = FakeExecutor()
    count = sync(executor, taxonomies, Target("dev"), root=REPO)
    assert count == len(taxonomies[0].codes)
    assert len(executor.scripts) == 1 and executor.scripts[0].startswith("BEGIN TRANSACTION;")


def test_cli_render_prints_the_statements(capsys):
    assert main(["--root", str(REPO), "rejections", "render", "--environment", "dev", "--domains", str(DOMAINS)]) == 0
    out = capsys.readouterr().out
    assert "custodial: " in out and " rejection codes, " in out and " active" in out
    assert 'MERGE INTO "ASTRA_DEV"."CONTROL"."REJECTION_CODES"' in out

    assert main(["--root", str(REPO), "--format", "json", "rejections", "render", "--environment", "dev", "--domains", str(DOMAINS), "--domain", "custodial"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["codes"][0]["domain"] == "custodial" and payload["statements"][0] == "BEGIN TRANSACTION"


def test_cli_render_reports_pack_problems(tmp_path, capsys):
    (tmp_path / "domains" / "broken").mkdir(parents=True)
    assert main(["--root", str(tmp_path), "--format", "github", "rejections", "render", "--environment", "dev", "--domains", str(tmp_path / "domains")]) == 1
    assert "::error file=domains/broken" in capsys.readouterr().out
