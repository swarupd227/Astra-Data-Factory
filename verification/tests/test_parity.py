"""Parity engine (S4.2.1): row and field comparison between legacy golden output and lakehouse output."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.golden import LocalStore, dataset_prefix
from astra_verification.parity import (
    FieldMapping,
    MATCH_RATE_TARGET,
    ParityError,
    ParityMapping,
    Tolerance,
    check,
    compare_rows,
    lakehouse_columns,
    lakehouse_query,
    load_legacy_rows,
    load_lakehouse_rows,
    load_parity,
    run,
)

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden"
PARITY = GOLDEN / "pershing" / "parity.yaml"
TEXT = PARITY.read_text(encoding="utf-8")


def _mapping() -> ParityMapping:
    m, problems = load_parity(PARITY, REPO)
    assert problems == [], [p.format() for p in problems]
    return m


# ------------------------------------------------------------- the file


def test_the_committed_mapping_loads_and_matches_the_real_position_entity():
    mapping = _mapping()
    assert (mapping.custodian, mapping.legacy_output, mapping.schema, mapping.table) == ("pershing", "positions", "SILVER", "POSITION")
    assert [(k.legacy, k.lakehouse) for k in mapping.keys] == [("AccountNumber", "ACCOUNT_NUMBER"), ("Cusip", "CUSTODIAN_SECURITY_ID"), ("AsOfDate", "AS_OF_DATE")]
    price = next(f for f in mapping.fields if f.legacy == "Price")
    assert price.lakehouse == "PRICE" and price.type == "decimal" and price.tolerance == Tolerance("decimal_places", places=4)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda t: t.replace("- { legacy: AsOfDate, lakehouse: AS_OF_DATE, type: date }", "- { legacy: AsOfDate, lakehouse: AS_OF_DATE, type: date, tolerance: { kind: exact } }"), "a key cannot have a tolerance"),
        (lambda t: t.replace("{ legacy: MarketValue, lakehouse: MARKET_VALUE", "{ legacy: Quantity, lakehouse: MARKET_VALUE"), "legacy column 'Quantity' is already used by"),
        (lambda t: t.replace("kind: decimal_places, places: 4", "kind: decimal_places"), "missing required field places"),
        (lambda t: t.replace("kind: decimal_places, places: 5", "kind: absolute"), "missing required field epsilon"),
        (lambda t: t.replace("legacy:\n  output: positions\n", "legacy: {}\n"), "missing required field output"),
    ],
)
def test_the_validator_names_what_is_wrong(tmp_path, mutate, expected):
    path = tmp_path / "parity.yaml"
    path.write_text(mutate(TEXT), encoding="utf-8")
    m, problems = load_parity(path, tmp_path)
    assert m is None and any(expected in p.message for p in problems), [p.message for p in problems]


def test_load_parity_reports_a_missing_file_instead_of_raising(tmp_path):
    m, problems = load_parity(tmp_path / "nope" / "parity.yaml", tmp_path)
    assert m is None and [p.message for p in problems] == ["no such file"]


def test_check_walks_a_golden_directory(tmp_path):
    mappings, problems = check(GOLDEN, REPO)
    assert problems == [] and [m.custodian for m in mappings] == ["pershing"]
    (tmp_path / "golden" / "wrong").mkdir(parents=True)
    (tmp_path / "golden" / "wrong" / "parity.yaml").write_text(TEXT, encoding="utf-8")
    _, problems = check(tmp_path / "golden", tmp_path)
    assert any("must match the directory 'wrong'" in p.message for p in problems)


# --------------------------------------------------------- tolerance


@pytest.mark.parametrize(
    "tolerance, legacy, lakehouse, expected",
    [
        (Tolerance("exact"), "PERSHING", "PERSHING", True),
        (Tolerance("exact"), "PERSHING", "pershing", False),
        (Tolerance("exact"), None, None, True),
        (Tolerance("exact"), None, "x", False),
        (Tolerance("exact"), "x", None, False),
        (Tolerance("decimal_places", places=4), "227.1200", "227.11999999", True),  # rounds to 227.1200 both ways
        (Tolerance("decimal_places", places=4), "227.1200", "227.1250", False),
        (Tolerance("absolute", epsilon=0.01), "100.001", "100.005", True),
        (Tolerance("absolute", epsilon=0.01), "100.00", "100.02", False),
        (Tolerance("relative", epsilon=0.001), "1000.00", "1000.50", True),
        (Tolerance("relative", epsilon=0.001), "1000.00", "1002.00", False),
        (Tolerance("decimal_places", places=2), "not-a-number", "not-a-number", True),  # falls back to exact when not numeric
        (Tolerance("decimal_places", places=2), "not-a-number", "also-not", False),
    ],
)
def test_tolerance_matches(tolerance, legacy, lakehouse, expected):
    assert tolerance.matches(legacy, lakehouse) is expected


# ------------------------------------------------------- the comparison engine


def _test_mapping() -> ParityMapping:
    return ParityMapping(
        custodian="pershing",
        description="",
        legacy_output="positions",
        schema="SILVER",
        table="POSITION",
        custodian_column="CUSTODIAN_ID",
        business_date_column="AS_OF_DATE",
        keys=(FieldMapping("AccountNumber", "ACCOUNT_NUMBER"), FieldMapping("Cusip", "CUSTODIAN_SECURITY_ID")),
        fields=(FieldMapping("Quantity", "QUANTITY", "decimal", Tolerance("decimal_places", places=5)), FieldMapping("Price", "PRICE", "decimal", Tolerance("decimal_places", places=4))),
        path=Path("parity.yaml"),
    )


def test_matched_rows_are_counted_and_nothing_else():
    mapping = _test_mapping()
    legacy = [{"AccountNumber": "12345678", "Cusip": "037833100", "Quantity": "100.00000", "Price": "227.1200"}]
    lakehouse = [{"ACCOUNT_NUMBER": "12345678", "CUSTODIAN_SECURITY_ID": "037833100", "QUANTITY": "100.00000", "PRICE": "227.1200"}]
    result = compare_rows(mapping, legacy, lakehouse, date(2026, 8, 3))
    assert (result.legacy_rows, result.lakehouse_rows, result.matched) == (1, 1, 1)
    assert result.missing == [] and result.extra == [] and result.mismatches == []
    assert result.match_rate == 1.0 and result.meets_target


def test_missing_extra_and_value_mismatch_by_field_are_classified_separately():
    mapping = _test_mapping()
    legacy = [
        {"AccountNumber": "12345678", "Cusip": "037833100", "Quantity": "100.00000", "Price": "227.1200"},  # missing from lakehouse
        {"AccountNumber": "87654321", "Cusip": "594918104", "Quantity": "25.00000", "Price": "410.5000"},  # both quantity and price mismatch
        {"AccountNumber": "11111111", "Cusip": "037833100", "Quantity": "10.00000", "Price": "1.0000"},  # matches
    ]
    lakehouse = [
        {"ACCOUNT_NUMBER": "87654321", "CUSTODIAN_SECURITY_ID": "594918104", "QUANTITY": "25.10000", "PRICE": "410.6000"},
        {"ACCOUNT_NUMBER": "11111111", "CUSTODIAN_SECURITY_ID": "037833100", "QUANTITY": "10.00000", "PRICE": "1.0000"},
        {"ACCOUNT_NUMBER": "99999999", "CUSTODIAN_SECURITY_ID": "037833100", "QUANTITY": "1.00000", "PRICE": "1.0000"},  # extra: not in legacy
    ]
    result = compare_rows(mapping, legacy, lakehouse, date(2026, 8, 3))
    assert result.legacy_rows == 3 and result.lakehouse_rows == 3 and result.matched == 1
    assert result.missing == [("12345678", "037833100")]
    assert result.extra == [("99999999", "037833100")]
    assert len(result.mismatches) == 1 and result.mismatches[0].key == ("87654321", "594918104")
    assert {f.field for f in result.mismatches[0].fields} == {"Quantity", "Price"}
    assert result.field_summary == {"Quantity": 1, "Price": 1}
    assert result.match_rate == pytest.approx(1 / 3) and not result.meets_target


def test_duplicate_keys_are_reported_and_the_first_row_is_compared():
    mapping = _test_mapping()
    legacy = [
        {"AccountNumber": "1", "Cusip": "A", "Quantity": "10.00000", "Price": "1.0000"},
        {"AccountNumber": "1", "Cusip": "A", "Quantity": "99.00000", "Price": "9.0000"},  # duplicate key
    ]
    lakehouse = [{"ACCOUNT_NUMBER": "1", "CUSTODIAN_SECURITY_ID": "A", "QUANTITY": "10.00000", "PRICE": "1.0000"}]
    result = compare_rows(mapping, legacy, lakehouse, date(2026, 8, 3))
    assert result.legacy_rows == 1 and result.matched == 1  # the duplicate did not create a second comparison
    assert result.duplicate_legacy_keys == [("1", "A")]


def test_an_empty_legacy_and_empty_lakehouse_is_perfect_parity_not_a_zero_rate():
    mapping = _test_mapping()
    result = compare_rows(mapping, [], [], date(2026, 8, 3))
    assert result.match_rate == 1.0 and result.meets_target


def test_a_none_value_matches_only_another_none():
    mapping = _test_mapping()
    legacy = [{"AccountNumber": "1", "Cusip": "A", "Quantity": None, "Price": "1.0000"}]
    lakehouse = [{"ACCOUNT_NUMBER": "1", "CUSTODIAN_SECURITY_ID": "A", "QUANTITY": None, "PRICE": "1.0000"}]
    assert compare_rows(mapping, legacy, lakehouse, date(2026, 8, 3)).matched == 1
    lakehouse2 = [{"ACCOUNT_NUMBER": "1", "CUSTODIAN_SECURITY_ID": "A", "QUANTITY": "0.00000", "PRICE": "1.0000"}]
    result = compare_rows(mapping, legacy, lakehouse2, date(2026, 8, 3))
    assert result.matched == 0 and result.mismatches[0].fields[0].field == "Quantity"


# -------------------------------------------------------------- loading rows


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "store")


def _put_output(store: LocalStore, custodian: str, day: date, version: int, output: str, csv_text: str) -> None:
    store.put(f"{dataset_prefix(custodian, day, version)}/outputs/{output}.csv", csv_text.encode("utf-8"))


def _write_datasets_index(golden_dir: Path, custodian: str, entries: list[dict]) -> None:
    path = golden_dir / custodian / "datasets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"custodian": custodian, "datasets": entries}), encoding="utf-8")


def test_load_legacy_rows_reads_the_latest_version_by_default(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Quantity\n1,100\n")
    _put_output(store, "pershing", date(2026, 8, 3), 2, "positions", "AccountNumber,Quantity\n1,200\n\n")
    _write_datasets_index(golden_dir, "pershing", [
        {"business_date": "2026-08-03", "version": 1, "source_files": []},
        {"business_date": "2026-08-03", "version": 2, "source_files": []},
    ])
    rows = load_legacy_rows(golden_dir, store, "pershing", date(2026, 8, 3), "positions")
    assert rows == [{"AccountNumber": "1", "Quantity": "200"}]
    rows_v1 = load_legacy_rows(golden_dir, store, "pershing", date(2026, 8, 3), "positions", version=1)
    assert rows_v1 == [{"AccountNumber": "1", "Quantity": "100"}]


def test_load_legacy_rows_treats_empty_fields_as_none(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Quantity\n1,\n")
    _write_datasets_index(golden_dir, "pershing", [{"business_date": "2026-08-03", "version": 1, "source_files": []}])
    assert load_legacy_rows(golden_dir, store, "pershing", date(2026, 8, 3), "positions") == [{"AccountNumber": "1", "Quantity": None}]


def test_load_legacy_rows_refuses_an_uncaptured_date(tmp_path, store):
    with pytest.raises(ParityError, match="no golden dataset captured for pershing 2026-08-03"):
        load_legacy_rows(tmp_path / "golden", store, "pershing", date(2026, 8, 3), "positions")


def test_load_legacy_rows_refuses_an_output_the_dataset_does_not_have(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber\n1\n")
    _write_datasets_index(golden_dir, "pershing", [{"business_date": "2026-08-03", "version": 1, "source_files": []}])
    with pytest.raises(ParityError, match="has no 'rejections' output"):
        load_legacy_rows(golden_dir, store, "pershing", date(2026, 8, 3), "rejections")


def test_lakehouse_query_selects_every_key_and_field_column_once_filtered_and_ordered():
    mapping = _mapping()
    sql = lakehouse_query(mapping, "ASTRA_DEV", date(2026, 8, 3))
    assert lakehouse_columns(mapping) == ["ACCOUNT_NUMBER", "CUSTODIAN_SECURITY_ID", "AS_OF_DATE", "QUANTITY", "PRICE", "MARKET_VALUE"]
    assert sql.startswith('SELECT "ACCOUNT_NUMBER", "CUSTODIAN_SECURITY_ID", "AS_OF_DATE", "QUANTITY", "PRICE", "MARKET_VALUE"')
    assert 'FROM "ASTRA_DEV"."SILVER"."POSITION"' in sql
    assert '''WHERE "CUSTODIAN_ID" = 'pershing' AND "AS_OF_DATE" = '2026-08-03\'''' in sql
    assert sql.rstrip().endswith('ORDER BY "ACCOUNT_NUMBER", "CUSTODIAN_SECURITY_ID", "AS_OF_DATE"')


class FakeExecutor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return self.rows

    def close(self) -> None:
        pass


def test_load_lakehouse_rows_zips_columns_and_stringifies_values():
    mapping = _mapping()
    executor = FakeExecutor([("12345678", "037833100", date(2026, 8, 3), "100.00000", "227.1200", None)])
    rows = load_lakehouse_rows(executor, mapping, "ASTRA_DEV", date(2026, 8, 3))
    assert rows == [{"ACCOUNT_NUMBER": "12345678", "CUSTODIAN_SECURITY_ID": "037833100", "AS_OF_DATE": "2026-08-03", "QUANTITY": "100.00000", "PRICE": "227.1200", "MARKET_VALUE": None}]
    assert len(executor.queries) == 1


# ------------------------------------------------------------------- run()


def test_run_wires_legacy_and_lakehouse_rows_together_and_writes_a_report(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n12345678,037833100,2026-08-03,100.00000,227.1200,22712.00\n")
    _write_datasets_index(golden_dir, "pershing", [{"business_date": "2026-08-03", "version": 1, "source_files": []}])
    executor = FakeExecutor([("12345678", "037833100", date(2026, 8, 3), "100.00000", "227.12001", "22712.00")])  # within the price tolerance (4 dp)
    result = run(_mapping(), golden_dir, store, executor, "ASTRA_DEV", date(2026, 8, 3), out=tmp_path / "out")
    assert result.legacy_rows == 1 and result.lakehouse_rows == 1 and result.matched == 1
    markdown = (tmp_path / "out" / "parity.md").read_text(encoding="utf-8")
    assert "# Parity: pershing 2026-08-03" in markdown and "Match rate:" in markdown
    data = json.loads((tmp_path / "out" / "parity.json").read_text(encoding="utf-8"))
    assert data["match_rate"] == 1.0


# ------------------------------------------------------------------- CLI


def test_cli_checks_the_committed_mappings(capsys):
    assert main(["parity", "check", str(GOLDEN), "--root", str(REPO)]) == 0
    assert "pershing: positions vs SILVER.POSITION; 3 key(s), 3 field(s)" in capsys.readouterr().out


def test_cli_runs_a_parity_comparison(tmp_path, store, monkeypatch, capsys):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n12345678,037833100,2026-08-03,100.00000,227.1200,22712.00\n")
    _write_datasets_index(golden_dir, "pershing", [{"business_date": "2026-08-03", "version": 1, "source_files": []}])
    executor = FakeExecutor([("12345678", "037833100", date(2026, 8, 3), "50.00000", "227.1200", "22712.00")])  # quantity mismatch

    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main([
        "parity", "run", "--config", str(PARITY), "--business-date", "2026-08-03", "--environment", "dev",
        "--golden", str(golden_dir), "--store", str(tmp_path / "store"), "--out", str(tmp_path / "out"), "--root", str(REPO),
    ])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "parity of pershing 2026-08-03: positions vs POSITION" in out
    assert "1 legacy row(s), 1 lakehouse row(s), 0 matched" in out
    assert "below target" in out
    assert "value mismatch 1 row(s); by field: Quantity 1" in out


def test_cli_reports_a_bad_mapping_before_touching_snowflake(tmp_path, capsys):
    bad = tmp_path / "parity.yaml"
    bad.write_text("parity_version: 1\n", encoding="utf-8")
    assert main(["parity", "run", "--config", str(bad), "--business-date", "2026-08-03", "--environment", "dev", "--store", str(tmp_path)]) == 2
    assert "problem" in capsys.readouterr().err
