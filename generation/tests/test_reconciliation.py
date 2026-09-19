"""Reconciliation, rendered as a bundle and checked against its reference implementation (S7.1.5)."""

from __future__ import annotations

import random
import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from astra_knowledge.patterns.reconciliation import CATEGORIES, CashRow, PositionRow, TransactionRow, reconcile
from astra_knowledge.reconciliation import CHECK_CODES, CHECKS, CashCheck, Tolerance

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.reconciliation import (
    BREAK_COLUMNS,
    break_query,
    bundle_name,
    cash_query,
    check_bundle,
    packs_with_reconciliation,
    position_query,
    render_bundle,
    within,
    write_bundle,
)

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
RELEASES = REPO / "releases"
D = Decimal
AS_OF = date(2026, 9, 17)


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return []

    def close(self) -> None:
        pass


def _pack():
    packs, problems = packs_with_reconciliation(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]
    return packs[0]


def _config():
    return _pack().reconciliation


# ---------------------------------------------------------------- what is rendered


def test_the_bundle_has_the_findings_the_procedure_and_the_invariant_tests():
    files = render_bundle(_pack())
    assert set(files) == {
        "manifest.yaml",
        "ddl/reconciliation.sql",
        "pipeline/reconcile.sql",
        "tests/reconciliation_breaks_carry_their_checks_code.sql",
        "tests/reconciliation_breaks_are_well_formed.sql",
        "tests/reconciliation_breaks_are_unique.sql",
        "tests/reconciliation_breaks_are_outside_their_tolerance.sql",
        "tests/reconciliation_breaks_add_up.sql",
    }
    manifest = files["manifest.yaml"]
    assert "bundle: custodial-reconciliation" in manifest and "source: custodial_reconciliation" in manifest
    assert manifest.index("ddl/reconciliation.sql") < manifest.index("pipeline/reconcile.sql")
    assert render_bundle(_pack()) == files  # deterministic


def test_the_findings_table_holds_a_break_with_its_code_its_category_and_the_arithmetic():
    ddl = render_bundle(_pack())["ddl/reconciliation.sql"]
    assert 'CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS"' in ddl and "BASE_LOCATION = 'control/reconciliation_breaks/'" in ddl
    for column in ("RUN_ID",) + BREAK_COLUMNS + ("DETECTED_AT",):
        assert f'"{column}"' in ddl
    assert "position_quantity, cash_balance" in ddl and "mismatch, appeared, disappeared, unverifiable" in ddl
    assert 'MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'account_number\';' in ddl  # the account number is PII wherever it is kept
    runs = ddl[ddl.index('"RECONCILIATION_RUNS"'):]
    for column in ("RUN_ID", "CUSTODIAN_ID", "AS_OF_DATE", "STARTED_AT", "FINISHED_AT", "POSITIONS_IN_SCOPE", "BALANCES_IN_SCOPE", "BREAKS_FOUND"):
        assert f'"{column}"' in runs


