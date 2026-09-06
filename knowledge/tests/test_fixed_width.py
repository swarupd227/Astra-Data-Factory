import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from astra_knowledge.cli import main
from astra_knowledge.patterns import FIXED_WIDTH_MULTI_RECORD, parse, patterns_for
from astra_knowledge.patterns.fixed_width import parse_fixed_width, record_type_of
from astra_knowledge.registry import Registry

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"


def gcus(version: str = "2017-07-25"):
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    return registry.get("pershing_gcus", version)


# Synthetic GCUS lines, 120 characters, no real data.
def hdr(file_date="20260905", remote="RMT0000001", flag="R") -> str:
    return "HDR" + file_date + remote.ljust(10) + flag + " " * 98


def dtl(account="ACC0000001", cusip="123456789", qty="000000000012345678", sign="+", price="000000012345600", asof="20260905", sectype="EQ") -> str:
    return "DTL" + account.ljust(10) + cusip.ljust(9) + qty + sign + price + asof + sectype + " " * 54


def trl(count="000000002") -> str:
    return "TRL" + count + " " * 108


SAMPLE = [hdr(), dtl(), dtl(account="ACC0000002", cusip="987654321", sign="-", sectype="FI"), trl()]


def test_the_gcus_spec_gives_one_typed_row_per_detail_record():
    parsed = parse_fixed_width(gcus(), SAMPLE)
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert parsed.lines == 4 and parsed.counts == {"header": 1, "detail": 2, "trailer": 1}
    assert [r.record for r in parsed.rows] == ["detail", "detail"]

    first = parsed.rows[0].values
    assert first["record_type"] == "DTL"
    assert first["account_number"] == "ACC0000001" and first["cusip"] == "123456789"
    assert first["quantity"] == Decimal("123.45678") and first["quantity_sign"] == "+"
    assert first["price"] == Decimal("12.345600")
    assert first["as_of_date"] == date(2026, 9, 5)
    assert first["security_type"] == "EQ" and first["filler"] is None
    assert parsed.rows[1].values["quantity"] == Decimal("-123.45678")
    assert parsed.rows[1].line_number == 3


def test_header_and_trailer_become_file_metadata():
    parsed = parse_fixed_width(gcus(), SAMPLE)
    assert parsed.metadata["header"]["file_date"] == date(2026, 9, 5)
    assert parsed.metadata["header"]["remote_id"] == "RMT0000001"
    assert parsed.metadata["header"]["refresh_flag"] == "R"
    assert parsed.metadata["trailer"]["detail_count"] == 2
    assert "header" not in [r.record for r in parsed.rows]


def test_record_type_is_read_from_the_configured_position():
    spec = gcus()
    assert record_type_of(spec, hdr()).label == "header"
    assert record_type_of(spec, dtl()).label == "detail"
    assert record_type_of(spec, trl()).label == "trailer"
    assert record_type_of(spec, "XXX" + " " * 117) is None
    assert spec.record("detail").match.text() == "'DTL' at 1-3"


def test_record_type_can_be_read_up_to_an_end_marker(tmp_path):
    folder = tmp_path / "specs" / "marked"
    folder.mkdir(parents=True)
    (folder / "v1.yaml").write_text(
        """spec_version: 0
spec: { id: marked, version: v1, effective_from: 2026-01-01, file_type: position, custodians: [acme] }
document: { title: Marked layout, reference: marked.pdf }
file: { format: fixed_width, record_length: 20 }
records:
  - type: header
    match: { start: 1, end_marker: "*", value: H }
    fields:
      - { name: record_type, position: { start: 1, length: 2 }, picture: X(2), citation: { page: 1 } }
      - { name: file_date, position: { start: 3, length: 8 }, picture: 9(8), type: date, format: YYYYMMDD, citation: { page: 1 } }
      - { name: filler, position: { start: 11, length: 10 }, picture: X(10), citation: { page: 1 } }
  - type: detail
    match: { start: 1, end_marker: "*", value: DT }
    fields:
      - { name: record_type, position: { start: 1, length: 3 }, picture: X(3), citation: { page: 2 } }
      - { name: amount, position: { start: 4, length: 7 }, picture: 9(5)V9(2), citation: { page: 2 } }
      - { name: filler, position: { start: 11, length: 10 }, picture: X(10), citation: { page: 2 } }
""",
        encoding="utf-8",
    )
    registry, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert problems == []
    spec = registry.get("marked", "v1")
    assert spec.record("detail").match.text() == "'DT' from 1 up to '*'"

    parsed = parse(spec, ["H*20260905", "DT*0001234", "DT*0009999", "HH*bad"])
    assert parsed.metadata["header"]["file_date"] == date(2026, 9, 5)
    assert [r.values["amount"] for r in parsed.rows] == [Decimal("12.34"), Decimal("99.99")]
    assert [p.text() for p in parsed.problems] == ["line 4: no record type matches this line"]


