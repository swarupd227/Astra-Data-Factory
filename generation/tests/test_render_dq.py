"""S3.2.7: each dq_rule becomes one data metric function or one association, and the control total returns the gap."""

from __future__ import annotations

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import CompileError, compile_config
from astra_data.render import render_bundle
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]

RULES = """dq_rules:
  - id: trailer_control_total
    kind: control_total
    level: file
    check: trailer record count equals the number of detail records
    trailer_field: detail_count
    aggregate: count
    severity: error
    owner: steward@example.com
  - id: cusip_present
    kind: not_null
    level: record
    check: every detail record names a security
    field: cusip
    severity: warning
    owner: steward@example.com
  - id: price_not_negative
    kind: range
    level: record
    check: a reported price is never negative
    field: price
    min: 0
    severity: info
    owner: steward@example.com
"""
assert RULES in VALID


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


def _compile(inputs, tmp_path, text):
    registry, catalog, packs = inputs
    path = tmp_path / "pershing_position.yaml"
    path.write_text(text, encoding="utf-8")
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)


def _with_rules(rules: str) -> str:
    return VALID.replace(RULES, "dq_rules:\n" + rules)


def _problems(inputs, tmp_path, rules: str) -> list[str]:
    with pytest.raises(CompileError) as excinfo:
        _compile(inputs, tmp_path, _with_rules(rules))
    return [p.message for p in excinfo.value.problems]


@pytest.fixture(scope="module")
def files(inputs):
    registry, catalog, packs = inputs
    return render_bundle(compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO))


def test_the_control_total_is_a_custom_dmf_on_the_file_metadata_that_returns_the_gap(files):
    dmf = files["dq/dmf_pershing_position.sql"]
    assert 'CREATE OR REPLACE DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_TRAILER_CONTROL_TOTAL"(ARG_T TABLE("TRAILER_DETAIL_COUNT" NUMBER(9,0), "DETAIL_COUNT" NUMBER(18,0)))' in dmf
    assert 'SELECT COALESCE(SUM(ABS(COALESCE("TRAILER_DETAIL_COUNT", 0) - COALESCE("DETAIL_COUNT", 0))), 0) FROM ARG_T' in dmf
    assert "Returns the gap between the trailer total and the file''s records, summed over files; 0 when the rule holds." in dmf
    assert 'ALTER DYNAMIC TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILE_METADATA" SET DATA_METRIC_SCHEDULE = \'TRIGGER_ON_CHANGES\';' in dmf
    assert 'ALTER DYNAMIC TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILE_METADATA" ADD DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_TRAILER_CONTROL_TOTAL" ON ("TRAILER_DETAIL_COUNT", "DETAIL_COUNT");' in dmf
    test = files["tests/pershing_position_dq_trailer_control_total.sql"]
    assert 'SELECT "FILE_NAME", "TRAILER_DETAIL_COUNT", "DETAIL_COUNT", COALESCE("TRAILER_DETAIL_COUNT", 0) - COALESCE("DETAIL_COUNT", 0) AS "GAP"' in test
    assert 'WHERE COALESCE("TRAILER_DETAIL_COUNT", 0) - COALESCE("DETAIL_COUNT", 0) <> 0;' in test


def test_every_rule_is_one_function_or_one_association(files):
    dmf = files["dq/dmf_pershing_position.sql"]
    detail = '{{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL"'
    rules = dmf[dmf.index("-- dq_rules of the config") :]
    # a system function measures not_null: one association, no function of its own
    assert f'ALTER DYNAMIC TABLE {detail} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.NULL_COUNT ON ("CUSIP");' in rules
    assert "CUSIP_PRESENT" not in rules
    # a range needs a function of its own: one function, one association
    assert 'CREATE OR REPLACE DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_PRICE_NOT_NEGATIVE"(ARG_T TABLE("PRICE" NUMBER(15,6)))' in rules
    assert 'SELECT COUNT(*) FROM ARG_T WHERE "PRICE" IS NOT NULL AND ("PRICE" < 0)' in rules
    assert f'ALTER DYNAMIC TABLE {detail} ADD DATA METRIC FUNCTION {{{{ DATABASE }}}}."CONTROL"."PERSHING_POSITION_PRICE_NOT_NEGATIVE" ON ("PRICE");' in rules
    assert rules.count("ADD DATA METRIC FUNCTION") == 3 and rules.count("CREATE OR REPLACE DATA METRIC FUNCTION") == 2
    # the detail table's schedule is set once, with the spec's own measures
    assert dmf.count(f"ALTER DYNAMIC TABLE {detail} SET DATA_METRIC_SCHEDULE") == 1
    # only error-severity rules get a failing-rows test
    assert "tests/pershing_position_dq_trailer_control_total.sql" in files
    assert "tests/pershing_position_dq_cusip_present.sql" not in files and "tests/pershing_position_dq_price_not_negative.sql" not in files