def test_the_procedure_replaces_a_custodian_days_findings_and_logs_the_run_in_one_transaction():
    sql = render_bundle(_pack())["pipeline/reconcile.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."RECONCILE"("CUSTODIAN_ID" STRING, "AS_OF_DATE" DATE)' in sql and "EXECUTE AS OWNER" in sql
    order = [sql.index(x) for x in ("BEGIN TRANSACTION;", 'DELETE FROM {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "AS_OF_DATE" = :AS_OF_DATE;', 'INSERT INTO {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS"', "found := SQLROWCOUNT;", 'INSERT INTO {{ DATABASE }}."CONTROL"."RECONCILIATION_RUNS"', "COMMIT;")]
    assert order == sorted(order)
    assert "EXCEPTION\n  WHEN OTHER THEN\n    ROLLBACK;\n    RAISE;" in sql
    assert 'positions := (SELECT COUNT(*) FROM {{ DATABASE }}."SILVER"."POSITION" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "AS_OF_DATE" = :AS_OF_DATE);' in sql
    assert 'balances := (SELECT COUNT(*) FROM {{ DATABASE }}."SILVER"."CASH_BALANCE"' in sql


def test_the_procedure_runs_exactly_the_checks_the_tests_run():
    """The text the procedure embeds is the text the equivalence tests execute, indented, with bind variables."""
    config = _config()
    sql = render_bundle(_pack())["pipeline/reconcile.sql"]
    embedded = "\n".join(f"    {line}" if line else line for line in break_query(config, ":CUSTODIAN_ID", ":AS_OF_DATE").splitlines())
    assert embedded in sql
    assert sql.count("UNION ALL") == len(config.cash)  # the position identity, then one cash identity per balance type
    assert re.findall(r'AS "CHECK_NAME"', sql) and sql.count("'position_quantity' AS") == 1 and sql.count("'cash_balance' AS") == len(config.cash)


def test_the_movements_of_the_config_are_the_types_table_of_the_position_query():
    query = position_query(_config(), "'pershing'", "DATE '2026-09-17'")
    values = re.search(r"FROM VALUES (.*?) AS v \(\"TYPE\", \"FACTOR\", \"UNRECONCILABLE\"\)", query).group(1)
    assert re.findall(r"\('(\w+)', (-?\d), (TRUE|FALSE)\)", values) == [(k, "0" if m == "unreconcilable" else str(m), "TRUE" if m == "unreconcilable" else "FALSE") for k, m in _config().movements.items()]


def test_each_balance_type_is_checked_on_its_own_movement_date_and_only_a_missable_date_can_make_it_unverifiable():
    config = _config()
    settled, traded = (cash_query(config, check, "'pershing'", "DATE '2026-09-17'") for check in config.cash)
    assert 't."SETTLE_DATE" > d."PRIOR_DATE" AND t."SETTLE_DATE" <= DATE \'2026-09-17\'' in settled and 't."SETTLE_DATE" IS NULL AND t."TRADE_DATE"' in settled
    assert 't."TRADE_DATE" > d."PRIOR_DATE"' in traded and 'IS NULL' not in traded.split('"MOVES"')[1].split('"EVAL"')[0]  # a trade date is never missing
    assert "'SETTLED'" in settled and "'TRADE_DATE'" in traded


@pytest.mark.parametrize(
    ("tolerance", "sql"),
    [
        (Tolerance("exact"), 'a = b'),
        (Tolerance("decimal_places", places=2), "ROUND(a, 2) = ROUND(b, 2)"),
        (Tolerance("absolute", epsilon=D("0.5")), "ABS(a - b) <= 0.5"),
        (Tolerance("relative", epsilon=D("0.0000001")), "ABS(a - b) <= 0.0000001 * GREATEST(ABS(a), ABS(b))"),
    ],
)
def test_every_tolerance_kind_is_rendered_as_the_reference_implementation_defines_it(tolerance, sql):
    assert within(tolerance, "a", "b") == sql


def test_the_tolerance_per_field_type_reaches_the_query_of_its_own_field():
    config = replace(_config(), tolerances={"quantity": Tolerance("absolute", epsilon=D("0.5")), "amount": Tolerance("decimal_places", places=1)})
    assert 'ABS("EXPECTED" - COALESCE("ACTUAL", 0)) <= 0.5' in position_query(config, "'c'", "DATE '2026-09-17'")
    for check in config.cash:
        assert 'ROUND("EXPECTED", 1) = ROUND(COALESCE("ACTUAL", 0), 1)' in cash_query(config, check, "'c'", "DATE '2026-09-17'")
    assert 'ABS("EXPECTED"' not in cash_query(config, config.cash[0], "'c'", "DATE '2026-09-17'")


# ---------------------------------------------------------------- deploys through the executor


def test_the_committed_bundle_is_current_and_deploys_through_the_executor():
    pack = _pack()
    assert check_bundle(pack, RELEASES, REPO) == []
    bundles, problems = check_bundles(RELEASES, REPO)
    assert problems == [] and any(b.name == bundle_name(pack) for b in bundles)
    bundle = load_bundle(RELEASES / "custodial-reconciliation", REPO)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/reconciliation.sql", "pipeline/reconcile.sql")
    assert all('ASTRA_QA."CONTROL"' in script for script in executor.scripts) and "{{" not in "".join(executor.scripts)
    results = run_tests(bundle, Target("qa"), executor)
    assert len(results) == 5 and all(r.passed for r in results)


def test_write_removes_stale_files_and_check_reports_drift(tmp_path):
    releases = tmp_path / "releases"
    pack = _pack()
    root = write_bundle(pack, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(pack, releases)
    assert not stale.exists() and check_bundle(pack, releases, tmp_path) == []
    (root / "pipeline" / "reconcile.sql").write_text("-- edited by hand\n", encoding="utf-8")
    problems = check_bundle(pack, releases, tmp_path)
    assert [(p.path, p.message.split(";")[0]) for p in problems] == [("releases/custodial-reconciliation/pipeline/reconcile.sql", "stale: the reconciliation file or the model changed since it was rendered")]


def test_cli_renders_and_checks(tmp_path, capsys):
    out = tmp_path / "releases"
    assert main(["--root", str(REPO), "reconciliation", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 1
    assert "not rendered; run astra-data reconciliation render" in capsys.readouterr().out
    assert main(["--root", str(REPO), "reconciliation", "render", "--domains", str(DOMAINS), "--releases", str(out)]) == 0
    assert "rendered custodial-reconciliation: 3 checks (model 1.0)" in capsys.readouterr().out
    assert main(["--root", str(REPO), "reconciliation", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 0
    assert "Reconciliation bundles are current for 1 domain pack" in capsys.readouterr().out


# ---------------------------------------------------------------- the SQL, run, agrees with the reference implementation
#
# The rendered checks are Snowflake SQL. sqlglot turns them into DuckDB's dialect, and they run over seeded copies of the
# three canonical tables; whatever they find must be exactly what the reference implementation finds in the same rows.


@pytest.fixture(scope="module")
def duck():
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("sqlglot")
    con = duckdb.connect()
    con.execute("CREATE SCHEMA SILVER")
    con.execute("CREATE SCHEMA CONTROL")
    con.execute('CREATE TABLE SILVER."POSITION" ("CUSTODIAN_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR, "SECURITY_ID" VARCHAR, "AS_OF_DATE" DATE, "QUANTITY" DECIMAL(28,8))')
    con.execute('CREATE TABLE SILVER."CASH_BALANCE" ("CUSTODIAN_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR, "CURRENCY" VARCHAR, "BALANCE_TYPE" VARCHAR, "AS_OF_DATE" DATE, "AMOUNT" DECIMAL(28,4))')
    con.execute(
        'CREATE TABLE SILVER."TRANSACTION" ("CUSTODIAN_ID" VARCHAR, "TRANSACTION_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR, "SECURITY_ID" VARCHAR, "TRANSACTION_TYPE" VARCHAR, "TRADE_DATE" DATE, "SETTLE_DATE" DATE, "QUANTITY" DECIMAL(28,8), "NET_AMOUNT" DECIMAL(28,4), "CURRENCY" VARCHAR, "STATUS" VARCHAR)'
    )
    return con


def _duckdb_sql(sql: str) -> str:
    import sqlglot

    return sqlglot.transpile(sql.replace("{{ DATABASE }}", "memory"), read="snowflake", write="duckdb")[0]


def _load(con, positions: list[PositionRow], balances: list[CashRow], transactions: list[TransactionRow]) -> None:
    for table in ("POSITION", "CASH_BALANCE", "TRANSACTION"):
        con.execute(f'DELETE FROM SILVER."{table}"')
    _insert(con, 'INSERT INTO SILVER."POSITION" VALUES (?, ?, ?, ?, ?)', [(p.custodian_id, p.account_number, p.security_id, p.as_of_date, p.quantity) for p in positions])
    _insert(con, 'INSERT INTO SILVER."CASH_BALANCE" VALUES (?, ?, ?, ?, ?, ?)', [(c.custodian_id, c.account_number, c.currency, c.balance_type, c.as_of_date, c.amount) for c in balances])
    _insert(
        con,
        'INSERT INTO SILVER."TRANSACTION" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [(t.custodian_id, t.transaction_id, t.account_number, t.security_id, t.transaction_type, t.trade_date, t.settle_date, t.quantity, t.net_amount, t.currency, t.status) for t in transactions],
    )


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, (Decimal, int)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _insert(con, statement: str, rows: list[tuple]) -> None:
    """One multi-row INSERT: DuckDB's executemany is a round trip per row, and hundreds of seeded worlds need thousands of rows.
    The values are the tests' own, so they are written as literals."""
    if rows:
        table = statement.split(" VALUES")[0]
        con.execute(f"{table} VALUES " + ", ".join("(" + ", ".join(_literal(v) for v in row) + ")" for row in rows))


def _dec(value):
    return None if value is None else Decimal(str(value))


def _sql_breaks(con, config, custodian="pershing", as_of=AS_OF) -> list[tuple]:
    sql = _duckdb_sql(break_query(config, f"'{custodian}'", f"DATE '{as_of.isoformat()}'"))
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    assert tuple(names) == BREAK_COLUMNS
    found = []
    for row in cursor.fetchall():
        r = dict(zip(names, row))
        found.append(_key(r["CHECK_NAME"], r["CATEGORY"], r["ACCOUNT_NUMBER"], r["SECURITY_ID"], r["CURRENCY"], r["BALANCE_TYPE"], r["PRIOR_DATE"], _dec(r["PRIOR_VALUE"]), _dec(r["MOVEMENT"]), _dec(r["EXPECTED"]), _dec(r["ACTUAL"]), _dec(r["DIFFERENCE"]), int(r["UNVERIFIABLE_COUNT"]), r["REJECTION_CODE"]))
    return sorted(found, key=repr)


def _key(*parts) -> tuple:
    """A comparable row: decimals normalised, so 100 and 100.00000000 are the same value and sort the same."""
    return tuple(p.normalize() if isinstance(p, Decimal) else p for p in parts)


def _twin_breaks(config, positions, balances, transactions, custodian="pershing", as_of=AS_OF) -> list[tuple]:
    return sorted(
        (_key(b.check, b.category, b.account_number, b.security_id, b.currency, b.balance_type, b.prior_date, b.prior_value, b.movement, b.expected, b.actual, b.difference, b.unverifiable_count, b.rejection_code) for b in reconcile(config, positions, balances, transactions, custodian, as_of)),
        key=repr,
    )


def _agree(duck, config, positions, balances, transactions, note=""):
    _load(duck, positions, balances, transactions)
    assert _sql_breaks(duck, config) == _twin_breaks(config, positions, balances, transactions), note


PRIOR = date(2026, 9, 16)


def _p(account="A1", security="S1", quantity="100", on=AS_OF, custodian="pershing"):
    return PositionRow(custodian, account, security, on, D(quantity))


def _t(kind="BUY", quantity="50", security="S1", account="A1", trade=AS_OF, settle=None, net="-1000", currency="USD", status="ACTIVE", custodian="pershing", tid="T1"):
    return TransactionRow(custodian, tid, account, security, kind, trade, settle, None if quantity is None else D(quantity), D(net), currency, status)


def _c(amount="1000", on=AS_OF, account="A1", currency="USD", balance_type="SETTLED", custodian="pershing"):
    return CashRow(custodian, account, currency, balance_type, on, D(amount))


def test_the_sql_finds_the_seeded_break_and_categorises_it_as_the_reference_implementation_does(duck):
    config = _config()
    positions = [_p(on=PRIOR), _p(quantity="140")]
    _load(duck, positions, [], [_t()])
    (found,) = _sql_breaks(duck, config)
    assert found[:2] == ("position_quantity", "mismatch") and found[7:12] == (D("100"), D("50"), D("150"), D("140"), D("-10")) and found[-1] == "POSITION_QUANTITY_MISMATCH"
    assert _twin_breaks(config, positions, [], [_t()]) == [found]


def test_a_clean_day_has_no_breaks_in_the_sql_either(duck):
    _agree(duck, _config(), [_p(on=PRIOR), _p(quantity="150")], [_c(on=PRIOR), _c(on=PRIOR, balance_type="TRADE_DATE"), _c(amount="0"), _c(amount="0", balance_type="TRADE_DATE")], [_t(settle=AS_OF)])
    assert _sql_breaks(duck, _config()) == []


def test_every_category_agrees_on_a_seeded_day(duck):
    config = _config()
    positions = [
        _p(on=PRIOR, quantity="100"), _p(quantity="140"),  # mismatch
        _p(security="S2", quantity="25"),  # appeared
        _p(security="S3", on=PRIOR, quantity="10"),  # disappeared
        _p(account="A2", on=PRIOR, quantity="5"), _p(account="A2", quantity="5"),  # unverifiable
    ]
    transactions = [_t("BUY", "50", settle=AS_OF), _t("CORPORATE_ACTION", "1", account="A2", tid="T2"), _t("SWAP", "3", account="A2", tid="T3"), _t("DIVIDEND", None, settle=AS_OF, tid="T4")]
    balances = [_c(on=PRIOR), _c(amount="4000"), _c(on=PRIOR, account="A2"), _c(on=PRIOR, account="A3", balance_type="TRADE_DATE")]
    _agree(duck, config, positions, balances, transactions)
    found = _sql_breaks(duck, config)
    assert {(b[0], b[1]) for b in found} >= {("position_quantity", c) for c in CATEGORIES} | {("cash_balance", "mismatch"), ("cash_balance", "disappeared")}


# One world per seed: three custodians' worth of accounts, securities, dates, transaction types (one the model does not know), statuses, nulls.
TYPES = ["BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT", "DIVIDEND", "INTEREST", "CAPITAL_GAIN", "FEE", "DEPOSIT", "WITHDRAWAL", "CORPORATE_ACTION", "ADJUSTMENT", "OTHER", "SWAP"]
DAYS = [date(2026, 9, d) for d in (10, 14, 15, 16, 17, 18)]


def _world(seed: int):
    rng = random.Random(seed)
    positions, balances, transactions = [], [], []
    for custodian in ("pershing", "fidelity"):
        for account in ("A1", "A2", "A3"):
            for day in DAYS:
                for security in ("S1", "S2", "S3"):
                    if rng.random() < 0.45:
                        positions.append(PositionRow(custodian, account, security, day, D(rng.choice(["0", "5", "10", "25", "100", "-10"]))))
                for currency in ("USD", "EUR"):
                    for balance_type in ("SETTLED", "TRADE_DATE"):
                        if rng.random() < 0.4:
                            balances.append(CashRow(custodian, account, currency, balance_type, day, D(rng.choice(["0", "100", "250.5", "-250.5", "-75.25", "1000", "1000.4"]))))
    for n in range(rng.randint(0, 14)):
        trade = rng.choice(DAYS)
        settle = None if rng.random() < 0.3 else trade + timedelta(days=rng.randint(0, 2))
        transactions.append(
            TransactionRow(
                rng.choice(["pershing", "pershing", "fidelity"]),
                f"T{n}",
                rng.choice(["A1", "A2", "A3"]),
                None if rng.random() < 0.15 else rng.choice(["S1", "S2", "S3"]),
                rng.choice(TYPES),
                trade,
                settle,
                None if rng.random() < 0.15 else D(rng.choice(["-10", "5", "10", "25"])),
                D(rng.choice(["-500", "-100.25", "0", "100", "250.5", "-250.5", "500"])),
                rng.choice(["USD", "USD", "EUR"]),
                rng.choice(["ACTIVE"] * 6 + ["CANCELLED", "SUPERSEDED"]),
            )
        )
    return positions, balances, transactions


VARIANTS = {
    "exact_by_trade_date": {},
    "loose_quantity_and_amount": {"tolerances": {"quantity": Tolerance("absolute", epsilon=D("10")), "amount": Tolerance("decimal_places", places=0)}},
    "relative_amounts_by_settle_date": {"movement_date": "settle_date", "tolerances": {"quantity": Tolerance("relative", epsilon=D("0.1")), "amount": Tolerance("relative", epsilon=D("0.05"))}},
    "cash_dates_swapped": {"cash": (CashCheck("SETTLED", "trade_date"), CashCheck("TRADE_DATE", "settle_date")), "tolerances": {"amount": Tolerance("absolute", epsilon=D("100"))}},
}


def test_the_sql_and_the_reference_implementation_agree_on_random_seeded_worlds(duck):
    """Eighty seeded worlds under four configurations: every position and balance of both custodians, transactions of every type and
    status, missing settle dates and quantities. The SQL, run, and the reference implementation must find the identical breaks."""
    seen: set[tuple[str, str]] = set()
    totals: dict[str, int] = {}
    for name, changes in VARIANTS.items():
        config = replace(_config(), **changes)
        sql = _duckdb_sql(break_query(config, "'pershing'", f"DATE '{AS_OF.isoformat()}'"))
        for seed in range(80):
            positions, balances, transactions = _world(seed)
            _load(duck, positions, balances, transactions)
            cursor = duck.execute(sql)
            names = [d[0] for d in cursor.description]
            found = sorted(
                (_key(r["CHECK_NAME"], r["CATEGORY"], r["ACCOUNT_NUMBER"], r["SECURITY_ID"], r["CURRENCY"], r["BALANCE_TYPE"], r["PRIOR_DATE"], _dec(r["PRIOR_VALUE"]), _dec(r["MOVEMENT"]), _dec(r["EXPECTED"]), _dec(r["ACTUAL"]), _dec(r["DIFFERENCE"]), int(r["UNVERIFIABLE_COUNT"]), r["REJECTION_CODE"]) for r in (dict(zip(names, row)) for row in cursor.fetchall())),
                key=repr,
            )
            expected = _twin_breaks(config, positions, balances, transactions)
            assert found == expected, f"{name}, seed {seed}"
            seen |= {(b[0], b[1]) for b in found}
            totals[name] = totals.get(name, 0) + len(found)
    # the comparison is not vacuous: every category of both checks that can occur did, and a looser tolerance really finds fewer
    assert seen >= {("position_quantity", c) for c in CATEGORIES} | {("cash_balance", c) for c in ("mismatch", "disappeared", "unverifiable")}
    assert totals["loose_quantity_and_amount"] < totals["exact_by_trade_date"]


# ---------------------------------------------------------------- the deployed invariant tests catch what they claim to


def _break_row(**over) -> dict:
    row = dict(RUN_ID="r1", CHECK_NAME="position_quantity", CATEGORY="mismatch", CUSTODIAN_ID="pershing", ACCOUNT_NUMBER="A1", SECURITY_ID="S1", CURRENCY=None, BALANCE_TYPE=None, AS_OF_DATE=AS_OF, PRIOR_DATE=PRIOR,
               PRIOR_VALUE=D("100"), MOVEMENT=D("50"), EXPECTED=D("150"), ACTUAL=D("140"), DIFFERENCE=D("-10"), UNVERIFIABLE_COUNT=0, REJECTION_CODE="POSITION_QUANTITY_MISMATCH", DETECTED_AT=date(2026, 9, 17))
    row.update(over)
    return row


def _run_bundle_test(con, name: str, rows: list[dict]) -> list:
    con.execute('DROP TABLE IF EXISTS CONTROL."RECONCILIATION_BREAKS"')
    con.execute(
        'CREATE TABLE CONTROL."RECONCILIATION_BREAKS" ("RUN_ID" VARCHAR, "CHECK_NAME" VARCHAR, "CATEGORY" VARCHAR, "CUSTODIAN_ID" VARCHAR, "ACCOUNT_NUMBER" VARCHAR, "SECURITY_ID" VARCHAR, "CURRENCY" VARCHAR, "BALANCE_TYPE" VARCHAR, "AS_OF_DATE" DATE, "PRIOR_DATE" DATE, '
        '"PRIOR_VALUE" DECIMAL(38,8), "MOVEMENT" DECIMAL(38,8), "EXPECTED" DECIMAL(38,8), "ACTUAL" DECIMAL(38,8), "DIFFERENCE" DECIMAL(38,8), "UNVERIFIABLE_COUNT" BIGINT, "REJECTION_CODE" VARCHAR, "DETECTED_AT" DATE)'
    )
    columns = ("RUN_ID", "CHECK_NAME", "CATEGORY", "CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "CURRENCY", "BALANCE_TYPE", "AS_OF_DATE", "PRIOR_DATE", "PRIOR_VALUE", "MOVEMENT", "EXPECTED", "ACTUAL", "DIFFERENCE", "UNVERIFIABLE_COUNT", "REJECTION_CODE", "DETECTED_AT")
    _insert(con, f"INSERT INTO CONTROL.\"RECONCILIATION_BREAKS\" VALUES ({', '.join('?' for _ in columns)})", [tuple(r[c] for c in columns) for r in rows])
    return con.execute(_duckdb_sql(render_bundle(_pack())[f"tests/{name}.sql"])).fetchall()


GOOD_CASH = dict(CHECK_NAME="cash_balance", SECURITY_ID=None, CURRENCY="USD", BALANCE_TYPE="SETTLED", PRIOR_VALUE=D("1000"), MOVEMENT=D("2100"), EXPECTED=D("3100"), ACTUAL=D("3000"), DIFFERENCE=D("-100"), REJECTION_CODE="CASH_BALANCE_MISMATCH")
UNVERIFIABLE = dict(CATEGORY="unverifiable", UNVERIFIABLE_COUNT=2, ACTUAL=D("100"), DIFFERENCE=D("-50"), EXPECTED=D("150"))


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("reconciliation_breaks_carry_their_checks_code", _break_row(REJECTION_CODE="CASH_BALANCE_MISMATCH")),
        ("reconciliation_breaks_are_well_formed", _break_row(CATEGORY="lost")),
        ("reconciliation_breaks_are_well_formed", _break_row(CHECK_NAME="reconciliation")),
        ("reconciliation_breaks_are_well_formed", _break_row(CATEGORY="unverifiable", UNVERIFIABLE_COUNT=0)),  # unverifiable with nothing to make it so
        ("reconciliation_breaks_are_well_formed", _break_row(UNVERIFIABLE_COUNT=3)),  # a mismatch that says it could not be evaluated
        ("reconciliation_breaks_are_well_formed", _break_row(SECURITY_ID=None)),  # a position break with no security
        ("reconciliation_breaks_are_well_formed", _break_row(**{**GOOD_CASH, "CURRENCY": None})),  # a cash break with no currency
        ("reconciliation_breaks_are_outside_their_tolerance", _break_row(ACTUAL=D("150"), DIFFERENCE=D("0"))),
        ("reconciliation_breaks_add_up", _break_row(EXPECTED=D("151"))),
        ("reconciliation_breaks_add_up", _break_row(DIFFERENCE=D("-9"))),
    ],
)
def test_each_invariant_test_returns_the_bad_row_it_exists_for(duck, name, bad):
    good = [_break_row(), _break_row(**GOOD_CASH), _break_row(ACCOUNT_NUMBER="A2", **UNVERIFIABLE)]
    assert _run_bundle_test(duck, name, good) == []
    assert len(_run_bundle_test(duck, name, good + [bad])) >= 1


def test_the_uniqueness_test_returns_a_finding_that_is_there_twice(duck):
    good = [_break_row(), _break_row(SECURITY_ID="S2"), _break_row(**GOOD_CASH), _break_row(**{**GOOD_CASH, "BALANCE_TYPE": "TRADE_DATE"})]
    assert _run_bundle_test(duck, "reconciliation_breaks_are_unique", good) == []
    assert len(_run_bundle_test(duck, "reconciliation_breaks_are_unique", good + [_break_row(RUN_ID="r2")])) == 1
    assert len(_run_bundle_test(duck, "reconciliation_breaks_are_unique", good + [_break_row(**{**GOOD_CASH, "RUN_ID": "r2"})])) == 1


def test_a_break_within_the_tolerance_of_a_looser_pack_would_be_returned_by_its_tolerance_test(duck):
    """The tolerance test is rendered from the pack's own tolerances: a break that a loose amount tolerance accepts is a bug in the findings."""
    loose = replace(_config(), tolerances={"amount": Tolerance("absolute", epsilon=D("150"))})
    pack = _pack()
    from astra_data.reconciliation import render_tests

    sql = _duckdb_sql(render_tests(replace(pack, reconciliation=loose))["reconciliation_breaks_are_outside_their_tolerance.sql"])
    _run_bundle_test(duck, "reconciliation_breaks_add_up", [_break_row(**GOOD_CASH)])  # a cash break off by 100
    assert len(duck.execute(sql).fetchall()) == 1  # inside a tolerance of 150
    assert _run_bundle_test(duck, "reconciliation_breaks_are_outside_their_tolerance", [_break_row(**GOOD_CASH)]) == []  # outside the pack's own (exact)
