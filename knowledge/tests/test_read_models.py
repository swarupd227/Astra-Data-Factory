"""Gold read models of a domain pack (S3.2.8)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from astra_knowledge.cdm import DomainPack, load_pack
from astra_knowledge.cli import main
from astra_knowledge.read_models import load_read_models, resolve_read_models

REPO = Path(__file__).resolve().parents[2]
CUSTODIAL = REPO / "domains" / "custodial"


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == [], [p.format() for p in problems]
    return pack


def test_the_pack_declares_the_four_read_models_over_the_latest_model(pack):
    models = pack.read_models
    assert models is not None and [m.id for m in models.models] == ["positions", "transactions", "cash_balances", "accounts"] and models.model_version == "1.0"
    positions = models.model("positions")
    assert (positions.table, positions.entity, positions.custodian, positions.business_date, positions.dated) == ("POSITIONS", "Position", "CUSTODIAN", "AS_OF_DATE", True)
    custodian = positions.column("CUSTODIAN")
    assert custodian.source == "CUSTODIAN_ID" and custodian.column.type == "string" and custodian.column.required
    # a carried column keeps the entity column's type, requiredness, PII and description under the consumer's name
    account = positions.column("ACCOUNT_NUMBER")
    assert account.source == "ACCOUNT_NUMBER" and account.column.pii == "account_number" and account.column.description.startswith("Account the position is held in")
    # an expression column has its own type and description
    value = positions.column("MARKET_VALUE")
    assert value.source is None and value.expression == "COALESCE(MARKET_VALUE, QUANTITY * PRICE)" and value.column.sql_type == "NUMBER(28,4)"
    accounts = models.model("accounts")
    assert accounts.business_date is None and not accounts.dated


def _mutated(tmp_path: Path, mutate) -> list[str]:
    root = tmp_path / "custodial"
    shutil.copytree(CUSTODIAL, root)
    path = root / "read-models.yaml"
    path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
    pack, problems = load_pack(root, tmp_path)
    assert pack is None
    return [p.message for p in problems]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda t: t.replace("entity: Position", "entity: Holding"), "read model 'positions' reads entity 'Holding', which model 1.0 does not define; entities are Firm, Account, Security, Position, Lot, Transaction, Price, Cash Balance, Exception"),
        (lambda t: t.replace("      - { name: COST_BASIS }\n      - { name: ACCRUED_INTEREST }", "      - { name: COST_BASIS, source: BOOK_COST }\n      - { name: ACCRUED_INTEREST }"), "column 'COST_BASIS' of read model 'positions' carries 'BOOK_COST', which is not a column of Position; its columns are "),
        (lambda t: t.replace('expression: "COALESCE(MARKET_VALUE, QUANTITY * PRICE)"', 'expression: "1"'), "column 'MARKET_VALUE' of read model 'positions': the expression names no column of Position"),
        (lambda t: t.replace("    business_date: AS_OF_DATE\n    columns:\n      - { name: CUSTODIAN, source: CUSTODIAN_ID }\n      - { name: ACCOUNT_NUMBER }\n      - { name: AS_OF_DATE }", "    business_date: ACCOUNT_NUMBER\n    columns:\n      - { name: CUSTODIAN, source: CUSTODIAN_ID }\n      - { name: ACCOUNT_NUMBER }\n      - { name: AS_OF_DATE }", 1), "business_date column 'ACCOUNT_NUMBER' of read model 'positions' is string; it must be a date column"),
        (lambda t: t.replace("    custodian: CUSTODIAN\n    business_date: AS_OF_DATE", "    custodian: CUSTODIAN_ID\n    business_date: AS_OF_DATE", 1), "custodian column 'CUSTODIAN_ID' of read model 'positions' is not one of its columns"),
        (lambda t: t.replace("      - { name: PRICE }\n      - { name: MARKET_VALUE", "      - { name: PRICE }\n      - { name: PRICE }\n      - { name: MARKET_VALUE", 1), "column 'PRICE' of read model 'positions' is defined twice"),
        (lambda t: t.replace('expression: "COALESCE(MARKET_VALUE, QUANTITY * PRICE)", type: decimal, precision: 28, scale: 4, ', 'expression: "COALESCE(MARKET_VALUE, QUANTITY * PRICE)", '), "read_models[0].columns[8]: missing required field type"),
        (lambda t: t.replace("domain: custodial", "domain: wealth"), "read models domain 'wealth' must match the pack directory 'custodial'"),
        (lambda t: t.replace('model_version: "1.0"', 'model_version: "3.0"'), "read models read model version 3.0, which the pack does not have; versions are 1.0"),
    ],
)
def test_the_loader_rejects_read_models_that_do_not_hold_together(tmp_path, mutate, expected):
    messages = _mutated(tmp_path, mutate)
    assert any(m.startswith(expected) for m in messages), messages


def test_a_pack_without_read_models_is_allowed(tmp_path):
    root = tmp_path / "custodial"
    shutil.copytree(CUSTODIAL, root)
    (root / "read-models.yaml").unlink()
    pack, problems = load_pack(root, tmp_path)
    assert problems == [] and pack.read_models is None


def test_load_and_resolve_are_two_steps(pack):
    raw, problems = load_read_models(CUSTODIAL / "read-models.yaml", REPO)
    assert problems == [] and raw["domain"] == "custodial"
    models, problems = resolve_read_models(raw, pack.latest, REPO)
    assert problems == [] and len(models.models) == 4 and models.model_version == "1.0"


def test_cdm_validate_counts_the_read_models(capsys):
    assert main(["--root", str(REPO), "--domains", str(REPO / "domains"), "cdm", "validate"]) == 0
    assert "4 Gold read models" in capsys.readouterr().out
