"""Silver CDM on Iceberg, rendered as a bundle (S7.1.1)."""

from __future__ import annotations

import re
from pathlib import Path

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.silver import bundle_name, check_bundle, packs_with_cdm, render_bundle, write_bundle

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
    packs, problems = packs_with_cdm(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]
    return packs[0]


def test_the_bundle_carries_every_real_ddl_and_test_file_astra_spec_cdm_render_already_writes():
    pack = _pack()
    rendered_tests = {p.name for p in (pack.root / "cdm" / "rendered" / pack.latest.version / "tests").glob("*.sql")}
    files = render_bundle(pack)
    assert set(files) == {"manifest.yaml", "ddl/silver_tables.sql"} | {f"tests/{name}" for name in rendered_tests}
    assert len(rendered_tests) == 18  # nine entities: nine key tests, eight reference tests, one lookup test
    assert render_bundle(pack) == files  # deterministic


def test_ddl_is_astra_spec_cdm_renders_own_output_unchanged():
    pack = _pack()
    committed = (pack.root / "cdm" / "rendered" / pack.latest.version / "ddl.sql").read_text(encoding="utf-8")
    assert render_bundle(pack)["ddl/silver_tables.sql"] == committed


def test_ddl_creates_every_entity_as_an_iceberg_table_with_its_key_not_null():
    ddl = render_bundle(_pack())["ddl/silver_tables.sql"]
    tables = re.findall(r'CREATE ICEBERG TABLE IF NOT EXISTS \{\{ DATABASE \}\}\."SILVER"\."(\w+)"', ddl)
    assert tables == ["FIRM", "ACCOUNT", "SECURITY", "POSITION", "LOT", "TRANSACTION", "PRICE", "CASH_BALANCE", "EXCEPTION"]
    account = ddl[ddl.index("-- Account:") : ddl.index("-- Security:")]
    assert '"CUSTODIAN_ID"      STRING NOT NULL' in account and '"ACCOUNT_NUMBER"    STRING NOT NULL' in account
    assert "BASE_LOCATION = 'silver/account/'" in account
    assert "-- references Firm through (FIRM_ID)" in account


def test_tests_are_astra_spec_cdm_renders_own_output_unchanged():
    pack = _pack()
    rendered_dir = pack.root / "cdm" / "rendered" / pack.latest.version / "tests"
    files = render_bundle(pack)
    for path in rendered_dir.glob("*.sql"):
        assert files[f"tests/{path.name}"] == path.read_text(encoding="utf-8")


def test_key_test_checks_for_duplicate_keys():
    files = render_bundle(_pack())
    account_key = files["tests/account_key.sql"]
    assert 'GROUP BY "CUSTODIAN_ID", "ACCOUNT_NUMBER"' in account_key and "HAVING COUNT(*) > 1;" in account_key


def test_reference_test_checks_for_orphans():
    files = render_bundle(_pack())
    ref = files["tests/account_firm_reference.sql"]
    assert 'LEFT JOIN {{ DATABASE }}."SILVER"."FIRM" AS r ON r."FIRM_ID" = e."FIRM_ID"' in ref and 'WHERE r."FIRM_ID" IS NULL;' in ref


def test_lookup_test_checks_rejection_codes():
    files = render_bundle(_pack())
    lookup = files["tests/exception_rejection_code_lookup.sql"]
    assert "REJECTION_CODE" in lookup and 'r."CODE" IS NULL' in lookup


def test_manifest_names_the_bundle_and_its_one_step():
    manifest = render_bundle(_pack())["manifest.yaml"]
    assert "bundle: custodial-silver" in manifest and "source: custodial_silver" in manifest
    assert "steps:\n  - ddl/silver_tables.sql" in manifest
    assert "tests:\n  - tests/*.sql" in manifest


def test_the_committed_bundle_is_current_and_deploys_through_the_executor():
    pack = _pack()
    assert check_bundle(pack, RELEASES, REPO) == []
    bundles, problems = check_bundles(RELEASES, REPO)
    assert problems == [] and any(b.name == bundle_name(pack) for b in bundles)
    bundle = load_bundle(RELEASES / "custodial-silver", REPO)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/silver_tables.sql",)
    assert all('ASTRA_QA."SILVER"' in script for script in executor.scripts) and "{{" not in "".join(executor.scripts)
    results = run_tests(bundle, Target("qa"), executor)
    assert len(results) == 18 and all(r.passed for r in results)


def test_write_removes_stale_files_and_check_reports_drift(tmp_path):
    releases = tmp_path / "releases"
    pack = _pack()
    root = write_bundle(pack, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(pack, releases)
    assert not stale.exists() and check_bundle(pack, releases, tmp_path) == []
    (root / "ddl" / "silver_tables.sql").write_text("-- edited by hand\n", encoding="utf-8")
    problems = check_bundle(pack, releases, tmp_path)
    assert [(p.path, p.message.split(";")[0]) for p in problems] == [("releases/custodial-silver/ddl/silver_tables.sql", "stale: the domain pack's CDM changed since it was rendered")]


def test_cli_renders_and_checks(tmp_path, capsys):
    out = tmp_path / "releases"
    assert main(["--root", str(REPO), "silver", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 1
    assert "not rendered; run astra-data silver render" in capsys.readouterr().out
    assert main(["--root", str(REPO), "silver", "render", "--domains", str(DOMAINS), "--releases", str(out)]) == 0
    assert "rendered custodial-silver: 9 entities (model 1.0)" in capsys.readouterr().out
    assert main(["--root", str(REPO), "silver", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 0
    assert "Silver bundles are current for 1 domain pack" in capsys.readouterr().out