def test_the_compiled_rules_say_what_they_measure(inputs):
    registry, catalog, packs = inputs
    compiled = compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)
    total, cusip, price = compiled.dq_rules
    assert (total.kind, total.table, total.record, total.aggregate, total.trailer_field) == ("control_total", "metadata", "detail", "count", "detail_count")
    assert [c.name for c in total.columns] == ["TRAILER_DETAIL_COUNT", "DETAIL_COUNT"] and total.system_function is None
    assert (cusip.table, cusip.system_function, cusip.columns[0].name) == ("record", "NULL_COUNT", "CUSIP")
    assert (price.kind, price.minimum, price.maximum, price.columns[0].sql_type) == ("range", 0, None, "NUMBER(15,6)")
    payload = compiled.to_dict()["dq_rules"][0]
    assert payload["columns"] == [{"name": "TRAILER_DETAIL_COUNT", "type": "NUMBER(9,0)"}, {"name": "DETAIL_COUNT", "type": "NUMBER(18,0)"}] and payload["system_function"] is None


def test_a_sum_control_total_adds_the_total_to_the_file_metadata(inputs, tmp_path):
    compiled = _compile(inputs, tmp_path, _with_rules(
        "  - { id: quantity_total, kind: control_total, level: file, check: 'trailer count equals the sum of quantities', trailer_field: detail_count, aggregate: sum, field: quantity }\n"
    ))
    files = render_bundle(compiled)
    rule = compiled.dq_rules[0]
    assert [c.name for c in rule.columns] == ["TRAILER_DETAIL_COUNT", "DETAIL_QUANTITY_TOTAL"] and rule.columns[1].sql_type == "NUMBER(38,5)"
    parse = files["pipeline/pershing_position_parse.sql"]
    assert 's0."TOTAL" AS "DETAIL_QUANTITY_TOTAL",' in parse
    assert 'LEFT JOIN (SELECT "FILE_NAME", SUM("QUANTITY") AS "TOTAL" FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL" GROUP BY "FILE_NAME") s0' in parse
    assert 'ON ("TRAILER_DETAIL_COUNT", "DETAIL_QUANTITY_TOTAL");' in files["dq/dmf_pershing_position.sql"]


