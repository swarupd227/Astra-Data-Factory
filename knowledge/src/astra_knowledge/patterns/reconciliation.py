"""Pattern: reconcile a position and a cash balance with the transactions between two snapshots.

Two identities, checked per custodian and business date over the canonical tables:

  position identity   for each account that has an earlier snapshot: the quantity of a position equals
                      its quantity in that snapshot (nothing, if it was not held) plus the securities
                      movement of the transactions dated after that snapshot up to and including the
                      business date. Only ACTIVE transactions count. A transaction moves by the
                      direction of its type times the size of its quantity; a type that cannot be
                      computed from the row (`unreconcilable`), a type the file does not know, a
                      quantity that is missing, or a movement date that is missing makes the position
                      unverifiable -- the equation is incomplete, so it is reported, not guessed at.
  cash identity       for each account, currency and balance type that has an earlier balance: the
                      balance equals that balance plus the net amount of the ACTIVE transactions of the
                      same currency dated after it up to the business date, dated by the check's
                      movement date. A transaction the movement date cannot place is unverifiable.

An account that has no earlier snapshot is a baseline, not a break: nothing can be said about it yet.
A difference beyond the tolerance of the field type (`quantity`, `amount`) is a break, and each break
has one category:

  mismatch      the position or balance is in both snapshots and the identity does not hold
  appeared      a position not in the earlier snapshot is in this one with a quantity the movement does not explain
  disappeared   a position or balance that the identity says should be there is not
  unverifiable  the identity cannot be evaluated, for the reason above

The rendered SQL (`astra_data.reconciliation`) evaluates the same identities over SILVER in one
statement per check; this module is what it is checked against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from astra_knowledge.reconciliation import CHECK_CODES, UNRECONCILABLE, Reconciliation

CATEGORIES = ("mismatch", "appeared", "disappeared", "unverifiable")
ACTIVE = "ACTIVE"
ZERO = Decimal(0)


@dataclass(frozen=True)
class PositionRow:
    custodian_id: str
    account_number: str
    security_id: str
    as_of_date: date
    quantity: Decimal


@dataclass(frozen=True)
class CashRow:
    custodian_id: str
    account_number: str
    currency: str
    balance_type: str
    as_of_date: date
    amount: Decimal


@dataclass(frozen=True)
class TransactionRow:
    custodian_id: str
    transaction_id: str
    account_number: str
    security_id: str | None
    transaction_type: str
    trade_date: date
    settle_date: date | None
    quantity: Decimal | None
    net_amount: Decimal
    currency: str
    status: str = ACTIVE


@dataclass(frozen=True)
class Break:
    """One position or balance whose identity does not hold, or cannot be checked."""

    check: str
    category: str
    custodian_id: str
    account_number: str
    as_of_date: date
    prior_date: date
    prior_value: Decimal | None
    movement: Decimal
    expected: Decimal
    actual: Decimal | None
    unverifiable_count: int
    security_id: str | None = None  # a position break
    currency: str | None = None  # a cash break
    balance_type: str | None = None

    @property
    def difference(self) -> Decimal:
        """What is there minus what the identity says should be."""
        return (self.actual if self.actual is not None else ZERO) - self.expected

    @property
    def rejection_code(self) -> str:
        return CHECK_CODES[self.check]

    def key(self) -> tuple:
        return (self.check, self.account_number, self.security_id, self.currency, self.balance_type)


def _movement_date(kind: str, trade_date: date, settle_date: date | None) -> date | None:
    return trade_date if kind == "trade_date" else settle_date


def reconcile_positions(config: Reconciliation, positions: list[PositionRow], transactions: list[TransactionRow], custodian_id: str, as_of: date) -> list[Break]:
    tolerance = config.tolerance("quantity")
    rows = [p for p in positions if p.custodian_id == custodian_id]
    prior_date: dict[str, date] = {}
    for p in rows:
        if p.as_of_date < as_of and p.as_of_date > prior_date.get(p.account_number, date.min):
            prior_date[p.account_number] = p.as_of_date
    prior = {(p.account_number, p.security_id): p.quantity for p in rows if prior_date.get(p.account_number) == p.as_of_date}
    current = {(p.account_number, p.security_id): p.quantity for p in rows if p.as_of_date == as_of and p.account_number in prior_date}

    moves: dict[tuple[str, str], list] = {}
    for t in transactions:
        if t.custodian_id != custodian_id or t.status != ACTIVE or t.security_id is None or t.account_number not in prior_date:
            continue
        factor = config.movements.get(t.transaction_type, UNRECONCILABLE)  # a type the model does not know is not computable either
        if factor == 0:
            continue  # cash only: it does not touch quantity
        low = prior_date[t.account_number]
        moved_on = _movement_date(config.movement_date, t.trade_date, t.settle_date)
        undated = moved_on is None and low < t.trade_date <= as_of
        placed = moved_on is not None and low < moved_on <= as_of
        if not (undated or placed):
            continue
        entry = moves.setdefault((t.account_number, t.security_id), [ZERO, 0])
        if undated or factor == UNRECONCILABLE or t.quantity is None:
            entry[1] += 1
        else:
            entry[0] += factor * abs(t.quantity)

    breaks: list[Break] = []
    for account, security in sorted(set(prior) | set(current) | set(moves)):
        before, after = prior.get((account, security)), current.get((account, security))
        movement, unverifiable = moves.get((account, security), (ZERO, 0))
        expected = (before if before is not None else ZERO) + movement
        if unverifiable > 0:
            category = "unverifiable"
        elif tolerance.matches(expected, after if after is not None else ZERO):
            continue
        elif after is None:
            category = "disappeared"
        elif before is None:
            category = "appeared"
        else:
            category = "mismatch"
        breaks.append(Break("position_quantity", category, custodian_id, account, as_of, prior_date[account], before, movement, expected, after, unverifiable, security_id=security))
    return breaks


def reconcile_cash(config: Reconciliation, balances: list[CashRow], transactions: list[TransactionRow], custodian_id: str, as_of: date) -> list[Break]:
    tolerance = config.tolerance("amount")
    breaks: list[Break] = []
    for check in config.cash:
        rows = [c for c in balances if c.custodian_id == custodian_id and c.balance_type == check.balance_type]
        prior_date: dict[tuple[str, str], date] = {}
        for c in rows:
            key = (c.account_number, c.currency)
            if c.as_of_date < as_of and c.as_of_date > prior_date.get(key, date.min):
                prior_date[key] = c.as_of_date
        prior = {(c.account_number, c.currency): c.amount for c in rows if prior_date.get((c.account_number, c.currency)) == c.as_of_date}
        current = {(c.account_number, c.currency): c.amount for c in rows if c.as_of_date == as_of and (c.account_number, c.currency) in prior_date}

        moves: dict[tuple[str, str], list] = {}
        for t in transactions:
            key = (t.account_number, t.currency)
            if t.custodian_id != custodian_id or t.status != ACTIVE or key not in prior_date:
                continue
            low = prior_date[key]
            moved_on = _movement_date(check.movement_date, t.trade_date, t.settle_date)
            undated = moved_on is None and low < t.trade_date <= as_of
            placed = moved_on is not None and low < moved_on <= as_of
            if not (undated or placed):
                continue
            entry = moves.setdefault(key, [ZERO, 0])
            if undated:
                entry[1] += 1
            else:
                entry[0] += t.net_amount

        for account, currency in sorted(prior):
            before, after = prior[(account, currency)], current.get((account, currency))
            movement, unverifiable = moves.get((account, currency), (ZERO, 0))
            expected = before + movement
            if unverifiable > 0:
                category = "unverifiable"
            elif tolerance.matches(expected, after if after is not None else ZERO):
                continue
            elif after is None:
                category = "disappeared"
            else:
                category = "mismatch"
            breaks.append(Break("cash_balance", category, custodian_id, account, as_of, prior_date[(account, currency)], before, movement, expected, after, unverifiable, currency=currency, balance_type=check.balance_type))
    return breaks


def reconcile(config: Reconciliation, positions: list[PositionRow], balances: list[CashRow], transactions: list[TransactionRow], custodian_id: str, as_of: date) -> list[Break]:
    """Every break of the business date for one custodian: the position identity, then the cash identity."""
    return reconcile_positions(config, positions, transactions, custodian_id, as_of) + reconcile_cash(config, balances, transactions, custodian_id, as_of)
