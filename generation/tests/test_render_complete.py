"""S3.2.9: the bundle is complete: Terraform whose plan is clean, docs with every field, rule and check cited, Atlan with PII, lintable tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.bundle import load_bundle
from astra_data.cli import main
from astra_data.compiler import compile_config
from astra_data.lint import lint_bundle, lint_bundles
from astra_data.render import render_bundle, write_bundle
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]
TERRAFORM = shutil.which("terraform") or next((str(p) for p in [Path.home() / ".local" / "bin" / "terraform.exe", Path.home() / ".local" / "bin" / "terraform"] if p.exists()), None)
PROVIDER_CACHE = REPO / "infra" / "terraform" / "foundation" / ".terraform" / "providers"


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


@pytest.fixture(scope="module")
def compiled(inputs):
    registry, catalog, packs = inputs
    return compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)


@pytest.fixture(scope="module")
def files(compiled):
    return render_bundle(compiled)


# ----------------------------------------------------------------- Terraform


def test_the_terraform_root_reads_every_prerequisite_and_fails_the_plan_when_one_is_missing(files):
    main_tf = files["terraform/main.tf"]
    assert 'data "snowflake_warehouses" "tier"' in main_tf and 'like            = local.warehouse' in main_tf
    assert 'warehouse      = "${local.database}_WH_MEDIUM"' in main_tf  # the source's tier
    for schema in ("bronze", "silver", "exceptions", "control"):
        assert f'data "snowflake_schemas" "{schema}"' in main_tf
    assert 'data "snowflake_tags" "pii"' in main_tf and 'schema = "${local.database}.CONTROL"' in main_tf
    assert 'data "aws_s3_bucket" "landing"' in main_tf and 'landing_folder = "${var.landing_prefix}/pershing/"' in main_tf
    assert main_tf.count("postcondition {") == 6  # warehouse, four schemas, tag; the bucket read fails by itself
    assert "Apply the foundation first." in main_tf
    assert 'value       = "${local.database}.BRONZE.PERSHING_POSITION_PIPE"' in main_tf
    assert '"${local.database}.BRONZE.PERSHING_GATE", "${local.database}.BRONZE.PERSHING_POSITION_PROCESS"' in main_tf
    versions = files["terraform/versions.tf"]
    assert 'source  = "snowflakedb/snowflake"' in versions and 'version = "~> 2.20"' in versions and 'required_version = ">= 1.10.0"' in versions
    assert "resource " not in main_tf  # the root holds no resources: a clean plan is the whole point
    test = files["terraform/tests/prerequisites.tftest.hcl"]
    assert 'run "plan_is_clean_when_the_foundation_provides_everything"' in test and 'run "a_missing_warehouse_fails_the_plan"' in test
    assert "expect_failures = [data.snowflake_warehouses.tier]" in test


@pytest.mark.skipif(TERRAFORM is None or not PROVIDER_CACHE.is_dir(), reason="terraform and the foundation's provider cache are needed")
def test_the_terraform_root_is_formatted_valid_and_its_mocked_plan_is_clean(compiled, tmp_path):
    root = write_bundle(compiled, tmp_path / "releases") / "terraform"
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}

    def tf(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([TERRAFORM, *args], cwd=root, env=env, capture_output=True, text=True)

    assert tf("fmt", "-check", "-diff").returncode == 0, tf("fmt", "-check", "-diff").stdout
    init = tf("init", "-input=false", "-backend=false", f"-plugin-dir={PROVIDER_CACHE}")
    assert init.returncode == 0, init.stderr
    validate = tf("validate")
    assert validate.returncode == 0, validate.stderr
    result = tf("test")
    assert result.returncode == 0 and "2 passed, 0 failed" in result.stdout, result.stdout + result.stderr


# ----------------------------------------------------------------------- docs


def test_docs_list_every_field_of_every_record_with_its_citation(files, compiled):
    doc = files["docs/pershing_position.md"]
    assert "### header (header) → `BRONZE.PERSHING_POSITION_FILE_METADATA` (one row per file)" in doc
    assert "### trailer (trailer) → `BRONZE.PERSHING_POSITION_FILE_METADATA` (one row per file)" in doc
    assert "| `TRAILER_DETAIL_COUNT` | trailer.detail_count | 4-12 | 9(9) | NUMBER(9,0) |  | page 40, line 5 |" in doc
    assert "| classifies the line | trailer.record_type | 1-3 | X(3) | STRING |  | page 40, line 3 |" in doc
    # every non-filler field of every record is a row with a citation
    rows = [line for line in doc.splitlines() if re.match(r"\| (`[A-Z_]+`|classifies the line) \| \w+\.\w+ \|", line)]
    assert len(rows) == sum(1 for r in compiled.spec.records for f in r.fields if f.name != "filler")
    assert all(re.search(r"\| page \d+(, line \d+)? \|$", row) for row in rows), [r for r in rows if not re.search(r"\| page \d+", row)]


def test_docs_cite_every_rule_and_dq_check(files, compiled):
    doc = files["docs/pershing_position.md"]
    assert "| `pershing_gcus.quantity_sign` | normalisation | confirmed | spec pershing_gcus 2017-07-25 page 13 line 6 |" in doc
    assert "| Rule | Kind | Level | Severity | Check | Metric | Measured on | Cited |" in doc
    total, cusip, price = compiled.dq_rules
    assert total.citation == "page 40, line 5"  # the trailer field's citation
    assert cusip.citation == "page 12, line 8"  # the cusip field's citation
    assert "`BRONZE.PERSHING_POSITION_FILE_METADATA` | page 40, line 5 |" in doc
    assert f"`BRONZE.PERSHING_POSITION_DETAIL` | {price.citation} |" in doc and price.citation.startswith("page ")
    assert "| Canonical column | Source | Transform | Rule | PII | Cited |" in doc
    assert "| `POSITION.ACCOUNT_NUMBER` (STRING) | detail.account_number (string) |  |  | account_number | page " in doc
    assert "## Infrastructure" in doc and "`terraform/` root" in doc


def test_a_dq_rule_may_state_its_own_citation(inputs, tmp_path):
    registry, catalog, packs = inputs
    text = VALID.replace("    trailer_field: detail_count\n", "    trailer_field: detail_count\n    citation: { document: Loader spec, page: 7, line: 12 }\n", 1)
    path = tmp_path / "pershing_position.yaml"
    path.write_text(text, encoding="utf-8")
    compiled = compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    assert compiled.dq_rules[0].citation == "Loader spec, page 7, line 12"
    assert compiled.to_dict()["dq_rules"][0]["citation"] == "Loader spec, page 7, line 12"


# ------------------------------------------------------------ PII and Atlan


def test_pii_follows_the_mappings_onto_bronze_silver_exceptions_and_the_catalog(files, compiled):
    assert compiled.pii_fields == {("detail", "account_number"): "account_number"}
    parse = files["pipeline/pershing_position_parse.sql"]
    assert 'ALTER DYNAMIC TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'account_number\';' in parse
    assert parse.index("SET TAG") > parse.index('CREATE OR REPLACE DYNAMIC ICEBERG TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL"')
    silver = files["ddl/silver_pershing_position.sql"]
    assert 'ALTER ICEBERG TABLE {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'account_number\';' in silver
    assert 'ALTER ICEBERG TABLE {{ DATABASE }}."EXCEPTIONS"."PERSHING_POSITION" MODIFY COLUMN "PAYLOAD" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'raw_record\';' in silver
    payload = json.loads(files["atlan/pershing_position.json"])
    columns = {("/".join(c["attributes"]["tableQualifiedName"].rsplit("/", 2)[1:]), c["attributes"]["name"]): c for c in payload["entities"] if c["typeName"] == "Column"}
    assert columns[("BRONZE/PERSHING_POSITION_DETAIL", "ACCOUNT_NUMBER")]["classifications"] == [{"typeName": "PII", "attributes": {"category": "account_number"}}]
    assert "classifications" not in columns[("BRONZE/PERSHING_POSITION_DETAIL", "CUSIP")]
    assert columns[("SILVER/PERSHING_POSITION_DETAIL", "ACCOUNT_NUMBER")]["classifications"][0]["attributes"]["category"] == "account_number"
    assert columns[("EXCEPTIONS/PERSHING_POSITION", "PAYLOAD")]["classifications"][0]["attributes"]["category"] == "raw_record"
    tables = [e for e in payload["entities"] if e["typeName"] == "Table"]
    assert all(e["attributes"]["ownerUsers"] == ["steward@example.com"] for e in tables) and len(tables) >= 8


# ---------------------------------------------------------------- lintable


def test_every_generated_test_parses_as_one_snowflake_select(compiled, tmp_path):
    root = write_bundle(compiled, tmp_path / "releases")
    bundle = load_bundle(root, tmp_path)
    result, problems = lint_bundle(bundle)
    assert problems == [] and result.tests == 12


def test_the_committed_bundles_lint_clean_and_a_broken_test_is_named(tmp_path, capsys):
    results, problems = lint_bundles(REPO / "releases", REPO)
    assert problems == [] and {r.bundle for r in results} == {"custodial-exceptions", "custodial-gold", "custodial-reference-data", "custodial-silver"}
    releases = tmp_path / "releases"
    shutil.copytree(REPO / "releases" / "custodial-gold", releases / "custodial-gold")
    broken = releases / "custodial-gold" / "tests" / "watermark_one_per_custodian_and_date.sql"
    broken.write_text('SELECT "CUSTODIAN_ID" FROM {{ DATABASE }}."GOLD"."WATERMARK" WHERE (COUNT(*) > 1;\n', encoding="utf-8")
    assert main(["--root", str(tmp_path), "bundles", "lint", str(releases)]) == 1
    out = capsys.readouterr().out
    assert "custodial-gold/tests/watermark_one_per_custodian_and_date.sql" in out and "does not parse as Snowflake SQL" in out
    two = releases / "custodial-gold" / "tests" / "positions_days_have_a_watermark.sql"
    two.write_text(two.read_text(encoding="utf-8") + "SELECT 1;\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "bundles", "lint", str(releases)]) == 1
    assert "a generated test is one statement; found 2" in capsys.readouterr().out