def test_problems_are_reported_per_line_and_field_without_stopping():
    spec = gcus()
    lines = [
        hdr(),
        dtl(account="          "),                 # required field blank
        dtl(sign=" "),                              # unknown sign
        dtl(sectype="ZZ"),                          # undeclared code
        dtl(asof="20261399"),                       # bad date
        "QQQ" + " " * 117,                          # unknown record type
        hdr(file_date="20260906"),                  # second header
        dtl() + "EXTRA",                            # too long
        trl(),
    ]
    parsed = parse_fixed_width(spec, lines)
    texts = [p.text() for p in parsed.problems]
    assert "line 2 (detail.account_number): required field is blank" in texts
    assert "line 3 (detail.quantity): sign is blank; the value is unknown, not zero" in texts
    assert any(t.startswith("line 4 (detail.security_type): 'ZZ' is not a declared code") for t in texts)
    assert "line 5 (detail.as_of_date): '20261399' is not a date in the format YYYYMMDD" in texts
    assert "line 6: no record type matches this line" in texts
    assert "line 7 (header): a second header record; a file has at most one" in texts
    assert "line 8: line is 125 characters, longer than the record length 120" in texts
    assert len(parsed.rows) == 5 and parsed.rows[1].values["quantity"] is None
    assert parsed.rows[2].values["security_type"] == "ZZ"


def test_short_lines_are_padded_and_blank_lines_skipped():
    parsed = parse_fixed_width(gcus(), [hdr().rstrip(), "", dtl().rstrip(), "\n", trl("000000001").rstrip() + "\r\n"])
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert parsed.lines == 3 and len(parsed.rows) == 1


def test_missing_header_or_trailer_is_a_problem():
    parsed = parse_fixed_width(gcus(), [dtl()])
    assert [p.text() for p in parsed.problems] == ["line 0: the file has no header record", "line 0: the file has no trailer record"]


def test_the_2026_version_parses_the_lot_id():
    line = "DTL" + "ACC0000001" + "123456789" + "000000000012345678" + "+" + "000000012345600" + "20260905" + "EQ" + "LOT000000042" + " " * 42
    parsed = parse_fixed_width(gcus("2026-01-01"), [hdr(), line, trl("000000001")])
    assert parsed.ok and parsed.rows[0].values["lot_id"] == "LOT000000042"


def test_pattern_library_dispatch():
    spec = gcus()
    assert [p.id for p in patterns_for(spec)] == ["fixed_width_multi_record"]
    assert FIXED_WIDTH_MULTI_RECORD.applies_to(spec)
    assert parse(spec, SAMPLE).counts["detail"] == 2


def test_delimited_specs_are_not_handled_yet(tmp_path):
    folder = tmp_path / "specs" / "csvish"
    folder.mkdir(parents=True)
    (folder / "v1.yaml").write_text(
        """spec_version: 0
spec: { id: csvish, version: v1, effective_from: 2026-01-01, file_type: price, custodians: [acme] }
document: { title: CSV layout, reference: csv.pdf }
file: { format: delimited, delimiter: "," }
records:
  - type: detail
    fields:
      - { name: cusip, column: 1, type: string, citation: { page: 1 } }
""",
        encoding="utf-8",
    )
    registry, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert problems == []
    spec = registry.get("csvish", "v1")
    assert patterns_for(spec) == []
    with pytest.raises(ValueError):
        parse(spec, [])


def test_match_rules_are_validated_against_the_file_shape(tmp_path):
    folder = tmp_path / "specs" / "pershing_gcus"
    folder.mkdir(parents=True)
    text = (SPECS / "pershing_gcus" / "2017-07-25.yaml").read_text(encoding="utf-8")
    (folder / "2017-07-25.yaml").write_text(text.replace("match: { position: { start: 1, length: 3 }, value: DTL }", "match: { position: { start: 1, length: 3 }, value: DETAIL }"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("match value 'DETAIL' has 6 characters but the position is 3 long" in p.message for p in problems)

    (folder / "2017-07-25.yaml").write_text(text.replace("    match: { position: { start: 1, length: 3 }, value: TRL }\n", ""), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("records[2] needs a match rule so that its lines can be told apart" in p.message for p in problems)


def test_cli_parse_prints_rows_metadata_and_problems(tmp_path, capsys):
    sample = tmp_path / "sample.dat"
    sample.write_text("\n".join([hdr(), dtl(), dtl(sign=" "), trl()]) + "\n", encoding="utf-8")
    code = main(["--root", str(REPO), "--specs", str(SPECS), "parse", "--id", "pershing_gcus", "--version", "2017-07-25", str(sample)])
    out = capsys.readouterr().out
    assert code == 1
    assert "pershing_gcus 2017-07-25 parsed with fixed_width_multi_record: 4 line(s), 1 header, 2 detail, 1 trailer" in out
    import re

    assert re.search(r"file_date\s+2026-09-05", out) and re.search(r"detail_count\s+2\b", out)
    assert "line 2 detail: account_number=ACC0000001, cusip=123456789, quantity=123.45678" in out
    assert "line 3 (detail.quantity): sign is blank; the value is unknown, not zero" in out

    code = main(["--root", str(REPO), "--specs", str(SPECS), "--format", "json", "parse", "--id", "pershing_gcus", "--version", "2017-07-25", str(sample)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["pattern"] == "fixed_width_multi_record" and payload["counts"]["detail"] == 2
    assert payload["rows"][0]["values"]["price"] == "12.345600" and payload["rows"][0]["values"]["as_of_date"] == "2026-09-05"
    assert payload["problems"][0]["field"] == "quantity"
