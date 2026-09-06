"""The canonical data model of the custodial domain pack (S2.3.1)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from astra_knowledge.cdm import DomainPack, check_rendered, diff, load_pack, load_packs, render_ddl, render_tests, rendered_files, write_rendered
from astra_knowledge.cli import main

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
CUSTODIAL = DOMAINS / "custodial"

STORY_ENTITIES = ["Account", "Security", "Position", "Lot", "Transaction", "Price", "Cash Balance", "Firm", "Exception"]


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == [], [p.format() for p in problems]
    assert pack is not None
    return pack


# ----------------------------------------------------------------- the model


def test_the_pack_defines_every_entity_the_story_names(pack):
    assert sorted(e.name for e in pack.latest.entities) == sorted(STORY_ENTITIES)
    assert pack.latest.version == "1.0" and pack.latest.schema == "SILVER"


def test_keys_are_the_reconciliation_identity(pack):
    keys = {e.name: e.key for e in pack.latest.entities}
    assert keys["Account"] == ("CUSTODIAN_ID", "ACCOUNT_NUMBER")
    assert keys["Security"] == ("SECURITY_ID",)
    assert keys["Position"] == ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "AS_OF_DATE")
    assert keys["Lot"] == ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "LOT_ID", "AS_OF_DATE")
    assert keys["Transaction"] == ("CUSTODIAN_ID", "TRANSACTION_ID")
    assert keys["Price"] == ("SECURITY_ID", "PRICE_DATE", "PRICE_SOURCE")
    assert keys["Cash Balance"] == ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "CURRENCY", "BALANCE_TYPE", "AS_OF_DATE")
    assert keys["Firm"] == ("FIRM_ID",)
    assert keys["Exception"] == ("EXCEPTION_ID",)
    for entity in pack.latest.entities:
        for name in entity.key:
            assert entity.column(name).required, f"{entity.name}.{name}"


def test_definitions_come_from_the_glossary(pack):
    for entity in pack.latest.entities:
        term = pack.glossary.term(entity.term)
        assert term is not None and term.kind == "entity" and term.term == entity.name
        assert entity.definition == term.definition and len(entity.definition) > 40
    assert sorted(t.term for t in pack.glossary.entity_terms()) == sorted(STORY_ENTITIES)
    assert {t.term for t in pack.glossary.terms if t.kind == "concept"} >= {"Custodian", "Reconciliation Identity", "Rejection Code", "Lineage", "Business Date"}


def test_transactions_carry_split_and_lifecycle_columns(pack):
    transaction = pack.latest.entity("Transaction")
    names = [c.name for c in transaction.columns]
    for name in ("SOURCE_TRANSACTION_ID", "SPLIT_RULE", "SPLIT_PART", "STATUS", "CANCELS_TRANSACTION_ID", "CORRECTS_TRANSACTION_ID", "CANCELLED_BY_TRANSACTION_ID", "SUPERSEDED_BY_TRANSACTION_ID"):
        assert name in names
    assert [c.value for c in transaction.column("STATUS").codes] == ["ACTIVE", "CANCELLED", "SUPERSEDED", "CANCEL"]


def test_exceptions_reference_the_rejection_taxonomy_and_carry_a_level(pack):
    exception = pack.latest.entity("Exception")
    assert exception.column("REJECTION_CODE").required
    assert [c.value for c in exception.column("LEVEL").codes] == ["FILE", "RECORD", "FIELD"]
    assert exception.column("RAW_VALUE").pii == "raw_record"


def test_pii_columns_use_the_foundation_categories(pack):
    tagged = {(e.name, c.name): c.pii for e in pack.latest.entities for c in e.columns if c.pii}
    assert tagged[("Account", "ACCOUNT_NUMBER")] == "account_number"
    assert tagged[("Account", "ACCOUNT_NAME")] == "name"
    assert all(v in {"raw_record", "name", "account_number", "tax_id", "email", "phone", "address", "date_of_birth", "financial"} for v in tagged.values())


def test_lineage_columns_are_appended_to_every_table(pack):
    assert [c.name for c in pack.latest.lineage] == ["SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "LOADED_AT", "UPDATED_AT"]
    for entity in pack.latest.entities:
        columns = pack.latest.table_columns(entity)
        assert [c.name for c in columns[-6:]] == [c.name for c in pack.latest.lineage]
        assert len({c.name for c in columns}) == len(columns)


def test_load_packs_finds_the_custodial_pack():
    packs, problems = load_packs(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]


# ------------------------------------------------------------------- the DDL


def test_ddl_creates_every_entity_on_iceberg(pack):
    ddl = render_ddl(pack.latest)
    tables = re.findall(r'CREATE ICEBERG TABLE IF NOT EXISTS \{\{ DATABASE \}\}\."SILVER"\."([A-Z_]+)"', ddl)
    assert tables == ["FIRM", "ACCOUNT", "SECURITY", "POSITION", "LOT", "TRANSACTION", "PRICE", "CASH_BALANCE", "EXCEPTION"]
    assert set(re.findall(r"\{\{\s*([A-Z_]+)\s*\}\}", ddl)) == {"DATABASE"}
    for entity in pack.latest.entities:
        assert f"BASE_LOCATION = 'silver/{entity.table.lower()}/'" in ddl


def test_ddl_carries_definitions_types_nullability_and_pii(pack):
    ddl = render_ddl(pack.latest)
    account = pack.latest.entity("Account")
    assert "client's securities" in account.definition
    assert f"COMMENT = 'Account: {account.definition.replace(chr(39), chr(39) * 2)} [custodial CDM 1.0]';" in ddl
    assert '"ACCOUNT_NUMBER"' in ddl and "STRING NOT NULL COMMENT 'Account number as the custodian assigns it. PII: account_number.'" in ddl
    assert '"QUANTITY"' in ddl and "NUMBER(28,8) NOT NULL" in ddl
    assert '"SOURCE_LINE"' in ddl and "NUMBER(18,0) COMMENT 'Line of the source file the row came from.'" in ddl
    assert '"LOADED_AT"' in ddl and "TIMESTAMP_NTZ(6) NOT NULL" in ddl
    assert "Codes: OPEN = open; CLOSED = closed; PENDING = opened at the custodian but not yet resolved to a platform account." in ddl
    assert "Custodian''s account type" in ddl  # quotes are escaped


def test_key_and_reference_tests_are_rendered(pack):
    tests = render_tests(pack.latest)
    assert {name for name in tests if name.endswith("_key.sql")} == {f"{e.table.lower()}_key.sql" for e in pack.latest.entities}
    assert 'GROUP BY "CUSTODIAN_ID", "ACCOUNT_NUMBER"\nHAVING COUNT(*) > 1;' in tests["account_key.sql"]

    assert 'LEFT JOIN {{ DATABASE }}."SILVER"."FIRM" AS r ON r."FIRM_ID" = e."FIRM_ID"' in tests["account_firm_reference.sql"]
    assert tests["account_firm_reference.sql"].rstrip().endswith('WHERE r."FIRM_ID" IS NULL;')

    optional = tests["transaction_security_reference.sql"]
    assert 'WHERE r."SECURITY_ID" IS NULL AND (e."SECURITY_ID" IS NOT NULL);' in optional
    position = tests["position_account_reference.sql"]
    assert 'ON r."CUSTODIAN_ID" = e."CUSTODIAN_ID" AND r."ACCOUNT_NUMBER" = e."ACCOUNT_NUMBER"' in position


def test_rendered_files_in_the_repository_are_current(pack):
    problems = check_rendered(pack, REPO)
    assert problems == [], [p.format() for p in problems]
    assert (CUSTODIAL / "cdm" / "rendered" / "1.0" / "ddl.sql").is_file()


def test_rendering_is_deterministic_and_write_removes_stale_files(tmp_path, pack):
    copy = _copy_pack(tmp_path)
    stale = copy.root / "cdm" / "rendered" / "1.0" / "tests" / "old_test.sql"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("SELECT 1;", encoding="utf-8")
    written = write_rendered(copy)
    assert not stale.exists()
    assert {p.relative_to(copy.root / "cdm" / "rendered" / "1.0").as_posix() for p in written} == set(rendered_files(pack.latest))
    assert check_rendered(copy) == []
    assert write_rendered(copy) == written


# --------------------------------------------------------------- versioning


def _copy_pack(tmp_path: Path) -> DomainPack:
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    pack, problems = load_pack(root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    return pack


def _add_version(root: Path, version: str, mutate, migration: str | None = None) -> None:
    data = yaml.safe_load((root / "cdm" / "1.0.yaml").read_text(encoding="utf-8"))
    data["model"]["version"] = version
    if migration is not None:
        data["model"]["migration"] = f"migrations/{version}.md"
        (root / "cdm" / "migrations").mkdir(exist_ok=True)
        (root / "cdm" / "migrations" / f"{version}.md").write_text(migration, encoding="utf-8")
    mutate(data)
    (root / "cdm" / f"{version}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _entity(data: dict, name: str) -> dict:
    return next(e for e in data["entities"] if e["name"] == name)


def add_optional_column(data):
    _entity(data, "Account")["columns"].append({"name": "BRANCH_CODE", "type": "string", "description": "Custodian branch code."})


def drop_column(data):
    _entity(data, "Account")["columns"] = [c for c in _entity(data, "Account")["columns"] if c["name"] != "ACCOUNT_TYPE"]


def test_an_additive_change_is_a_minor_version(tmp_path):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "1.1", add_optional_column)
    pack, problems = load_pack(pack.root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    assert [m.version for m in pack.models] == ["1.0", "1.1"]
    changes = diff(pack.model("1.0"), pack.model("1.1"))
    assert [(c.kind, c.message) for c in changes] == [("additive", "Account: optional column BRANCH_CODE added")]


def test_a_breaking_change_needs_a_major_version_and_a_migration_note(tmp_path):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "1.1", drop_column)
    _, problems = load_pack(pack.root, tmp_path)
    messages = [p.message for p in problems]
    assert any("version 1.1 makes 1 breaking change to 1.0, so it must be 2.0 with a migration note: Account: column ACCOUNT_TYPE removed" in m for m in messages), messages
    assert any("must name a migration note (model.migration: migrations/1.1.md)" in m for m in messages)

    (pack.root / "cdm" / "1.1.yaml").unlink()
    _add_version(pack.root, "2.0", drop_column)
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["version 2.0 breaks 1.0 and must name a migration note (model.migration: migrations/2.0.md)"]

    (pack.root / "cdm" / "2.0.yaml").unlink()
    _add_version(pack.root, "2.0", drop_column, migration="# 2.0\n\nACCOUNT_TYPE is dropped; REGISTRATION_TYPE replaces it.\n")
    pack, problems = load_pack(pack.root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    assert pack.latest.version == "2.0" and pack.latest.migration == "migrations/2.0.md"


def test_a_migration_note_must_exist_and_say_something(tmp_path):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "2.0", drop_column, migration="")
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["migration note 'migrations/2.0.md' is empty"]
    (pack.root / "cdm" / "migrations" / "2.0.md").unlink()
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["migration note 'migrations/2.0.md' does not exist next to the model"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: _entity(d, "Exception").update(key=["EXCEPTION_ID", "REJECTION_CODE"]), "Exception: key changed from (EXCEPTION_ID) to (EXCEPTION_ID, REJECTION_CODE)"),
        (lambda d: _entity(d, "Account").update(table="ACCOUNTS"), "Account: table renamed from ACCOUNT to ACCOUNTS"),
        (lambda d: d["entities"].remove(_entity(d, "Lot")), "entity Lot removed"),
        (lambda d: next(c for c in _entity(d, "Account")["columns"] if c["name"] == "ACCOUNT_NAME").update(required=True), "Account: column ACCOUNT_NAME made required"),
        (lambda d: next(c for c in _entity(d, "Position")["columns"] if c["name"] == "QUANTITY").update(precision=20, scale=8), "Position: column QUANTITY changed from NUMBER(28,8) to NUMBER(20,8)"),
        (lambda d: next(c for c in _entity(d, "Position")["columns"] if c["name"] == "QUANTITY").update(type="string", precision=None, scale=None) or _strip(d), "Position: column QUANTITY changed from NUMBER(28,8) to STRING"),
        (lambda d: _entity(d, "Account")["columns"].append({"name": "BRANCH_CODE", "type": "string", "required": True, "description": "Branch."}), "Account: required column BRANCH_CODE added; existing rows have no value for it"),
        (lambda d: d["lineage"].pop(), "lineage: column UPDATED_AT removed"),
        (lambda d: d["model"].update(schema="CANONICAL"), "target schema changed from SILVER to CANONICAL"),
    ],
)
def test_breaking_changes_are_classified(tmp_path, mutate, expected):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "2.0", mutate, migration="Migration note.")
    pack, problems = load_pack(pack.root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    breaking = [c.message for c in diff(pack.model("1.0"), pack.model("2.0")) if c.breaking]
    assert breaking == [expected]


def _strip(data: dict) -> None:
    for entity in data["entities"]:
        for column in entity["columns"]:
            for key in ("precision", "scale"):
                if column.get(key) is None:
                    column.pop(key, None)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: next(c for c in _entity(d, "Position")["columns"] if c["name"] == "QUANTITY").update(precision=30, scale=10), "Position: column QUANTITY widened from NUMBER(28,8) to NUMBER(30,10)"),
        (lambda d: next(c for c in _entity(d, "Transaction")["columns"] if c["name"] == "SPLIT_PART").update(type="decimal", precision=20, scale=2), "Transaction: column SPLIT_PART widened from NUMBER(18,0) to NUMBER(20,2)"),
        (lambda d: next(c for c in _entity(d, "Account")["columns"] if c["name"] == "BASE_CURRENCY").update(required=False), "Account: column BASE_CURRENCY made optional"),
        (lambda d: next(c for c in _entity(d, "Account")["columns"] if c["name"] == "ACCOUNT_NAME").update(description="Registration name."), "Account: column ACCOUNT_NAME description changed"),
        (lambda d: _entity(d, "Firm")["columns"][2]["codes"].append({"value": "FAMILY_OFFICE", "meaning": "family office"}), "Firm: column FIRM_TYPE code list changed"),
        (lambda d: d["entities"].append({"name": "Benchmark", "term": "Benchmark", "table": "BENCHMARK", "key": ["BENCHMARK_ID"], "columns": [{"name": "BENCHMARK_ID", "type": "string", "required": True, "description": "Identifier."}]}), "entity Benchmark added"),
        (lambda d: _entity(d, "Price")["references"].clear(), "Price: reference to Security through (SECURITY_ID) removed"),
    ],
)
def test_additive_changes_are_classified(tmp_path, mutate, expected):
    pack = _copy_pack(tmp_path)
    if "Benchmark" in expected:
        glossary = pack.root / "glossary.yaml"
        glossary.write_text(glossary.read_text(encoding="utf-8") + "\n  - term: Benchmark\n    kind: entity\n    definition: An index a portfolio's performance is compared with.\n", encoding="utf-8")
    _add_version(pack.root, "1.1", mutate)
    pack, problems = load_pack(pack.root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    changes = diff(pack.model("1.0"), pack.model("1.1"))
    assert [c.message for c in changes] == [expected] and not any(c.breaking for c in changes)


def test_a_version_must_change_something_and_follow_its_predecessor(tmp_path):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "1.1", lambda d: None)
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["version 1.1 is identical to 1.0; a new version must change something"]

    (pack.root / "cdm" / "1.1.yaml").unlink()
    _add_version(pack.root, "1.2", add_optional_column)
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["version 1.2 only adds to 1.0, so it must be 1.1; a new major version is for breaking changes"]

    (pack.root / "cdm" / "1.2.yaml").unlink()
    _add_version(pack.root, "2.0", add_optional_column, migration="Nothing to migrate.")
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["version 2.0 only adds to 1.0, so it must be 1.1; a new major version is for breaking changes"]


def test_the_first_version_is_one_point_zero(tmp_path):
    pack = _copy_pack(tmp_path)
    (pack.root / "cdm" / "1.0.yaml").rename(pack.root / "cdm" / "1.1.yaml")
    text = (pack.root / "cdm" / "1.1.yaml").read_text(encoding="utf-8").replace('version: "1.0"', 'version: "1.1"')
    (pack.root / "cdm" / "1.1.yaml").write_text(text, encoding="utf-8")
    _, problems = load_pack(pack.root, tmp_path)
    assert [p.message for p in problems] == ["the first model version must be 1.0; found 1.1"]


# --------------------------------------------------------------- validation


def _mutated(tmp_path: Path, mutate) -> list[str]:
    pack = _copy_pack(tmp_path)
    data = yaml.safe_load((pack.root / "cdm" / "1.0.yaml").read_text(encoding="utf-8"))
    mutate(data)
    (pack.root / "cdm" / "1.0.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _, problems = load_pack(pack.root, tmp_path)
    return [p.message for p in problems]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: _entity(d, "Lot").update(term="Tax Lot"), "entity 'Lot' names glossary term 'Tax Lot', which is not in"),
        (lambda d: _entity(d, "Lot").update(term="Custodian"), "entity 'Lot' names glossary term 'Custodian', which is a concept term, not an entity term"),
        (lambda d: _entity(d, "Lot").update(name="Tax Lot"), "entity 'Tax Lot' must be named after its glossary term 'Lot'"),
        (lambda d: d["entities"].remove(_entity(d, "Lot")), "entity term 'Lot' has no entity in any model version"),
        (lambda d: _entity(d, "Account").update(key=["CUSTODIAN_ID", "NOWHERE"]), "key column 'NOWHERE' is not a column of 'Account'"),
        (lambda d: _entity(d, "Account").update(key=["CUSTODIAN_ID", "ACCOUNT_NAME"]), "key column 'ACCOUNT_NAME' of 'Account' must be required"),
        (lambda d: _entity(d, "Account")["columns"].append({"name": "LOADED_AT", "type": "timestamp", "description": "x"}), "column 'LOADED_AT' of 'Account' has the name of a lineage column"),
        (lambda d: _entity(d, "Account")["columns"].append({"name": "FIRM_ID", "type": "string", "description": "x"}), "column 'FIRM_ID' of 'Account' is defined twice"),
        (lambda d: _entity(d, "Account")["references"][0].update(entity="Firms"), "'Account' references entity 'Firms', which is not in the model"),
        (lambda d: _entity(d, "Position")["references"][0].update(columns=["CUSTODIAN_ID"]), "'Position' references 'Account' with 1 column(s), but its key has 2: CUSTODIAN_ID, ACCOUNT_NUMBER"),
        (lambda d: _entity(d, "Account")["references"][0].update(columns=["NOWHERE"]), "'Account' references 'Firm' through 'NOWHERE', which is not a column of 'Account'"),
        (lambda d: next(c for c in _entity(d, "Position")["columns"] if c["name"] == "QUANTITY").update(scale=28), "column 'QUANTITY': scale 28 must be less than precision 28"),
        (lambda d: _entity(d, "Firm")["columns"][2]["codes"].append({"value": "RIA", "meaning": "again"}), "column 'FIRM_TYPE': code 'RIA' is listed twice"),
        (lambda d: d["model"].update(version="1.1"), "model.version '1.1' must match the file name; the file is 1.0.yaml"),
        (lambda d: next(c for c in _entity(d, "Position")["columns"] if c["name"] == "PRICE").pop("precision"), "precision"),
    ],
)
def test_the_validator_rejects_a_model_that_does_not_hold_together(tmp_path, mutate, expected):
    messages = _mutated(tmp_path, mutate)
    assert any(expected in m for m in messages), messages


def test_a_pack_needs_a_glossary_and_a_model(tmp_path):
    root = tmp_path / "domains" / "empty"
    root.mkdir(parents=True)
    _, problems = load_pack(root, tmp_path)
    assert [p.message for p in problems] == ["domain pack has no glossary.yaml"]
    shutil.copy(CUSTODIAL / "glossary.yaml", root / "glossary.yaml")
    _, problems = load_pack(root, tmp_path)
    assert [p.message for p in problems] == ["domain pack has no model versions under cdm/ (expected files like cdm/1.0.yaml)"]


# ---------------------------------------------------------------------- CLI


def test_cli_validate_reports_the_pack(capsys):
    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "cdm", "validate"]) == 0
    assert "checked 1 model version and 15 glossary terms across 1 domain pack: no problems" in capsys.readouterr().out

    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "--format", "json", "cdm", "validate"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packs"][0]["name"] == "custodial" and payload["packs"][0]["versions"] == ["1.0"]
    assert payload["packs"][0]["entities"]["1.0"] == ["Firm", "Account", "Security", "Position", "Lot", "Transaction", "Price", "Cash Balance", "Exception"]


def test_cli_validate_reports_problems_with_the_file_and_line(tmp_path, capsys):
    pack = _copy_pack(tmp_path)
    text = (pack.root / "cdm" / "1.0.yaml").read_text(encoding="utf-8").replace("    term: Lot\n", "    term: Tax Lot\n")
    (pack.root / "cdm" / "1.0.yaml").write_text(text, encoding="utf-8")
    assert main(["--root", str(tmp_path), "--domains", str(tmp_path / "domains"), "--format", "github", "cdm", "validate"]) == 1
    out = capsys.readouterr().out
    assert "::error file=domains/custodial/cdm/1.0.yaml,line=" in out and "names glossary term 'Tax Lot'" in out


def test_cli_render_check_and_show(tmp_path, capsys):
    pack = _copy_pack(tmp_path)
    args = ["--root", str(tmp_path), "--domains", str(tmp_path / "domains")]
    (pack.root / "cdm" / "rendered" / "1.0" / "ddl.sql").write_text("-- stale\n", encoding="utf-8")
    assert main([*args, "cdm", "render", "--check"]) == 1
    assert "domains/custodial/cdm/rendered/1.0/ddl.sql: stale" in capsys.readouterr().out

    assert main([*args, "cdm", "render"]) == 0
    assert "rendered custodial CDM 1.0: 9 tables, 17 tests" in capsys.readouterr().out
    assert main([*args, "cdm", "render", "--check"]) == 0
    assert "rendered files are current" in capsys.readouterr().out

    assert main([*args, "cdm", "show", "--domain", "custodial"]) == 0
    out = capsys.readouterr().out
    assert "custodial CDM 1.0" in out and "Cash Balance  CASH_BALANCE  key: CUSTODIAN_ID, ACCOUNT_NUMBER, CURRENCY, BALANCE_TYPE, AS_OF_DATE" in out
    assert "  ACCOUNT_NUMBER" in out and "PII account_number" in out


def test_cli_diff_classifies_changes(tmp_path, capsys):
    pack = _copy_pack(tmp_path)
    _add_version(pack.root, "2.0", lambda d: (drop_column(d), add_optional_column(d)), migration="Note.")
    args = ["--root", str(tmp_path), "--domains", str(tmp_path / "domains")]
    assert main([*args, "cdm", "diff", "--domain", "custodial", "--from", "1.0", "--to", "2.0"]) == 0
    out = capsys.readouterr().out
    assert "custodial CDM 1.0 -> 2.0: 1 breaking change, 1 additive change; migration note migrations/2.0.md" in out
    assert "  breaking  Account: column ACCOUNT_TYPE removed" in out
    assert "  additive  Account: optional column BRANCH_CODE added" in out