def test_unique_accepted_values_and_condition_rules(inputs, tmp_path):
    compiled = _compile(inputs, tmp_path, _with_rules(
        "  - { id: one_key, kind: unique, level: record, check: 'one row per account and security', fields: [account_number, cusip] }\n"
        "  - { id: one_account, kind: unique, level: record, check: 'one row per account', field: account_number }\n"
        "  - { id: known_types, kind: accepted_values, level: record, check: 'record type is known', field: record_type, values: [DTL, 'X1'] }\n"
        "  - { id: priced_when_held, kind: condition, level: pair, check: 'a held position has a price', condition: 'quantity = 0 OR price IS NOT NULL', severity: warning }\n"
    ))
    files = render_bundle(compiled)
    dmf = files["dq/dmf_pershing_position.sql"]
    assert 'CREATE OR REPLACE DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_ONE_KEY"(ARG_T TABLE("ACCOUNT_NUMBER" STRING, "CUSIP" STRING))' in dmf
    assert 'SELECT COALESCE(SUM("N" - 1), 0) FROM (SELECT COUNT(*) AS "N" FROM ARG_T GROUP BY "ACCOUNT_NUMBER", "CUSIP" HAVING COUNT(*) > 1)' in dmf
    assert 'ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.DUPLICATE_COUNT ON ("ACCOUNT_NUMBER");' in dmf
    assert 'WHERE "RECORD_TYPE" IS NOT NULL AND "RECORD_TYPE" NOT IN (\'DTL\', \'X1\')' in dmf
    # a pair-level rule measures the Silver table with the columns the condition names
    assert 'CREATE OR REPLACE DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_PRICED_WHEN_HELD"(ARG_T TABLE("QUANTITY" NUMBER(18,5), "PRICE" NUMBER(15,6)))' in dmf
    assert "SELECT COUNT(*) FROM ARG_T WHERE NOT COALESCE((quantity = 0 OR price IS NOT NULL), FALSE)" in dmf
    assert 'ALTER ICEBERG TABLE {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" SET DATA_METRIC_SCHEDULE = \'TRIGGER_ON_CHANGES\';' in dmf
    assert 'ALTER ICEBERG TABLE {{ DATABASE }}."SILVER"."PERSHING_POSITION_DETAIL" ADD DATA METRIC FUNCTION {{ DATABASE }}."CONTROL"."PERSHING_POSITION_PRICED_WHEN_HELD" ON ("QUANTITY", "PRICE");' in dmf
    unique = files["tests/pershing_position_dq_one_key.sql"]
    assert 'GROUP BY "ACCOUNT_NUMBER", "CUSIP"' in unique and "HAVING COUNT(*) > 1;" in unique
    assert 'SELECT "FILE_NAME", "LINE_NUMBER", "RECORD_TYPE"' in files["tests/pershing_position_dq_known_types.sql"]


def test_rules_are_checked_against_the_spec(inputs, tmp_path):
    assert _problems(inputs, tmp_path, "  - { id: t, kind: control_total, level: record, check: x, trailer_field: detail_count }\n") == [
        "dq_rules[0] (t): a control_total rule is file level, not record: it compares a trailer value with the file's records"
    ]
    assert _problems(inputs, tmp_path, "  - { id: t, kind: control_total, level: file, check: x, trailer_field: nope }\n") == [
        "dq_rules[0] (t): trailer_field 'nope' is not a field of the trailer record; its fields are detail_count"
    ]
    assert _problems(inputs, tmp_path, "  - { id: t, kind: control_total, level: file, check: x, trailer_field: detail_count, aggregate: sum, field: cusip }\n") == [
        "dq_rules[0] (t): field 'cusip' is cusip, not a number; a control total sums integer or decimal fields".replace("is cusip", "is string")
    ]
    assert _problems(inputs, tmp_path, "  - { id: n, kind: not_null, level: record, check: x, field: ticker }\n")[0].startswith(
        "dq_rules[0] (n): field 'ticker' is not a column of record 'detail'; its columns are RECORD_TYPE, ACCOUNT_NUMBER, "
    )
    assert _problems(inputs, tmp_path, "  - { id: n, kind: not_null, level: file, check: x, field: cusip }\n") == [
        "dq_rules[0] (n): a not_null rule measures rows, so its level is record, pair or business, not file; file-level rules are control totals"
    ]
    assert _problems(inputs, tmp_path, "  - { id: r, kind: range, level: record, check: x, field: cusip, min: 0 }\n") == [
        "dq_rules[0] (r): field 'cusip' is string; a range applies to integer, decimal, date or time fields"
    ]
    assert _problems(inputs, tmp_path, "  - { id: r, kind: range, level: record, check: x, field: price, min: 5, max: 1 }\n") == ["dq_rules[0] (r): min 5 is greater than max 1"]
    assert _problems(inputs, tmp_path, "  - { id: c, kind: condition, level: record, check: x, condition: '1 = 1' }\n")[0].startswith(
        "dq_rules[0] (c): condition names no column of record 'detail'; its columns are "
    )
    assert _problems(inputs, tmp_path, "  - { id: a, kind: accepted_values, level: record, check: x, field: cusip }\n") == [
        "dq_rules[0] (a): an accepted_values rule needs field and values: the field and the values it may hold"
    ]
    # the schema rejects a rule without a kind before the compiler sees it
    assert _problems(inputs, tmp_path, "  - { id: k, level: record, check: x, field: cusip }\n") == ["dq_rules[0]: missing required field kind"]
