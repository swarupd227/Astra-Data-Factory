"""Split records and the cancel/correct lifecycle (S2.2.6)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import json

import pytest
import yaml

from astra_knowledge.cli import main
from astra_knowledge.patterns import LifecycleState, ParsedRow, apply_lifecycle, parse, split
from astra_knowledge.registry import Registry, SourceSpec

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"


def load(spec_id: str) -> SourceSpec:
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    spec = registry.get(spec_id, "2026-01-01")
    assert spec is not None
    return spec


@pytest.fixture(scope="module")
def spec() -> SourceSpec:
    return load("drip_transaction_example")


def hdr(date: str = "20260302") -> str:
    return f"H{date}".ljust(100)


def trl(count: int) -> str:
    return f"T{count:09d}".ljust(100)


def dtl(
    txn: str,
    action: str = "N",
    original: str = "",
    kind: str = "BUY",
    quantity: str = "0000000050000",
    amount: str = "0000000012500",
    sign: str = "+",
    account: str = "ACC0000001",
    cusip: str = "037833100",
    trade_date: str = "20260302",
) -> str:
    return f"D{txn:<12}{action}{original:<12}{account:<10}{cusip:<9}{kind:<4}{trade_date}{quantity}{amount}{sign}".ljust(100)


def parse_lines(spec: SourceSpec, *details: str):
    return parse(spec, [hdr(), *details, trl(len(details))])


# ---------------------------------------------------------------- split


def test_drip_line_yields_a_dividend_and_a_purchase(spec):
    parsed = parse_lines(spec, dtl("T1", kind="DRIP", quantity="0000000052000", amount="0000000012500"))
    assert parsed.ok, [p.text() for p in parsed.problems]
    assert [r.record for r in parsed.rows] == ["dividend", "purchase"]

    dividend, purchase = parsed.rows
    assert dividend.values["transaction_type"] == "DIV"
    assert dividend.values["quantity"] is None
    assert dividend.values["amount"] == Decimal("125.00")

    assert purchase.values["transaction_type"] == "BUY"
    assert purchase.values["quantity"] == Decimal("5.2000")
    assert purchase.values["amount"] == Decimal("-125.00")

    # Both parts carry the same identity and account; the split does not invent keys.
    for row in parsed.rows:
        assert row.values["transaction_id"] == "T1"
        assert row.values["account_number"] == "ACC0000001"


def test_split_parts_are_traceable_to_the_source_line(spec):
    parsed = parse_lines(spec, dtl("T1", kind="DRIP"))
    dividend, purchase = parsed.rows
    assert (dividend.origin, dividend.split, dividend.part, dividend.line_number) == ("detail", "drip", 0, 2)
    assert (purchase.origin, purchase.split, purchase.part, purchase.line_number) == ("detail", "drip", 1, 2)
    assert parsed.counts["detail"] == 1
    assert parsed.counts["dividend"] == 1
    assert parsed.counts["purchase"] == 1


def test_rows_no_rule_matches_pass_through_unchanged(spec):
    parsed = parse_lines(spec, dtl("T1", kind="BUY"), dtl("T2", kind="DRIP"), dtl("T3", kind="SELL", sign="-"))
    assert parsed.ok
    assert [r.record for r in parsed.rows] == ["detail", "dividend", "purchase", "detail"]
    buy, _, _, sell = parsed.rows
    assert buy.origin is None and buy.split is None and buy.part is None
    assert buy.values["amount"] == Decimal("125.00")
    assert sell.values["amount"] == Decimal("-125.00")


def test_parse_can_leave_splitting_to_the_caller(spec):
    lines = [hdr(), dtl("T1", kind="DRIP"), trl(1)]
    unsplit = parse(spec, lines, splitting=False)
    assert [r.record for r in unsplit.rows] == ["detail"]
    assert [r.record for r in split(unsplit).rows] == ["dividend", "purchase"]


def test_a_bad_amount_is_reported_once_and_not_negated(spec):
    lines = [hdr(), dtl("T1", kind="DRIP", amount="00000000ABCDE"), trl(1)]
    parsed = parse(spec, lines)
    assert [p.text() for p in parsed.problems] == ["line 2 (detail.amount): FIELD_NOT_NUMERIC: '00000000ABCDE' is not all digits"]
    dividend, purchase = parsed.rows
    assert dividend.values["amount"] is None and purchase.values["amount"] is None


def test_negating_a_non_number_is_a_problem(spec):
    # A value that parsed as text (a renderer bug, or a rule aimed at a string field) cannot be negated.
    unsplit = parse(spec, [hdr(), dtl("T1", kind="DRIP"), trl(1)], splitting=False)
    row = unsplit.rows[0]
    unsplit.rows[0] = ParsedRow(row.record, row.line_number, {**row.values, "amount": "125.00"})
    parsed = split(unsplit)
    assert [p.text() for p in parsed.problems] == ["line 2 (detail.amount): SPLIT_NOT_NUMERIC: split 'drip' part 'purchase' negates 'amount', which is not a number"]
    assert parsed.rows[1].values["amount"] == "125.00"


# ------------------------------------------------------------ lifecycle


def test_new_records_become_active(spec):
    state = LifecycleState()
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T1"), dtl("T2")), "txn_20260302.dat")
    assert (result.new, result.cancelled, result.corrected, result.problems) == (2, 0, 0, [])
    assert sorted(r.identity for r in state.active()) == [("T1",), ("T2",)]
    record = state.records[("T1",)]
    assert (record.file_name, record.line_number, record.status) == ("txn_20260302.dat", 2, "active")


def test_cancel_marks_the_original_cancelled_and_both_are_traceable(spec):
    state = LifecycleState()
    apply_lifecycle(state, spec, parse_lines(spec, dtl("T1"), dtl("T2")), "day1.dat")
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T9", action="X", original="T1")), "day2.dat")
    assert (result.new, result.cancelled, result.corrected, result.problems) == (0, 1, 0, [])

    original = state.records[("T1",)]
    cancel = state.records[("T9",)]
    assert original.status == "cancelled"
    assert original.cancelled_by == ("T9",)
    assert cancel.status == "cancel"
    assert cancel.cancels == ("T1",)
    assert (cancel.file_name, cancel.line_number) == ("day2.dat", 2)
    # A cancel is never an active transaction; the untouched record still is.
    assert [r.identity for r in state.active()] == [("T2",)]


def test_correction_supersedes_the_original_and_becomes_active(spec):
    state = LifecycleState()
    apply_lifecycle(state, spec, parse_lines(spec, dtl("T1", amount="0000000010000")))
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T1C", action="C", original="T1", amount="0000000011000")))
    assert (result.new, result.cancelled, result.corrected, result.problems) == (0, 0, 1, [])

    original = state.records[("T1",)]
    correction = state.records[("T1C",)]
    assert original.status == "superseded"
    assert original.superseded_by == ("T1C",)
    assert correction.status == "active"
    assert correction.corrects == ("T1",)
    assert correction.values["amount"] == Decimal("110.00")
    assert [r.identity for r in state.active()] == [("T1C",)]


def test_cancel_of_a_drip_line_closes_both_parts(spec):
    state = LifecycleState()
    apply_lifecycle(state, spec, parse_lines(spec, dtl("T1", kind="DRIP")))
    assert sorted(state.records) == [("T1", "dividend"), ("T1", "purchase")]

    # The cancel is itself a DRIP line, so it arrives as two parts too.
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T9", action="X", original="T1", kind="DRIP")))
    assert result.problems == []
    assert result.cancelled == 2
    assert state.records[("T1", "dividend")].status == "cancelled"
    assert state.records[("T1", "dividend")].cancelled_by == ("T9", "dividend")
    assert state.records[("T1", "purchase")].status == "cancelled"
    assert state.records[("T1", "purchase")].cancelled_by == ("T9", "purchase")
    assert state.records[("T9", "dividend")].cancels == ("T1",)
    assert state.records[("T9", "purchase")].cancels == ("T1",)
    assert state.active() == []


def test_cancel_of_a_missing_original_is_a_problem(spec):
    state = LifecycleState()
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T9", action="X", original="T404")))
    assert result.cancelled == 0
    assert [p.code for p in result.problems] == ["LIFECYCLE_ORIGINAL_MISSING"]
    assert "T404" in result.problems[0].text()
    assert state.records == {}


def test_cancel_without_a_reference_is_a_problem(spec):
    state = LifecycleState()
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T9", action="X")))
    assert [p.code for p in result.problems] == ["LIFECYCLE_ORIGINAL_MISSING"]
    assert "original_transaction_id blank" in result.problems[0].text()


def test_a_second_cancel_of_the_same_original_is_a_problem(spec):
    state = LifecycleState()
    apply_lifecycle(state, spec, parse_lines(spec, dtl("T1")))
    apply_lifecycle(state, spec, parse_lifecycle_lines(spec, "T8", "X", "T1"))
    result = apply_lifecycle(state, spec, parse_lifecycle_lines(spec, "T9", "C", "T1"))
    assert [p.code for p in result.problems] == ["LIFECYCLE_ALREADY_CLOSED"]
    assert "already cancelled" in result.problems[0].text()
    assert ("T9",) not in state.records


def parse_lifecycle_lines(spec: SourceSpec, txn: str, action: str, original: str):
    return parse_lines(spec, dtl(txn, action=action, original=original))


def test_a_second_new_record_with_the_same_identity_is_a_problem(spec):
    state = LifecycleState()
    apply_lifecycle(state, spec, parse_lines(spec, dtl("T1")), "day1.dat")
    result = apply_lifecycle(state, spec, parse_lines(spec, dtl("T1")), "day2.dat")
    assert result.new == 0
    assert [p.code for p in result.problems] == ["LIFECYCLE_DUPLICATE"]
    assert "line 2 of day1.dat" in result.problems[0].text()


def test_blank_identity_is_a_problem(spec):
    state = LifecycleState()
    parsed = parse_lines(spec, dtl(""))
    result = apply_lifecycle(state, spec, parsed)
    assert [p.code for p in result.problems] == ["LIFECYCLE_IDENTITY_BLANK"]
    assert state.records == {}


def test_lifecycle_needs_a_lifecycle_block():
    plain = load("pershing_gcus")
    result = apply_lifecycle(LifecycleState(), plain, parse(plain, []))
    assert [p.level for p in result.problems] == ["file"]
    assert "declares no lifecycle block" in result.problems[0].text()


def test_unknown_action_codes_are_treated_as_new(spec):
    assert spec.lifecycle is not None
    assert spec.lifecycle.action_for("N") == "new"
    assert spec.lifecycle.action_for("X") == "cancel"
    assert spec.lifecycle.action_for("C") == "correct"
    assert spec.lifecycle.action_for(None) == "new"
    assert spec.lifecycle.action_for("?") == "new"


# ------------------------------------------------------------- registry


def test_registry_reads_split_and_lifecycle(spec):
    assert [r.name for r in spec.splits] == ["drip"]
    rule = spec.splits[0]
    assert (rule.record, rule.field, rule.values) == ("detail", "transaction_type", ("DRIP",))
    assert [p.name for p in rule.parts] == ["dividend", "purchase"]
    assert rule.parts[0].set == {"transaction_type": "DIV", "quantity": None}
    assert rule.parts[1].negate == ("amount",)
    assert rule.applies("DRIP") and rule.applies(" DRIP ") and not rule.applies("BUY") and not rule.applies(None)

    assert spec.lifecycle.record == "detail"
    assert spec.lifecycle.identity == ("transaction_id",)
    assert spec.lifecycle.reference == ("original_transaction_id",)
    assert spec.logical_fields("dividend") == spec.logical_fields("detail")


def _load(tmp_path: Path, mutate):
    data = yaml.safe_load((SPECS / "drip_transaction_example" / "2026-01-01.yaml").read_text(encoding="utf-8"))
    mutate(data)
    target = tmp_path / "specs" / "drip_transaction_example"
    target.mkdir(parents=True)
    (target / "2026-01-01.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return Registry.load(tmp_path / "specs", tmp_path)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["split"][0].update(record="nowhere"), "split[0].record 'nowhere' is not a logical record"),
        (lambda d: d["split"][0]["when"].update(field="nowhere"), "split[0].when.field 'nowhere' is not a field of 'detail'"),
        (lambda d: d["split"][0]["into"][0]["set"].update(nowhere=1), "split[0].into[0] refers to 'nowhere', which is not a field"),
        (lambda d: d["split"][0]["into"][1].update(negate=["nowhere"]), "split[0].into[1] refers to 'nowhere', which is not a field"),
        (lambda d: d["split"][0]["into"][1].update(name="dividend"), "'dividend' is already the label of a record or another split part"),
        (lambda d: d["split"][0]["into"][1].update(name="detail"), "'detail' is already the label of a record or another split part"),
        (lambda d: d["lifecycle"].update(record="nowhere"), "lifecycle.record 'nowhere' is not a logical record"),
        (lambda d: d["lifecycle"].update(action_field="nowhere"), "lifecycle.action_field 'nowhere' is not a field of 'detail'"),
        (lambda d: d["lifecycle"].update(identity=["nowhere"]), "lifecycle.identity 'nowhere' is not a field of 'detail'"),
        (lambda d: d["lifecycle"].update(reference=["nowhere"]), "lifecycle.reference 'nowhere' is not a field of 'detail'"),
        (lambda d: d["lifecycle"].update(reference=["original_transaction_id", "cusip"]), "one field for each identity field"),
        (lambda d: d["lifecycle"].update(actions={"N": "new"}), "at least one code to cancel or correct"),
    ],
)
def test_registry_rejects_dangling_split_and_lifecycle_references(tmp_path, mutate, expected):
    _, problems = _load(tmp_path, mutate)
    assert any(expected in p.message for p in problems), [p.message for p in problems]


def test_lifecycle_may_name_a_split_part(tmp_path):
    registry, problems = _load(tmp_path, lambda d: d["lifecycle"].update(record="purchase"))
    assert problems == []
    assert registry.get("drip_transaction_example", "2026-01-01").lifecycle.record == "purchase"


# ------------------------------------------------------------------ CLI


def test_cli_parse_shows_split_parts_with_their_origin(tmp_path, capsys):
    sample = tmp_path / "transactions.dat"
    sample.write_text("\n".join([hdr(), dtl("T1", kind="DRIP"), dtl("T2", kind="SELL", sign="-"), trl(2)]) + "\n", encoding="utf-8")
    args = ["--root", str(REPO), "--specs", str(SPECS), "parse", "--id", "drip_transaction_example", "--version", "2026-01-01", str(sample)]

    assert main(["--format", "json", *args]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"] == {"header": 1, "detail": 2, "trailer": 1, "dividend": 1, "purchase": 1}
    dividend, purchase, sell = payload["rows"]
    assert dividend["record"] == "dividend" and dividend["origin"] == {"record": "detail", "split": "drip", "part": 0}
    assert purchase["record"] == "purchase" and purchase["origin"] == {"record": "detail", "split": "drip", "part": 1}
    assert (dividend["line"], purchase["line"], sell["line"]) == (2, 2, 3)
    assert purchase["values"]["amount"] == "-125.00" and dividend["values"]["quantity"] is None
    assert "origin" not in sell

    assert main(args) == 0
    text = capsys.readouterr().out
    assert "line 2 dividend (from detail, split drip part 1):" in text
    assert "line 2 purchase (from detail, split drip part 2):" in text
    assert "line 3 detail:" in text
