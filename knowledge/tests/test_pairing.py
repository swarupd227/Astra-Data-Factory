import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from astra_knowledge.cli import main
from astra_knowledge.patterns import pair, parse
from astra_knowledge.registry import Registry

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"


def split_spec():
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == [], [p.message for p in problems]
    return registry.get("split_position_example", "2026-01-01")


# Synthetic 60-character lines.
def hdr(file_date="20260906") -> str:
    return "H" + file_date + " " * 51


def a(account="ACC0000001", cusip="123456789", qty="000000010000000", sign="+") -> str:
    return "A" + account.ljust(10) + cusip.ljust(9) + qty + sign + " " * 24


def b(account="ACC0000001", cusip="123456789", price="0000012500000", value="000000000125000", sign="+") -> str:
    return "B" + account.ljust(10) + cusip.ljust(9) + price + value + sign + " " * 11


def trl(count="000000004") -> str:
    return "T" + count + " " * 50


def test_a_and_b_with_the_same_account_and_cusip_become_one_row():
    parsed = parse(split_spec(), [hdr(), a(), b(), a(account="ACC0000002", cusip="987654321", sign="-"), b(account="ACC0000002", cusip="987654321", sign="-"), trl()])
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert parsed.counts == {"header": 1, "holding": 2, "valuation": 2, "trailer": 1, "position": 2}
    assert [r.record for r in parsed.rows] == ["position", "position"]

    first = parsed.rows[0]
    assert first.line_number == 2
    assert first.values["account_number"] == "ACC0000001" and first.values["cusip"] == "123456789"
    assert first.values["quantity"] == Decimal("100.00000") and first.values["quantity_sign"] == "+"
    assert first.values["price"] == Decimal("12.500000") and first.values["market_value"] == Decimal("1250.00")
    # Fields present in both records are prefixed with their record label; keys appear once.
    assert first.values["holding_record_type"] == "A" and first.values["valuation_record_type"] == "B"
    assert "record_type" not in first.values and list(first.values)[:2] == ["account_number", "cusip"]
    assert parsed.rows[1].values["market_value"] == Decimal("-1250.00")
    assert parsed.metadata["header"]["file_date"] == date(2026, 9, 6)


def test_records_pair_regardless_of_order_or_distance():
    parsed = parse(split_spec(), [hdr(), b(), a(account="ACC0000002", cusip="222222222"), a(), b(account="ACC0000002", cusip="222222222"), trl()])
    assert parsed.ok
    assert [(r.line_number, r.values["account_number"]) for r in parsed.rows] == [(2, "ACC0000001"), (3, "ACC0000002")]


def test_a_missing_partner_raises_pair_incomplete_with_the_row_reference():
    parsed = parse(split_spec(), [hdr(), a(), b(), a(account="ACC0000009", cusip="999999999"), b(account="ACC0000003", cusip="333333333"), trl()])
    assert not parsed.ok and not parsed.rejected
    codes = [(p.code, p.line_number, p.record) for p in parsed.problems]
    assert codes == [("PAIR_INCOMPLETE", 4, "holding"), ("PAIR_INCOMPLETE", 5, "valuation")]
    texts = [p.text() for p in parsed.problems]
    assert texts[0] == "line 4 (holding): PAIR_INCOMPLETE: no valuation record for account_number='ACC0000009', cusip='999999999'; the holding record at line 4 is incomplete"
    assert texts[1] == "line 5 (valuation): PAIR_INCOMPLETE: no holding record for account_number='ACC0000003', cusip='333333333'; the valuation record at line 5 is incomplete"
    assert [r.values["account_number"] for r in parsed.rows] == ["ACC0000001"]
    assert parsed.counts["position"] == 1


def test_duplicates_and_blank_keys_are_reported_with_their_own_codes():
    parsed = parse(split_spec(), [hdr(), a(), a(), b(), a(account="          "), trl()])
    by_code = {p.code: p for p in parsed.problems}
    assert by_code["PAIR_DUPLICATE"].line_number == 3 and "the first is at line 2" in by_code["PAIR_DUPLICATE"].message
    assert by_code["PAIR_KEY_BLANK"].line_number == 5 and by_code["PAIR_KEY_BLANK"].field == "account_number"
    assert any(p.message == "required field is blank" and p.line_number == 5 for p in parsed.problems)
    assert len(parsed.rows) == 1


def test_pairing_can_be_skipped_to_see_physical_records():
    physical = parse(split_spec(), [hdr(), a(), b(), trl()], pairing=False)
    assert [r.record for r in physical.rows] == ["holding", "valuation"]
    logical = pair(physical)
    assert [r.record for r in logical.rows] == ["position"] and [r.record for r in physical.rows] == ["holding", "valuation"]


def test_registry_validates_pairing_against_records_and_keys(tmp_path):
    folder = tmp_path / "specs" / "split_position_example"
    folder.mkdir(parents=True)
    text = (SPECS / "split_position_example" / "2026-01-01.yaml").read_text(encoding="utf-8")

    (folder / "2026-01-01.yaml").write_text(text.replace("records: [holding, valuation]", "records: [holding, pricing]"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("lists record 'pricing', which is not a record of this spec" in p.message for p in problems)

    (folder / "2026-01-01.yaml").write_text(text.replace("keys: [account_number, cusip]", "keys: [account_number, price]"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("key 'price' is not a field of record 'holding'" in p.message for p in problems)

    (folder / "2026-01-01.yaml").write_text(text.replace("records: [holding, valuation]", "records: [header, valuation]"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("a header record; only detail records are paired" in p.message for p in problems)

    (folder / "2026-01-01.yaml").write_text(text.replace("  - name: position\n", "  - name: holding\n"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("is already the label of a record" in p.message for p in problems)


def test_cli_parse_shows_logical_rows_and_pairing_problems(tmp_path, capsys):
    sample = tmp_path / "positions.dat"
    sample.write_text("\n".join([hdr(), a(), b(), a(account="ACC0000009", cusip="999999999"), trl()]) + "\n", encoding="utf-8")
    code = main(["--root", str(REPO), "--specs", str(SPECS), "--format", "json", "parse", "--id", "split_position_example", "--version", "2026-01-01", str(sample)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["counts"]["position"] == 1 and payload["rows"][0]["record"] == "position"
    assert payload["rows"][0]["values"]["market_value"] == "1250.00"
    assert payload["problems"][0]["code"] == "PAIR_INCOMPLETE" and payload["problems"][0]["line_number"] == 4
