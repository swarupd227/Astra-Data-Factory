"""The config compiler (S3.1.1)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.cli import main
from astra_data.compiler import CompileError, compile_config, compile_paths
from astra_data.transforms import TransformError, parse_transform
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


def compile_text(tmp_path: Path, inputs, text: str, name: str = "pershing_position.yaml"):
    registry, catalog, packs = inputs
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)


def problems_for(tmp_path: Path, inputs, text: str) -> list[str]:
    with pytest.raises(CompileError) as excinfo:
        compile_text(tmp_path, inputs, text)
    return [p.message for p in excinfo.value.problems]


# -------------------------------------------------------------- compiling


def test_the_example_config_compiles_to_a_resolved_model(inputs):
    registry, catalog, packs = inputs
    compiled = compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)
    assert compiled.id == "pershing_position" and compiled.spec.label == "pershing_gcus 2017-07-25"
    assert compiled.pattern.id == "fixed_width_multi_record" and compiled.profile.id == "snowflake_iceberg"
    assert compiled.pack.name == "custodial" and compiled.model.version == "1.0"
    assert [m.target for m in compiled.mappings] == ["POSITION.ACCOUNT_NUMBER", "POSITION.QUANTITY"]

    account, quantity = compiled.mappings
    assert (account.record, account.source.name, account.source_type, account.result_type, account.transform) == ("detail", "account_number", "string", "string", None)
    assert quantity.transform.text == "signed_implied_decimal(13, 5)" and quantity.result_type == "decimal" and quantity.column.sql_type == "NUMBER(28,8)"
    assert quantity.rule.id == "pershing_gcus.quantity_sign" and quantity.rule.status == "confirmed"
    assert [r.id for r in compiled.rules] == ["pershing_gcus.quantity_sign"]
    assert compiled.delivery["cutoff_time"] == "06:00" and compiled.alerts["late"] == "error"

    payload = compiled.to_dict()
    assert payload["mappings"][1]["source"] == {"record": "detail", "field": "quantity", "type": "decimal", "picture": "9(13)V9(5)", "position": [23, 18], "column": None}
    assert payload["mappings"][1]["transform"] == {"name": "signed_implied_decimal", "arguments": [13, 5]}
    assert payload["provenance"]["spec"]["path"] == "specs/pershing_gcus/2017-07-25.yaml" and len(payload["provenance"]["config"]["sha256"]) == 64
    assert payload["provenance"]["rules"][0]["changed_by"] == "steward@example.com"
    assert payload["target_profile"]["orchestration"] == "Serverless tasks"


def test_the_pattern_comes_from_the_spec_when_not_named(tmp_path, inputs):
    compiled = compile_text(tmp_path, inputs, VALID.replace("pattern: fixed_width_multi_record\n", ""))
    assert compiled.pattern.id == "fixed_width_multi_record"
    assert compiled.provenance["pattern"] == "fixed_width_multi_record"


def test_mappings_may_name_the_logical_record(tmp_path, inputs):
    compiled = compile_text(tmp_path, inputs, VALID.replace("    source: account_number\n", "    source: account_number\n    record: detail\n"))
    assert compiled.mappings[0].record == "detail"


# ---------------------------------------------------------------- errors


def test_a_missing_required_field_names_the_field(tmp_path, inputs):
    without = "\n".join(line for line in VALID.splitlines() if not line.startswith("target_profile:")) + "\n"
    messages = problems_for(tmp_path, inputs, without)
    assert messages == ["top level: missing required field target_profile"]


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("pattern: fixed_width_multi_record", "pattern: cobol_copybook", "pattern 'cobol_copybook' is not in the pattern library; patterns are delimited_file, fixed_width_multi_record"),
        ("pattern: fixed_width_multi_record", "pattern: delimited_file", "pattern 'delimited_file' does not handle spec pershing_gcus 2017-07-25 (fixed_width files); patterns that do: fixed_width_multi_record"),
        ("  - pershing_gcus.quantity_sign\n", "  - pershing_gcus.quantity_sign\n  - pershing_gcus.nowhere\n", "rules[1] 'pershing_gcus.nowhere' is not in the rule catalog (rules/<group>/<name>.yaml)"),
        ("target_profile: snowflake_iceberg", "target_profile: fabric_lakehouse", "target_profile 'fabric_lakehouse' has no renderers; target profiles are snowflake_iceberg"),
        ("domain_pack: custodial", "domain_pack: insurance", "domain_pack 'insurance' is not a domain pack under domains/; domain packs are custodial"),
        ("  - target: position.quantity", "  - target: holding.quantity", "mappings[1].target 'holding.quantity': 'holding' is not an entity of custodial CDM 1.0; entities are firm, account, security, position, lot, transaction, price, cash_balance, exception"),
        ("  - target: position.quantity", "  - target: position.units", "mappings[1].target 'position.units': 'units' is not a column of Position; columns are custodian_id, account_number, security_id, as_of_date, custodian_security_id, position_type, quantity, price, market_value, cost_basis, accrued_interest, currency"),
        ("    source: quantity\n", "    source: qty\n", "mappings[1].source 'qty' is not a field of record 'detail' in spec pershing_gcus 2017-07-25; fields are account_number, as_of_date, cusip, filler, price, quantity, quantity_sign, record_type, security_type"),
        ("    source: account_number\n", "    source: account_number\n    record: holdings\n", "mappings[0].record 'holdings' is not a logical record of spec pershing_gcus 2017-07-25; records are detail"),
        ("transform: signed_implied_decimal(13, 5)", "transform: signed_decimal(13, 5)", "mappings[1].transform: 'signed_decimal' is not a transform the renderers know; transforms are implied_decimal, negate, nullif, signed_implied_decimal, to_date, trim, upper"),
        ("transform: signed_implied_decimal(13, 5)", "transform: signed_implied_decimal(13)", "mappings[1].transform: signed_implied_decimal takes 2 arguments (signed_implied_decimal(digits: int, scale: int)), not 1"),
        ("transform: signed_implied_decimal(13, 5)", "transform: signed_implied_decimal(13, five)", "mappings[1].transform: signed_implied_decimal: scale must be a whole number, not 'five'"),
        ("transform: signed_implied_decimal(13, 5)", "transform: 13 5", "mappings[1].transform: '13 5' is not a transform call; write name(arguments), for example signed_implied_decimal(13, 5)"),
        ("transform: signed_implied_decimal(13, 5)", "transform: to_date(YYYYMMDD)", "mappings[1].transform to_date does not accept a decimal field; it accepts string, integer"),
        ("  - target: position.account_number\n    source: account_number\n", "  - target: position.account_number\n    source: as_of_date\n", "mappings[0]: detail.as_of_date is date, which cannot land in POSITION.ACCOUNT_NUMBER (STRING); add a transform or map another field"),
        ("  - target: position.account_number\n    source: account_number\n", "  - target: position.as_of_date\n    source: account_number\n    transform: upper()\n", "mappings[0]: detail.account_number is string after upper(), which cannot land in POSITION.AS_OF_DATE (DATE); add a transform or map another field"),
    ],
)
def test_unknown_references_and_bad_mappings_are_named(tmp_path, inputs, old, new, expected):
    assert old in VALID
    messages = problems_for(tmp_path, inputs, VALID.replace(old, new, 1))
    assert expected in messages, messages


def test_a_config_referencing_a_rejected_rule_does_not_compile(tmp_path, inputs):
    registry, _, packs = inputs
    from datetime import datetime, timezone

    from astra_knowledge.rules import set_status

    rules_dir = tmp_path / "rules"
    shutil.copytree(REPO / "rules", rules_dir)
    catalog, _ = Catalog.load(rules_dir, tmp_path, registry)
    set_status(catalog.get("pershing_gcus.quantity_sign"), "rejected", "steward@example.com", at=datetime(2026, 9, 9, tzinfo=timezone.utc))
    catalog, _ = Catalog.load(rules_dir, tmp_path, registry)
    path = tmp_path / "pershing_position.yaml"
    path.write_text(VALID, encoding="utf-8")
    with pytest.raises(CompileError) as excinfo:
        compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    assert [p.message for p in excinfo.value.problems] == ["rules[0] 'pershing_gcus.quantity_sign' was rejected by steward@example.com on 2026-09-09; a config may not use a rejected rule"]


def test_transforms_parse_and_type():
    call = parse_transform("to_date('YYYYMMDD')")
    assert call.arguments == ("YYYYMMDD",) and call.result_type("string") == "date" and call.text == "to_date('YYYYMMDD')"
    assert parse_transform("negate()").result_type("integer") == "integer"
    assert parse_transform("nullif(0)").result_type("decimal") == "decimal"
    with pytest.raises(TransformError, match="takes 0 arguments"):
        parse_transform("trim(1)")


def test_compile_paths_reports_every_config(tmp_path, inputs):
    registry, catalog, packs = inputs
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pershing_position.yaml").write_text(VALID, encoding="utf-8")
    (configs / "pershing_broken.yaml").write_text(VALID.replace("id: pershing_position", "id: pershing_broken").replace("domain_pack: custodial", "domain_pack: nowhere"), encoding="utf-8")
    compiled, problems = compile_paths([configs], registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    assert [c.id for c in compiled] == ["pershing_position"]
    assert [(p.path, p.message) for p in problems] == [("configs/pershing_broken.yaml", "domain_pack 'nowhere' is not a domain pack under domains/; domain packs are custodial")]


# ---------------------------------------------------------------------- CLI


def _args(root: Path = REPO) -> list[str]:
    return ["--root", str(root), "compile", "--specs", str(root / "specs"), "--rules", str(root / "rules"), "--domains", str(root / "domains")]


def test_cli_compile_prints_a_summary_and_json(capsys):
    assert main([*_args(), str(REPO / "configs")]) == 0
    out = capsys.readouterr().out
    assert "pershing_position: pershing_gcus 2017-07-25 via fixed_width_multi_record -> custodial CDM 1.0 on snowflake_iceberg; 2 mappings, 1 rule, 1 dq rule" in out
    assert "compiled 1 config: no problems" in out

    assert main(["--format", "json", *_args(), str(REPO / "configs")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["source"]["id"] == "pershing_position" and payload[0]["mappings"][1]["target"]["column"] == "QUANTITY"


def test_cli_compile_reports_problems_as_annotations(tmp_path, capsys):
    root = tmp_path
    for name in ("specs", "rules", "domains"):
        shutil.copytree(REPO / name, root / name)
    (root / "configs").mkdir()
    (root / "configs" / "pershing_position.yaml").write_text(VALID.replace("pattern: fixed_width_multi_record", "pattern: cobol_copybook"), encoding="utf-8")
    assert main(["--format", "github", *_args(root), str(root / "configs")]) == 1
    out = capsys.readouterr().out
    assert "::error file=configs/pershing_position.yaml,line=" in out and "pattern 'cobol_copybook' is not in the pattern library" in out
    assert "::notice title=Astra Data::1 problem compiling 1 config" in out
