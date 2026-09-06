import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from astra_knowledge.cli import main
from astra_knowledge.patterns import parse, patterns_for
from astra_knowledge.patterns.delimited import parse_delimited
from astra_knowledge.registry import Registry

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"

CSV = [
    "Price Date,CUSIP,Security Name,Close Price,Currency,Source\n",
    '2026-09-05,123456789,"Acme Holdings, Inc.",123.45,USD,EXCH\n',
    '2026-09-05,987654321,"Widget ""Classic"" Corp",-0.75,CAD,EVAL\n',
    "2026-09-05,555555555,Plain Name,1000,GBP,EXCH\r\n",
    "\n",
]


def price_spec():
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    return registry.get("csv_price_example", "2026-01-01")


def variant(tmp_path: Path, transform) -> object:
    folder = tmp_path / "specs" / "csv_price_example"
    folder.mkdir(parents=True)
    text = (SPECS / "csv_price_example" / "2026-01-01.yaml").read_text(encoding="utf-8")
    (folder / "2026-01-01.yaml").write_text(transform(text), encoding="utf-8")
    registry, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert problems == [], [p.message for p in problems]
    return registry.get("csv_price_example", "2026-01-01")


def test_csv_with_quoted_fields_parses_into_typed_rows():
    parsed = parse_delimited(price_spec(), CSV)
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert parsed.lines == 4 and parsed.counts == {"detail": 3}
    rows = [r.values for r in parsed.rows]
    assert rows[0] == {"price_date": date(2026, 9, 5), "cusip": "123456789", "security_name": "Acme Holdings, Inc.", "close_price": Decimal("123.45"), "currency": "USD", "price_source": "EXCH"}
    assert rows[1]["security_name"] == 'Widget "Classic" Corp' and rows[1]["close_price"] == Decimal("-0.75")
    assert rows[2]["close_price"] == Decimal("1000") and parsed.rows[2].line_number == 4


def test_pipe_delimited_with_backslash_escapes(tmp_path):
    spec = variant(tmp_path, lambda t: t.replace('delimiter: ","', 'delimiter: "|"').replace("  quote: '\"'\n", "  quote: '\"'\n  escape: \"\\\\\"\n"))
    lines = [
        "Price Date|CUSIP|Security Name|Close Price|Currency|Source\n",
        '2026-09-05|123456789|"Pipe \\"Quoted\\" Co|Ltd"|12.5|USD|EXCH\n',
    ]
    parsed = parse_delimited(spec, lines)
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert parsed.rows[0].values["security_name"] == 'Pipe "Quoted" Co|Ltd'


def test_column_count_mismatch_rejects_the_file_but_keeps_readable_rows():
    lines = CSV[:2] + ["2026-09-05,111111111,Short Row,5\n", "2026-09-05,222222222,Long Row,6,USD,EXCH,extra\n"] + CSV[2:3]
    parsed = parse_delimited(price_spec(), lines)
    assert parsed.rejected and not parsed.ok
    (problem,) = parsed.file_problems
    assert problem.level == "file" and problem.line_number == 3
    assert problem.message == "2 line(s) do not have the expected 6 columns: line 3 has 4, line 4 has 7"
    assert [r.values["cusip"] for r in parsed.rows] == ["123456789", "987654321"]


def test_header_row_is_checked_against_labels():
    lines = ["Date,CUSIP,Name,Close Price,Currency,Source\n"] + CSV[1:2]
    parsed = parse_delimited(price_spec(), lines)
    assert parsed.rejected
    assert parsed.file_problems[0].message == "header row does not match the spec: column 1 is 'Date', expected 'Price Date'; column 3 is 'Name', expected 'Security Name'"
    assert len(parsed.rows) == 1


def test_field_problems_are_per_row_and_do_not_reject_the_file():
    lines = CSV[:1] + ["2026-13-40,123456789,Bad Date,abc,EUR,EXCH\n", ",987654321,No Date,1.0,USD,EXCH\n"]
    parsed = parse_delimited(price_spec(), lines)
    assert not parsed.rejected
    texts = [p.text() for p in parsed.problems]
    assert "line 2 (detail.price_date): '2026-13-40' is not a date in the format YYYY-MM-DD" in texts
    assert "line 2 (detail.close_price): 'abc' is not a number" in texts
    assert any(t.startswith("line 2 (detail.currency): 'EUR' is not a declared code") for t in texts)
    assert "line 3 (detail.price_date): required field is blank" in texts


