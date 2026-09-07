"""The Silver MERGE stage (S3.2.3)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import compile_config
from astra_data.render import RenderError, render_bundle
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


@pytest.fixture(scope="module")
def files(inputs):
    registry, catalog, packs = inputs
    return render_bundle(compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO))


def test_silver_table_holds_the_logical_record_with_scope_and_retirement(files):
    ddl = files["ddl/silver_pershing_position.sql"]
    assert 'CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" (' in ddl
    for column in ("ACCOUNT_NUMBER", "CUSIP", "QUANTITY", "PRICE", "AS_OF_DATE", "SECURITY_TYPE", "SCOPE_REMOTE_ID", "BUSINESS_DATE", "FIRST_FILE", "LAST_FILE", "LAST_LINE", "RETIRED_AT", "RETIRED_BY_FILE"):
        assert f'"{column}"' in ddl, column
    import re

    assert re.search(r'"ACCOUNT_NUMBER"\s+STRING NOT NULL', ddl) and re.search(r'"CUSIP"\s+STRING NOT NULL', ddl)  # merge keys
    assert '"FILLER"' not in ddl
    assert 'CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."EXCEPTIONS"."PERSHING_POSITION" (' in ddl
    for column in ("EXCEPTION_ID", "REJECTION_CODE", "LEVEL", "STAGE", "PAYLOAD", "RECORD_KEY", "STATUS", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "RUN_ID"):
        assert f'"{column}"' in ddl, column


def test_full_mode_replaces_and_delta_mode_merges(files):
    sql = files["pipeline/pershing_position_merge.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_MERGE"(RUN_ID STRING)' in sql
    assert "SELECT \"FILE_NAME\", \"ROW_COUNT\" FROM {{ DATABASE }}.\"BRONZE\".\"PERSHING_POSITION_FILES\" WHERE \"STATUS\" = 'pending' ORDER BY \"FILE_LAST_MODIFIED\", \"FILE_NAME\"" in sql
    assert "LET mode STRING := (SELECT CASE :mode_code WHEN 'R' THEN 'refresh' WHEN 'U' THEN 'update' END);" in sql
    assert 'LET mode_code STRING := (SELECT "HEADER_REFRESH_FLAG"::STRING FROM' in sql and 'LET business_date DATE := (SELECT "HEADER_FILE_DATE" FROM' in sql
    assert 'LET scope_remote_id STRING := (SELECT "HEADER_REMOTE_ID"::STRING FROM' in sql
    # insert or update on scope and keys
    assert 'MERGE INTO {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" t' in sql
    assert 'ON t."ACCOUNT_NUMBER" = s."ACCOUNT_NUMBER" AND t."CUSIP" = s."CUSIP" AND t."SCOPE_REMOTE_ID" IS NOT DISTINCT FROM :scope_remote_id' in sql
    assert 'WHEN MATCHED THEN UPDATE SET "RECORD_TYPE" = s."RECORD_TYPE", "QUANTITY" = s."QUANTITY"' in sql and '"RETIRED_AT" = NULL' in sql
    assert 'WHEN NOT MATCHED THEN INSERT ("RECORD_TYPE", "ACCOUNT_NUMBER", "CUSIP", "QUANTITY", "QUANTITY_SIGN", "PRICE", "AS_OF_DATE", "SECURITY_TYPE", "SCOPE_REMOTE_ID", "BUSINESS_DATE"' in sql
    # refresh retires what the file no longer carries; update carries it forward
    refresh = sql[sql.index("IF (mode = 'refresh') THEN"):]
    assert 'SET "RETIRED_AT" = SYSDATE(), "RETIRED_BY_FILE" = :file_name' in refresh and "retired := SQLROWCOUNT;" in refresh
    assert 'carried := (SELECT COUNT(*) FROM {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" t WHERE t."RETIRED_AT" IS NULL' in refresh
    assert 'INSERT INTO {{ DATABASE }}."CONTROL"."MERGE_LOG" ("CUSTODIAN_ID", "SOURCE_ID", "SCOPE", "BUSINESS_DATE", "MODE", "FILE_NAME", "ROWS_INSERTED", "ROWS_UPDATED", "ROWS_CARRIED", "ROWS_RETIRED", "LOADED_AT")' in sql
    assert "VALUES ('pershing', 'pershing_position', 'remote_id=' || COALESCE(:scope_remote_id, 'NULL'), :business_date, :mode, :file_name, :inserted, :updated, :carried, :retired, SYSDATE());" in sql
    assert "UPDATE {{ DATABASE }}.\"BRONZE\".\"PERSHING_POSITION_FILES\" SET \"STATUS\" = 'merged'" in sql


def test_files_are_merged_only_once_their_parse_is_complete(files):
    sql = files["pipeline/pershing_position_merge.sql"]
    assert "IF (line_count IS NULL OR row_count IS NULL OR line_count <> row_count) THEN CONTINUE; END IF;" in sql
    assert 'LET detail_expected NUMBER := (SELECT "DETAIL_COUNT" - (SELECT COUNT(*) FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PARSE_PROBLEMS" p WHERE p."FILE_NAME" = :file_name AND p."LEVEL" = \'record\' AND p."RECORD" = \'detail\')' in sql
    assert "IF (detail_parsed <> detail_expected) THEN CONTINUE; END IF;" in sql


def test_unknown_mode_out_of_order_blank_and_duplicate_keys_are_exceptions(files):
    sql = files["pipeline/pershing_position_merge.sql"]
    assert "'MERGE_MODE_UNKNOWN', 'file', 'merge'" in sql and "is not one of ''R'' = refresh, ''U'' = update" in sql and "the file has no header record, so the merge mode is unknown" in sql
    assert "'MERGE_OUT_OF_ORDER', 'file', 'merge'" in sql and "is earlier than the ' || :latest::STRING || ' already in Silver for this scope" in sql
    assert sql.count("SET \"STATUS\" = 'rejected'") == 2
    assert "'MERGE_KEY_BLANK', 'record', 'merge'" in sql and 'WHERE "ACCOUNT_NUMBER" IS NULL OR "CUSIP" IS NULL;' in sql
    assert "'MERGE_DUPLICATE_KEY', 'record', 'merge'" in sql and 'ROW_NUMBER() OVER (PARTITION BY "ACCOUNT_NUMBER", "CUSIP" ORDER BY "LINE_NUMBER") AS "NTH"' in sql and 'WHERE "NTH" > 1;' in sql
    assert "TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*))" in sql  # the rejected row travels with the exception
    assert "'account_number=' || COALESCE(\"ACCOUNT_NUMBER\"::STRING, 'NULL') || ', ' || 'cusip=' || COALESCE(\"CUSIP\"::STRING, 'NULL')" in sql
    assert "'PAIR_" not in sql  # GCUS is not paired


def _paired_inputs(tmp_path: Path, inputs):
    """A copy of the registry whose split-position spec also merges, and a config for it."""
    registry_root = tmp_path / "specs"
    shutil.copytree(REPO / "specs", registry_root)
    spec_path = registry_root / "split_position_example" / "2026-01-01.yaml"
    text = spec_path.read_text(encoding="utf-8")
    assert "merge:" not in text
    text = text.replace("pairing:", "merge:\n  mode_field: refresh_flag\n  modes: { F: refresh, D: update }\n  scope: []\n  business_date_field: file_date\n  keys: [account_number, cusip]\n\npairing:", 1)
    text = text.replace(
        "      - { name: filler, position: { start: 10, length: 51 }, picture: X(51), citation: { page: 2, line: 7 } }",
        "      - { name: refresh_flag, position: { start: 10, length: 1 }, picture: X(1), type: code, citation: { page: 2, line: 6 }, codes: [{ value: F, meaning: full }, { value: D, meaning: delta }] }\n"
        "      - { name: filler, position: { start: 11, length: 50 }, picture: X(50), citation: { page: 2, line: 7 } }",
    )
    spec_path.write_text(text, encoding="utf-8")
    registry, problems = Registry.load(registry_root, tmp_path)
    if problems:
        pytest.skip("the split-position spec needs header fields refresh_flag and file_date to merge: " + "; ".join(p.message for p in problems))
    _, catalog, packs = inputs
    spec = registry.get("split_position_example", "2026-01-01")
    config = VALID.replace("id: pershing_position", "id: split_positions").replace("custodian: pershing", f"custodian: {spec.custodians[0]}").replace("  family: pershing_gcus\n", "")
    config = config.replace("id: pershing_gcus            # specs/pershing_gcus/", "id: split_position_example").replace('version: "2017-07-25"        # specs/pershing_gcus/2017-07-25.yaml', 'version: "2026-01-01"')
    config = config.replace("effective_from: 2026-09-01", "effective_from: 2026-01-01")
    config = config[: config.index("mappings:")] + "rules: []\n\ndelivery:\n  cutoff_time: \"06:00\"\n  timezone: UTC\n  files:\n    - pattern: split/POS_%.dat\n"
    path = tmp_path / "split_positions.yaml"
    path.write_text(config, encoding="utf-8")
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)


def test_paired_rows_only_and_unpaired_go_to_exceptions(tmp_path, inputs):
    compiled = _paired_inputs(tmp_path, inputs)
    files = render_bundle(compiled)
    sql = files["pipeline/split_positions_merge.sql"]
    assert "records paired on account_number, cusip" in sql
    assert 'WITH r0 AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY "ACCOUNT_NUMBER", "CUSIP" ORDER BY "LINE_NUMBER") AS "NTH" FROM {{ DATABASE }}."BRONZE"."SPLIT_POSITIONS_HOLDING"' in sql
    assert 'JOIN r1 ON r1."ACCOUNT_NUMBER" = r0."ACCOUNT_NUMBER" AND r1."CUSIP" = r0."CUSIP" AND r1."NTH" = 1 WHERE r0."NTH" = 1' in sql
    for code in ("PAIR_KEY_BLANK", "PAIR_DUPLICATE", "PAIR_INCOMPLETE"):
        assert f"'{code}', 'record', 'merge'" in sql, code
    assert "holding record whose partner record never appeared in the file" in sql and "valuation record whose partner record never appeared in the file" in sql
    ddl = files["ddl/silver_split_positions.sql"]
    assert 'CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."SPLIT_POSITIONS_POSITION" (' in ddl
    assert '"MARKET_VALUE"' in ddl and '"QUANTITY"' in ddl


def test_a_spec_without_a_merge_block_cannot_be_merged(inputs, tmp_path):
    registry, catalog, packs = inputs
    text = VALID.replace("id: pershing_position", "id: prices_csv").replace("custodian: pershing", "custodian: example_custodian").replace("file_type: position", "file_type: price").replace("  family: pershing_gcus\n", "")
    text = text.replace("id: pershing_gcus            # specs/pershing_gcus/", "id: csv_price_example").replace('version: "2017-07-25"        # specs/pershing_gcus/2017-07-25.yaml', 'version: "2026-01-01"')
    text = text.replace("pattern: fixed_width_multi_record", "pattern: delimited_file").replace("effective_from: 2026-09-01", "effective_from: 2026-01-01")
    text = text[: text.index("mappings:")] + "rules: []\n\ndelivery:\n  cutoff_time: \"06:00\"\n  timezone: UTC\n  files:\n    - pattern: example/prices_%.csv\n"
    path = tmp_path / "prices_csv.yaml"
    path.write_text(text, encoding="utf-8")
    compiled = compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    with pytest.raises(RenderError) as excinfo:
        render_bundle(compiled)
    assert any("declares no merge block" in p.message for p in excinfo.value.problems)
