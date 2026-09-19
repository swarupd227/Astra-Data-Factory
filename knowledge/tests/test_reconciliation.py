"""Reconciliation: the position identity, the cash identity and the tolerance per field type (S7.1.5)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from astra_knowledge.cdm import DomainPack, load_pack
from astra_knowledge.patterns.reconciliation import (
    CATEGORIES,
    CashRow,
    PositionRow,
    TransactionRow,
    reconcile,
    reconcile_cash,
    reconcile_positions,
)
from astra_knowledge.reconciliation import CHECK_CODES, Tolerance

REPO = Path(__file__).resolve().parents[2]
CUSTODIAL = REPO / "domains" / "custodial"
D = Decimal
PRIOR, DAY, NEXT = date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)
C = "pershing"


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == [], [p.format() for p in problems]
    return pack


@pytest.fixture(scope="module")
def config(pack):
    return pack.reconciliation


def pos(account="A1", security="S1", quantity="100", on=DAY, custodian=C) -> PositionRow:
    return PositionRow(custodian, account, security, on, D(quantity))


def txn(kind="BUY", quantity="50", security="S1", account="A1", trade=DAY, settle=None, net="-1000", currency="USD", status="ACTIVE", custodian=C, tid="T1") -> TransactionRow:
    return TransactionRow(custodian, tid, account, security, kind, trade, settle, None if quantity is None else D(quantity), D(net), currency, status)


def cash(amount="1000", on=DAY, account="A1", currency="USD", balance_type="SETTLED", custodian=C) -> CashRow:
    return CashRow(custodian, account, currency, balance_type, on, D(amount))


def positions(config, snapshot, transactions, custodian=C):
    return reconcile_positions(config, snapshot, transactions, custodian, DAY)


# ---------------------------------------------------------------- the pack's file


def test_the_pack_carries_its_reconciliation_and_its_codes(pack, config):
    assert config.movement_date == "trade_date" and [c.balance_type for c in config.cash] == ["SETTLED", "TRADE_DATE"]
    assert set(CHECK_CODES.values()) <= {c.code for c in pack.rejections.codes}
    assert config.tolerances == {"quantity": Tolerance("exact"), "amount": Tolerance("exact")}


def test_every_transaction_type_of_the_model_has_a_movement(pack, config):
    types = [c.value for c in pack.latest.entity("Transaction").column("TRANSACTION_TYPE").codes]
    assert sorted(config.movements) == sorted(types)
    assert {t for t, m in config.movements.items() if m == 1} == {"BUY", "TRANSFER_IN"} and {t for t, m in config.movements.items() if m == -1} == {"SELL", "TRANSFER_OUT"}
    assert {t for t, m in config.movements.items() if m == "unreconcilable"} == {"CORPORATE_ACTION", "ADJUSTMENT", "OTHER"}


def _load_with(tmp_path: Path, old: str, new: str):
    domains = tmp_path / "domains"
    shutil.copytree(CUSTODIAL, domains / "custodial")
    path = domains / "custodial" / "reconciliation.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return load_pack(domains / "custodial", tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("    OTHER: unreconcilable\n", "", "position_identity.movements must name every transaction type; missing OTHER"),
        ("    OTHER: unreconcilable\n", "    OTHER: unreconcilable\n    SWAP: 1\n", "transaction type 'SWAP' is not a code of Transaction.TRANSACTION_TYPE"),
        ("    BUY: 1\n", "    BUY: 2\n", "reconciliation.yaml"),
        ("balance_type: SETTLED", "balance_type: ESCROW", "balance type 'ESCROW' is not a code of Cash Balance.BALANCE_TYPE"),
        ("  - { balance_type: TRADE_DATE, movement_date: trade_date }", "  - { balance_type: SETTLED, movement_date: trade_date }", "balance type SETTLED is checked twice"),
        ("quantity: { kind: exact }", "quantity: { kind: absolute }", "reconciliation.yaml"),
        ("quantity: { kind: exact }", "quantity: { kind: decimal_places, places: -1 }", "reconciliation.yaml"),
        ("quantity: { kind: exact }", "quantity: { kind: fuzzy }", "reconciliation.yaml"),
        ("movement_date: trade_date   #", "movement_date: booking_date   #", "reconciliation.yaml"),
        ("domain: custodial", "domain: wealth", "reconciliation domain 'wealth' must match the pack directory 'custodial'"),
        ('model_version: "1.0"', 'model_version: "2.0"', "reconciliation reads model version 2.0, which the pack does not have"),
    ],
)
def test_a_reconciliation_file_that_does_not_hold_together_is_refused(tmp_path, old, new, message):
    loaded, problems = _load_with(tmp_path, old, new)
    assert loaded is None and any(message in p.message or message in p.path for p in problems), [p.format() for p in problems]


def test_a_pack_whose_taxonomy_lacks_the_checks_code_is_refused(tmp_path):
    domains = tmp_path / "domains"
    shutil.copytree(CUSTODIAL, domains / "custodial")
    path = domains / "custodial" / "rejections.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("  - code: POSITION_QUANTITY_MISMATCH")
    end = text.index("  - code: POSITION_VALUE_MISMATCH")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    loaded, problems = load_pack(domains / "custodial", tmp_path)
    assert loaded is None and any("POSITION_QUANTITY_MISMATCH, which is not in the rejection taxonomy" in p.message for p in problems)


def test_a_pack_without_reconciliation_is_allowed(tmp_path):
    domains = tmp_path / "domains"
    shutil.copytree(CUSTODIAL, domains / "custodial")
    (domains / "custodial" / "reconciliation.yaml").unlink()
    loaded, problems = load_pack(domains / "custodial", tmp_path)
    assert problems == [] and loaded.reconciliation is None


# ---------------------------------------------------------------- tolerance per field type


def test_exact_is_the_default_and_matches_only_equal_values():
    assert Tolerance().matches(D("1.5"), D("1.5")) and not Tolerance().matches(D("1.5"), D("1.5000001"))


def test_decimal_places_rounds_both_sides_half_away_from_zero():
    two = Tolerance("decimal_places", places=2)
    assert two.matches(D("1.004"), D("1.001")) and two.matches(D("1.005"), D("1.01")) and not two.matches(D("1.005"), D("1.00"))
    assert two.matches(D("-1.005"), D("-1.01"))  # a tie moves away from zero on the negative side too


def test_absolute_allows_a_difference_up_to_and_including_epsilon():
    half = Tolerance("absolute", epsilon=D("0.5"))
    assert half.matches(D("10"), D("10.5")) and half.matches(D("10"), D("9.5")) and not half.matches(D("10"), D("10.51"))


def test_relative_is_a_fraction_of_the_larger_magnitude():
    one_percent = Tolerance("relative", epsilon=D("0.01"))
    assert one_percent.matches(D("1000"), D("1010")) and not one_percent.matches(D("1000"), D("1011"))
    assert one_percent.matches(D("0"), D("0")) and not one_percent.matches(D("0"), D("0.0001"))


def test_a_wide_value_is_compared_without_overflowing_the_default_context():
    wide = D("99999999999999999999.99999999")
    assert Tolerance("decimal_places", places=12).matches(wide, wide) and Tolerance("relative", epsilon=D("0.0001")).matches(wide, wide)


# ---------------------------------------------------------------- the position identity, on seeded positions


def test_a_position_that_is_prior_plus_transactions_is_not_a_break(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150")]
    assert positions(config, snapshot, [txn("BUY", "50")]) == []


def test_a_seeded_break_is_detected_and_categorised_with_the_size_of_the_difference(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="140")]
    (found,) = positions(config, snapshot, [txn("BUY", "50")])
    assert (found.check, found.category, found.rejection_code) == ("position_quantity", "mismatch", "POSITION_QUANTITY_MISMATCH")
    assert (found.account_number, found.security_id, found.as_of_date, found.prior_date) == ("A1", "S1", DAY, PRIOR)
    assert (found.prior_value, found.movement, found.expected, found.actual, found.difference) == (D("100"), D("50"), D("150"), D("140"), D("-10"))


def test_a_sell_and_a_transfer_out_take_quantity_away_whatever_the_sign_the_row_carries(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="60")]
    assert positions(config, snapshot, [txn("SELL", "30", tid="T1"), txn("TRANSFER_OUT", "-10", tid="T2")]) == []
    assert positions(config, snapshot, [txn("SELL", "-30", tid="T1"), txn("TRANSFER_OUT", "10", tid="T2")]) == []


def test_a_transfer_in_adds_quantity(config):
    assert positions(config, [pos(on=PRIOR, quantity="10"), pos(on=DAY, quantity="35")], [txn("TRANSFER_IN", "25")]) == []


def test_cash_only_types_do_not_touch_quantity(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]
    cash_only = [txn(kind, None, tid=kind) for kind in ("DIVIDEND", "INTEREST", "CAPITAL_GAIN", "FEE", "DEPOSIT", "WITHDRAWAL")]
    assert positions(config, snapshot, cash_only) == []
    assert positions(config, snapshot, [txn("DIVIDEND", "500")]) == []  # even a quantity on the row is not a movement for a cash type


@pytest.mark.parametrize("status", ["CANCELLED", "SUPERSEDED", "CANCEL"])
def test_only_active_transactions_count(config, status):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]
    assert positions(config, snapshot, [txn("BUY", "50", status=status)]) == []


def test_the_window_is_after_the_prior_snapshot_up_to_and_including_the_business_date(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="105")]
    assert positions(config, snapshot, [txn("BUY", "5", trade=DAY)]) == []  # on the business date: counts
    assert len(positions(config, snapshot, [txn("BUY", "5", trade=PRIOR)])) == 1  # on the prior date: the prior snapshot already holds it
    assert len(positions(config, snapshot, [txn("BUY", "5", trade=NEXT)])) == 1  # after the business date: not yet


def test_the_prior_snapshot_is_the_latest_one_before_the_business_date_per_account(config):
    older = date(2026, 9, 10)
    snapshot = [pos(on=older, quantity="1"), pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150")]
    assert positions(config, snapshot, [txn("BUY", "50")]) == []
    (found,) = positions(config, [pos(on=older, quantity="1"), pos(on=DAY, quantity="150")], [txn("BUY", "50")])
    assert found.prior_date == older and found.expected == D("51")


def test_a_position_that_appears_without_a_transaction_to_explain_it_is_categorised_appeared(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100"), pos(security="S2", on=DAY, quantity="25")]
    (found,) = positions(config, snapshot, [])
    assert (found.category, found.security_id, found.prior_value, found.expected, found.actual) == ("appeared", "S2", None, D("0"), D("25"))
    assert positions(config, snapshot, [txn("BUY", "25", security="S2")]) == []  # bought that day: explained


def test_a_position_that_vanishes_without_a_sale_is_categorised_disappeared(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(security="S3", on=PRIOR, quantity="10"), pos(on=DAY, quantity="100")]
    (found,) = positions(config, snapshot, [])
    assert (found.category, found.security_id, found.prior_value, found.expected, found.actual, found.difference) == ("disappeared", "S3", D("10"), D("10"), None, D("-10"))
    assert positions(config, snapshot, [txn("SELL", "10", security="S3")]) == []  # sold out: it is right that it is gone


def test_a_purchase_that_never_reached_the_position_file_is_disappeared(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]
    (found,) = positions(config, snapshot, [txn("BUY", "40", security="S9")])
    assert (found.category, found.security_id, found.prior_value, found.expected) == ("disappeared", "S9", None, D("40"))


def test_an_account_with_no_earlier_snapshot_is_a_baseline_not_a_break(config):
    snapshot = [pos(account="NEW1", on=DAY, quantity="999")]
    assert positions(config, snapshot, [txn("BUY", "1", account="NEW1")]) == []


def test_another_custodians_rows_are_left_alone(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150"), pos(custodian="fidelity", on=PRIOR, quantity="1"), pos(custodian="fidelity", on=DAY, quantity="2")]
    assert positions(config, snapshot, [txn("BUY", "50"), txn("BUY", "500", custodian="fidelity", tid="T2")]) == []


@pytest.mark.parametrize("kind", ["CORPORATE_ACTION", "ADJUSTMENT", "OTHER", "SWAP"])
def test_a_movement_that_cannot_be_computed_makes_the_position_unverifiable_not_a_guess(config, kind):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]  # equal, and still not asserted: the equation is incomplete
    (found,) = positions(config, snapshot, [txn(kind, "10")])
    assert (found.category, found.unverifiable_count, found.movement) == ("unverifiable", 1, D("0"))


def test_a_movement_type_without_a_quantity_is_unverifiable(config):
    (found,) = positions(config, [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")], [txn("BUY", None)])
    assert found.category == "unverifiable"


def test_a_transaction_the_movement_date_cannot_place_is_unverifiable_only_when_it_could_move_quantity(config):
    by_settlement = replace(config, movement_date="settle_date")
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]
    (found,) = positions(by_settlement, snapshot, [txn("BUY", "5", settle=None)])
    assert found.category == "unverifiable"
    assert positions(by_settlement, snapshot, [txn("DIVIDEND", None, settle=None)]) == []  # cannot move quantity: it does not matter
    assert positions(by_settlement, [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="105")], [txn("BUY", "5", trade=PRIOR, settle=DAY)]) == []  # placed by its settle date


def test_a_transaction_dated_outside_the_window_by_trade_date_is_not_undated_by_settle_date(config):
    by_settlement = replace(config, movement_date="settle_date")
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="100")]
    assert positions(by_settlement, snapshot, [txn("BUY", "5", trade=date(2026, 9, 1), settle=None)]) == []


# ---------------------------------------------------------------- tolerance per field type


def test_a_quantity_tolerance_is_applied_to_positions_and_an_amount_tolerance_to_cash(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150.4")]
    loose_quantity = replace(config, tolerances={"quantity": Tolerance("absolute", epsilon=D("0.5"))})
    assert positions(loose_quantity, snapshot, [txn("BUY", "50")]) == []
    assert len(positions(config, snapshot, [txn("BUY", "50")])) == 1  # exact by default
    assert len(positions(replace(loose_quantity, tolerances={"quantity": Tolerance("absolute", epsilon=D("0.3"))}), snapshot, [txn("BUY", "50")])) == 1

    balances = [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="1499.99")]
    settled = [txn("BUY", "1", net="500", settle=DAY)]
    assert len(reconcile_cash(loose_quantity, balances, settled, C, DAY)) == 1  # a quantity tolerance does not loosen cash
    loose_amount = replace(config, tolerances={"amount": Tolerance("decimal_places", places=1)})
    assert reconcile_cash(loose_amount, balances, settled, C, DAY) == []
    assert len(positions(loose_amount, [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150.01")], [txn("BUY", "50")])) == 1  # nor does an amount tolerance loosen quantity


# ---------------------------------------------------------------- the cash identity, on seeded balances


def test_a_balance_that_is_prior_plus_net_amounts_is_not_a_break_and_a_seeded_one_is(config):
    settled = [txn("SELL", "10", net="2500", settle=DAY, tid="T1"), txn("WITHDRAWAL", None, security=None, net="-400", settle=DAY, tid="T2")]
    assert reconcile_cash(config, [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="3100")], settled, C, DAY) == []
    (found,) = reconcile_cash(config, [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="3000")], settled, C, DAY)
    assert (found.check, found.category, found.rejection_code) == ("cash_balance", "mismatch", "CASH_BALANCE_MISMATCH")
    assert (found.account_number, found.currency, found.balance_type, found.security_id) == ("A1", "USD", "SETTLED", None)
    assert (found.prior_value, found.movement, found.expected, found.actual, found.difference) == (D("1000"), D("2100"), D("3100"), D("3000"), D("-100"))


def test_each_balance_type_moves_on_its_own_date(config):
    balances = [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="1000"), cash(on=PRIOR, amount="1000", balance_type="TRADE_DATE"), cash(on=DAY, amount="1500", balance_type="TRADE_DATE")]
    unsettled = [txn("SELL", "10", net="500", trade=DAY, settle=NEXT)]  # traded today, settles tomorrow
    assert reconcile_cash(config, balances, unsettled, C, DAY) == []


def test_a_balance_of_another_currency_or_account_is_not_moved_by_the_transaction(config):
    balances = [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="1000")]
    assert reconcile_cash(config, balances, [txn("SELL", "10", net="500", settle=DAY, currency="EUR"), txn("SELL", "10", net="500", settle=DAY, account="A2", tid="T2")], C, DAY) == []


def test_a_balance_that_should_be_there_and_is_not_is_disappeared(config):
    (found,) = reconcile_cash(config, [cash(on=PRIOR, amount="1000")], [], C, DAY)
    assert (found.category, found.prior_value, found.expected, found.actual, found.difference) == ("disappeared", D("1000"), D("1000"), None, D("-1000"))
    assert reconcile_cash(config, [cash(on=PRIOR, amount="0")], [], C, DAY) == []  # an emptied balance need not be reported


def test_a_balance_with_no_earlier_balance_is_a_baseline(config):
    assert reconcile_cash(config, [cash(on=DAY, amount="12345")], [txn("SELL", "1", net="1", settle=DAY)], C, DAY) == []


def test_a_transaction_the_movement_date_cannot_place_makes_a_balance_unverifiable(config):
    balances = [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="1000")]
    (found,) = reconcile_cash(config, balances, [txn("SELL", "10", net="500", trade=DAY, settle=None)], C, DAY)
    assert (found.category, found.unverifiable_count, found.balance_type) == ("unverifiable", 1, "SETTLED")  # the trade-date balance needs no settle date


def test_cancelled_transactions_do_not_move_cash(config):
    assert reconcile_cash(config, [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="1000")], [txn("SELL", "10", net="500", settle=DAY, status="CANCELLED")], C, DAY) == []


# ---------------------------------------------------------------- together


def test_every_category_is_reachable_and_the_set_is_closed(config):
    snapshot = [
        pos(account="A1", on=PRIOR, quantity="100"), pos(account="A1", on=DAY, quantity="140"),  # mismatch
        pos(account="A1", security="S2", on=DAY, quantity="25"),  # appeared
        pos(account="A1", security="S3", on=PRIOR, quantity="10"),  # disappeared
        pos(account="A2", on=PRIOR, quantity="5"), pos(account="A2", on=DAY, quantity="5"),  # unverifiable
    ]
    found = reconcile(config, snapshot, [], [txn("BUY", "50"), txn("CORPORATE_ACTION", "1", account="A2", tid="T2")], C, DAY)
    assert {b.category for b in found} == set(CATEGORIES)
    assert {(b.check, b.category, b.security_id) for b in found} == {("position_quantity", "mismatch", "S1"), ("position_quantity", "appeared", "S2"), ("position_quantity", "disappeared", "S3"), ("position_quantity", "unverifiable", "S1")}


def test_a_clean_day_has_no_breaks(config):
    snapshot = [pos(on=PRIOR, quantity="100"), pos(on=DAY, quantity="150")]
    balances = [cash(on=PRIOR, amount="1000"), cash(on=DAY, amount="0"), cash(on=PRIOR, amount="1000", balance_type="TRADE_DATE"), cash(on=DAY, amount="0", balance_type="TRADE_DATE")]
    trades = [txn("BUY", "50", net="-1000", settle=DAY)]
    assert reconcile(config, snapshot, balances, trades, C, DAY) == []