def test_record_types_are_matched_by_column(tmp_path):
    folder = tmp_path / "specs" / "typed_csv"
    folder.mkdir(parents=True)
    (folder / "v1.yaml").write_text(
        """spec_version: 0
spec: { id: typed_csv, version: v1, effective_from: 2026-01-01, file_type: transaction, custodians: [acme] }
document: { title: Typed CSV, reference: typed.pdf }
file: { format: delimited, delimiter: "," }
records:
  - type: header
    match: { column: 1, value: H }
    fields:
      - { name: record_type, column: 1, type: code, citation: { page: 1 } }
      - { name: file_date, column: 2, type: date, format: YYYYMMDD, citation: { page: 1 } }
  - type: detail
    match: { column: 1, value: D }
    fields:
      - { name: record_type, column: 1, type: code, citation: { page: 2 } }
      - { name: account, column: 2, type: string, citation: { page: 2 } }
      - { name: amount, column: 3, type: decimal, citation: { page: 2 } }
  - type: trailer
    match: { column: 1, value: T }
    fields:
      - { name: record_type, column: 1, type: code, citation: { page: 3 } }
      - { name: count, column: 2, type: integer, citation: { page: 3 } }
""",
        encoding="utf-8",
    )
    registry, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert problems == []
    spec = registry.get("typed_csv", "v1")
    parsed = parse(spec, ["H,20260905", "D,ACC1,10.50", "D,ACC2,-3", "X,?,?", "T,2"])
    assert parsed.metadata["header"]["file_date"] == date(2026, 9, 5) and parsed.metadata["trailer"]["count"] == 2
    assert [r.values["amount"] for r in parsed.rows] == [Decimal("10.50"), Decimal("-3")]
    assert [p.text() for p in parsed.problems] == ["line 4: no record type matches this line"]
    assert not parsed.rejected


def test_without_a_declared_column_count_only_short_rows_are_mismatches(tmp_path):
    spec = variant(tmp_path, lambda t: t.replace("  column_count: 6\n", ""))
    parsed = parse_delimited(spec, CSV[:2] + ["2026-09-05,1,Short,1\n", "2026-09-05,2,Long,2,USD,EXCH,extra,more\n"])
    assert parsed.file_problems[0].message == "1 line(s) have fewer columns than their record type needs: line 3 has 4 (needs 6)"
    assert len(parsed.rows) == 2


def test_malformed_quoting_is_a_file_problem():
    parsed = parse_delimited(price_spec(), CSV[:1] + ['2026-09-05,1,"unterminated,1,USD,EXCH\n', "2026-09-05,2,Next,1,USD,EXCH\n"])
    assert parsed.rejected and parsed.file_problems[0].message.startswith("malformed quoting:")


def test_registry_checks_columns_against_the_column_count(tmp_path):
    folder = tmp_path / "specs" / "csv_price_example"
    folder.mkdir(parents=True)
    text = (SPECS / "csv_price_example" / "2026-01-01.yaml").read_text(encoding="utf-8")
    (folder / "2026-01-01.yaml").write_text(text.replace("column: 6\n", "column: 7\n"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("'price_source' is in column 7, beyond the column count 6" in p.message for p in problems)

    (folder / "2026-01-01.yaml").write_text(text.replace("header_rows: 1", "header_rows: 0"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("has a label but file.header_rows is 0" in p.message for p in problems)


def test_pattern_dispatch_and_cli(tmp_path, capsys):
    assert [p.id for p in patterns_for(price_spec())] == ["delimited_file"]
    sample = tmp_path / "prices.csv"
    sample.write_text("".join(CSV), encoding="utf-8")
    code = main(["--root", str(REPO), "--specs", str(SPECS), "--format", "json", "parse", "--id", "csv_price_example", "--version", "2026-01-01", str(sample)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["pattern"] == "delimited_file" and payload["counts"] == {"detail": 3}
    assert payload["rows"][0]["values"]["security_name"] == "Acme Holdings, Inc." and payload["rows"][1]["values"]["close_price"] == "-0.75"
