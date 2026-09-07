"""The parse dynamic tables (S3.2.2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import compile_config
from astra_data.render import RenderError, render_bundle
from astra_data.render.parse import field_sql, render_parse
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
def compiled(inputs):
    registry, catalog, packs = inputs
    return compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)


@pytest.fixture(scope="module")
def parse(compiled) -> str:
    return render_bundle(compiled)["pipeline/pershing_position_parse.sql"]


def test_every_configured_gcus_field_is_present_with_its_type(parse, compiled):
    block = parse[parse.index('CREATE OR REPLACE DYNAMIC ICEBERG TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL"'):]
    block = block[: block.index(";")]
    detail = compiled.spec.record("detail")
    for field in detail.fields:
        if field.name == "filler":
            continue
        assert f'AS "{field.name.upper()}"' in block, field.name
    assert '"FILLER"' not in block
    assert "IFF(TRIM(SUBSTR(\"PADDED\", 4, 10)) = '', NULL, RTRIM(SUBSTR(\"PADDED\", 4, 10))) AS \"ACCOUNT_NUMBER\"" in block
    assert '{{ DATABASE }}."CONTROL"."SIGNED_IMPLIED_DECIMAL"(SUBSTR("PADDED", 23, 18), SUBSTR("PADDED", 41, 1), 5, ARRAY_CONSTRUCT(\'+\'), ARRAY_CONSTRUCT(\'-\'), ARRAY_CONSTRUCT(\' \'))::NUMBER(18,5) AS "QUANTITY"' in block  # the spec's sign convention
    assert 'IFF(REGEXP_LIKE(TRIM(SUBSTR("PADDED", 42, 15)), \'[0-9]+\'), {{ DATABASE }}."CONTROL"."IMPLIED_DECIMAL"(SUBSTR("PADDED", 42, 15), 6)::NUMBER(15,6), NULL) AS "PRICE"' in block
    assert "TRY_TO_DATE(TRIM(SUBSTR(\"PADDED\", 57, 8)), 'YYYYMMDD')" in block and 'AS "AS_OF_DATE"' in block
    assert "CASE WHEN SUBSTR(\"PADDED\", 65, 2) IN ('EQ', 'FI', 'MF', 'OP') THEN SUBSTR(\"PADDED\", 65, 2)" in block and 'AS "SECURITY_TYPE"' in block
    assert "CASE WHEN SUBSTR(\"PADDED\", 41, 1) IN ('+', '-', ' ') THEN SUBSTR(\"PADDED\", 41, 1)" in block  # a blank code is a code
    assert block.endswith('WHERE "RECORD_TYPE" = \'detail\' AND NOT "TOO_LONG"')


def test_target_lag_and_warehouse_come_from_the_config(parse):
    assert parse.count("TARGET_LAG = '10 minutes'") == 3  # detail, parse problems, file metadata
    assert parse.count("WAREHOUSE = {{ WAREHOUSE_MEDIUM }}") == 3
    assert "BASE_LOCATION = 'bronze/pershing_position_detail/'" in parse


def test_target_lag_changes_with_the_config(inputs, tmp_path):
    registry, catalog, packs = inputs
    path = tmp_path / "pershing_position.yaml"
    path.write_text(VALID.replace("target_lag_minutes: 10", "target_lag_minutes: 45"), encoding="utf-8")
    compiled = compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    files = render_bundle(compiled)
    assert files["pipeline/pershing_position_parse.sql"].count("TARGET_LAG = '45 minutes'") == 3
    assert "SCHEDULE = '45 MINUTE'" not in files["pipeline/pershing_position_tasks.sql"]  # the process task runs after the custodian's gate, not on the lag (S3.2.6)
    assert compiled.to_dict()["processing"] == {"target_lag_minutes": 45}

    without = "\n".join(line for line in VALID.splitlines() if "processing:" not in line and "target_lag_minutes" not in line) + "\n"
    path.write_text(without, encoding="utf-8")
    assert compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path).target_lag_minutes == 15


def test_lines_are_classified_by_the_match_rules(parse):
    view = parse[parse.index('CREATE OR REPLACE VIEW {{ DATABASE }}."BRONZE"."PERSHING_POSITION_CLASSIFIED"'):]
    view = view[: view.index(";")]
    assert 'RPAD("LINE", 120) AS "PADDED"' in view and 'LENGTH("LINE") > 120 AS "TOO_LONG"' in view
    assert "WHEN SUBSTR(\"PADDED\", 1, 3) = 'HDR' THEN 'header'" in view
    assert "WHEN SUBSTR(\"PADDED\", 1, 3) = 'DTL' THEN 'detail'" in view
    assert "WHEN SUBSTR(\"PADDED\", 1, 3) = 'TRL' THEN 'trailer'" in view
    assert 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_LINES"' in view


def test_rows_failing_record_level_dq_are_excluded_and_counted(parse):
    problems = parse[parse.index('CREATE OR REPLACE DYNAMIC ICEBERG TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PARSE_PROBLEMS"'):]
    problems = problems[: problems.index("\n);")]
    assert "'record', 'RECORD_TYPE_UNKNOWN', 'no record type matches this line'" in problems and 'WHERE "RECORD_TYPE" IS NULL' in problems
    assert "'record', 'RECORD_TOO_LONG', 'line is ' || \"LINE_LENGTH\" || ' characters, longer than the record length 120'" in problems
    assert "'record', 'RECORD_DUPLICATE_HEADER', 'a second header record; a file has at most one'" in problems
    assert "'file', 'FILE_HEADER_MISSING', 'the file has no header record'" in problems and "'file', 'FILE_TRAILER_MISSING', 'the file has no trailer record'" in problems
    assert "HAVING COUNT_IF(\"RECORD_TYPE\" = 'trailer') = 0" in problems
    assert "'detail', 'account_number', 'field', 'FIELD_REQUIRED_BLANK', 'required field is blank'" in problems
    assert "'detail', 'quantity', 'field', IFF({{ DATABASE }}.\"CONTROL\".\"SIGNED_IMPLIED_DECIMAL_PROBLEM\"(" in problems and "'FIELD_NOT_NUMERIC', 'FIELD_SIGN_INVALID')" in problems
    assert "'detail', 'as_of_date', 'field', 'FIELD_DATE_INVALID'" in problems
    assert "'detail', 'security_type', 'field', 'FIELD_CODE_UNKNOWN'" in problems and "'header', 'refresh_flag', 'field', 'FIELD_CODE_UNKNOWN'" in problems
    assert "'trailer', 'detail_count', 'field', 'FIELD_NOT_NUMERIC'" in problems

    metadata = parse[parse.index('CREATE OR REPLACE DYNAMIC ICEBERG TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILE_METADATA"'):]
    assert 'COUNT_IF("LEVEL" = \'record\') AS "EXCLUDED_ROWS"' in metadata and 'COUNT_IF("LEVEL" = \'field\') AS "FIELD_PROBLEMS"' in metadata
    assert 'COUNT_IF("RECORD_TYPE" = \'detail\') AS "DETAIL_COUNT"' in metadata and 'AS "TRAILER_DETAIL_COUNT"' in metadata and 'AS "HEADER_REFRESH_FLAG"' in metadata
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY \"FILE_NAME\" ORDER BY \"LINE_NUMBER\") = 1" in metadata


def test_value_rules_follow_the_pattern_library(compiled):
    spec = compiled.spec
    detail = spec.record("detail")
    header = spec.record("header")
    sql = field_sql(spec, detail, detail.field("cusip"))
    assert sql.problem is None and sql.value == "IFF(TRIM(SUBSTR(\"PADDED\", 14, 9)) = '', NULL, RTRIM(SUBSTR(\"PADDED\", 14, 9)))"
    date = field_sql(spec, header, header.field("file_date"))
    assert "REGEXP_LIKE(TRIM(SUBSTR(\"PADDED\", 4, 8)), '0+')" in date.value  # all zeros is NULL, not a problem
    assert date.code == "'FIELD_DATE_INVALID'"
    count = field_sql(spec, spec.record("trailer"), spec.record("trailer").field("detail_count"))
    assert "TO_NUMBER(TRIM(SUBSTR(\"PADDED\", 4, 9)), 38, 0)" in count.value and count.code == "'FIELD_NOT_NUMERIC'"


def test_delimited_specs_render_with_split_part_and_explicit_numbers(inputs, tmp_path):
    registry, catalog, packs = inputs
    text = VALID.replace("id: pershing_position", "id: prices_csv").replace("custodian: pershing", "custodian: example_custodian").replace("file_type: position", "file_type: price").replace("  family: pershing_gcus\n", "")
    text = text.replace("id: pershing_gcus            # specs/pershing_gcus/", "id: csv_price_example").replace('version: "2017-07-25"        # specs/pershing_gcus/2017-07-25.yaml', 'version: "2026-01-01"')
    text = text.replace("pattern: fixed_width_multi_record", "pattern: delimited_file").replace("effective_from: 2026-09-01", "effective_from: 2026-01-01")
    text = text[: text.index("mappings:")] + "rules: []\n\ndelivery:\n  cutoff_time: \"06:00\"\n  timezone: UTC\n  files:\n    - pattern: example/prices_%.csv\n"
    path = tmp_path / "prices_csv.yaml"
    path.write_text(text, encoding="utf-8")
    compiled = compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    parse = render_parse(compiled)
    assert 'SPLIT_PART("LINE", \',\', 4)' in parse
    assert "TO_NUMBER(REPLACE(TRIM(SPLIT_PART(\"LINE\", ',', 4)), ',', ''), 38, 12)" in parse
    assert '"TOO_LONG"' in parse and "FALSE AS \"TOO_LONG\"" in parse


def test_unsupported_formats_and_quoted_delimited_files_are_render_problems(compiled):
    from astra_data.render import parse as parse_module

    spec = compiled.spec
    quoted = compiled.__class__(**{**compiled.__dict__, "spec": spec.__class__(**{**spec.__dict__, "file": {**spec.file, "format": "delimited", "delimiter": ",", "quote": "\""}})})
    messages = [p.message for p in parse_module.problems(quoted)]
    assert any("quoting or escaping" in m for m in messages)
