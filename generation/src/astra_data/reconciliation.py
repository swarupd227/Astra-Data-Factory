"""Reconciliation of the canonical tables, rendered as a release bundle per domain pack (S7.1.5, ADR 0079).

From `domains/<pack>/reconciliation.yaml` the bundle renders:

  ddl/reconciliation.sql        CONTROL.RECONCILIATION_BREAKS, the current findings of each custodian and business
                                date, and CONTROL.RECONCILIATION_RUNS, the row counts of every run
  pipeline/reconcile.sql        CONTROL.RECONCILE(custodian, business date): checks the position identity and each
                                cash balance identity over SILVER, replaces that custodian-day's findings and logs the run
  tests/*.sql                   invariants over the findings that hold on real data: every break has the code of its
                                check and a valid category, is unique, lies outside its tolerance and is arithmetically
                                what it says

The checks are one SELECT each (`position_query`, `cash_query`), the same text whether the procedure runs it
with bind variables or a test runs it over seeded tables. They evaluate the identities the Python reference
implementation (`astra_knowledge.patterns.reconciliation`) defines, with the tolerance per field type the pack
declares, and the renderer's tests run both over the same seeded rows and compare what they find.

A break is not an exception row. Exceptions belong to one source's EXCEPTIONS table (S3.2.5) and leave NEW once
(S7.1.4); a reconciliation break compares two sources (the position file and the transaction file) and is found
again, or is gone, on the next run. So the findings of a custodian and day are replaced whole by each run, in one
transaction, and a fixed break simply stops being there. Each carries the taxonomy code of its check, so the
rejection taxonomy stays the one list of what can be wrong. Who works a break, and how, is a later story.

The bundle is committed under releases/<pack>-reconciliation and checked in CI, like the Gold, Silver and
exceptions bundles.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from astra_knowledge.cdm import DomainPack, load_packs
from astra_knowledge.patterns.reconciliation import CATEGORIES
from astra_knowledge.reconciliation import CHECK_CODES, CHECKS, UNRECONCILABLE, CashCheck, Reconciliation, Tolerance

from astra_core.problems import Problem

BUNDLE_SUFFIX = "-reconciliation"
DB = "{{ DATABASE }}"
CONTROL = f'{DB}."CONTROL"'
SILVER = f'{DB}."SILVER"'
BREAKS = f'{CONTROL}."RECONCILIATION_BREAKS"'
RUNS = f'{CONTROL}."RECONCILIATION_RUNS"'

# The columns every check yields, in order; the procedure inserts them into BREAKS after RUN_ID.
BREAK_COLUMNS = (
    "CHECK_NAME",
    "CATEGORY",
    "CUSTODIAN_ID",
    "ACCOUNT_NUMBER",
    "SECURITY_ID",
    "CURRENCY",
    "BALANCE_TYPE",
    "AS_OF_DATE",
    "PRIOR_DATE",
    "PRIOR_VALUE",
    "MOVEMENT",
    "EXPECTED",
    "ACTUAL",
    "DIFFERENCE",
    "UNVERIFIABLE_COUNT",
    "REJECTION_CODE",
)


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _number(value: Decimal) -> str:
    return format(value, "f")


def within(tolerance: Tolerance, expected: str, actual: str) -> str:
    """SQL that is true when `expected` and `actual` agree under the tolerance; `Tolerance.matches` in SQL."""
    if tolerance.kind == "decimal_places":
        return f"ROUND({expected}, {tolerance.places}) = ROUND({actual}, {tolerance.places})"
    if tolerance.kind == "absolute":
        return f"ABS({expected} - {actual}) <= {_number(tolerance.epsilon)}"
    if tolerance.kind == "relative":
        return f"ABS({expected} - {actual}) <= {_number(tolerance.epsilon)} * GREATEST(ABS({expected}), ABS({actual}))"
    return f"{expected} = {actual}"


# -- the checks: one SELECT each --------------------------------------------------


def _moved_on(kind: str) -> str:
    return _q("TRADE_DATE" if kind == "trade_date" else "SETTLE_DATE")


def _window(kind: str, as_of: str) -> tuple[str, str | None]:
    """Where a transaction is placed: dated after the snapshot up to the business date by the movement date, or -- only for a
    movement date that can be missing -- undated, with a trade date that says it could belong (None when it cannot be missing)."""
    moved = f"t.{_moved_on(kind)}"
    placed = f'{moved} > d."PRIOR_DATE" AND {moved} <= {as_of}'
    undated = None if kind == "trade_date" else f'{moved} IS NULL AND t."TRADE_DATE" > d."PRIOR_DATE" AND t."TRADE_DATE" <= {as_of}'
    return placed, undated


def position_query(config: Reconciliation, custodian: str, as_of: str) -> str:
    """The position identity for one custodian and business date. `custodian` and `as_of` are SQL expressions:
    the procedure's bind variables, or literals in a test."""
    position, transaction = f'{SILVER}."POSITION"', f'{SILVER}."TRANSACTION"'
    placed, undated = _window(config.movement_date, as_of)
    or_undated = f"({undated}) OR " if undated else ""
    in_window = f"(({placed}) OR ({undated}))" if undated else f"({placed})"
    types = ", ".join(f"({_lit(kind)}, {0 if move == UNRECONCILABLE else move}, {'TRUE' if move == UNRECONCILABLE else 'FALSE'})" for kind, move in config.movements.items())
    agree = within(config.tolerance("quantity"), '"EXPECTED"', 'COALESCE("ACTUAL", 0)')
    return f"""WITH "PRIOR_DATES" AS (
  SELECT "ACCOUNT_NUMBER", MAX("AS_OF_DATE") AS "PRIOR_DATE"
  FROM {position}
  WHERE "CUSTODIAN_ID" = {custodian} AND "AS_OF_DATE" < {as_of}
  GROUP BY "ACCOUNT_NUMBER"
), "PRIOR_ROWS" AS (
  SELECT p."ACCOUNT_NUMBER", p."SECURITY_ID", p."QUANTITY"
  FROM {position} p
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = p."ACCOUNT_NUMBER" AND d."PRIOR_DATE" = p."AS_OF_DATE"
  WHERE p."CUSTODIAN_ID" = {custodian}
), "CURRENT_ROWS" AS (
  SELECT p."ACCOUNT_NUMBER", p."SECURITY_ID", p."QUANTITY"
  FROM {position} p
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = p."ACCOUNT_NUMBER"
  WHERE p."CUSTODIAN_ID" = {custodian} AND p."AS_OF_DATE" = {as_of}
), "MOVES" AS (
  SELECT t."ACCOUNT_NUMBER", t."SECURITY_ID",
         SUM(CASE WHEN ({placed}) AND m."FACTOR" IN (1, -1) AND t."QUANTITY" IS NOT NULL THEN m."FACTOR" * ABS(t."QUANTITY") ELSE 0 END) AS "MOVEMENT",
         SUM(CASE WHEN {or_undated}m."TYPE" IS NULL OR m."UNRECONCILABLE" OR t."QUANTITY" IS NULL THEN 1 ELSE 0 END) AS "UNVERIFIABLE"
  FROM {transaction} t
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = t."ACCOUNT_NUMBER"
  LEFT JOIN (SELECT * FROM VALUES {types} AS v ("TYPE", "FACTOR", "UNRECONCILABLE")) m ON m."TYPE" = t."TRANSACTION_TYPE"
  WHERE t."CUSTODIAN_ID" = {custodian} AND t."STATUS" = 'ACTIVE' AND t."SECURITY_ID" IS NOT NULL
    AND (m."TYPE" IS NULL OR m."UNRECONCILABLE" OR m."FACTOR" <> 0)
    AND {in_window}
  GROUP BY t."ACCOUNT_NUMBER", t."SECURITY_ID"
), "KEYS" AS (
  SELECT "ACCOUNT_NUMBER", "SECURITY_ID" FROM "PRIOR_ROWS"
  UNION SELECT "ACCOUNT_NUMBER", "SECURITY_ID" FROM "CURRENT_ROWS"
  UNION SELECT "ACCOUNT_NUMBER", "SECURITY_ID" FROM "MOVES"
), "EVAL" AS (
  SELECT k."ACCOUNT_NUMBER", k."SECURITY_ID", d."PRIOR_DATE", pr."QUANTITY" AS "PRIOR_VALUE", cu."QUANTITY" AS "ACTUAL",
         COALESCE(mv."MOVEMENT", 0) AS "MOVEMENT", COALESCE(mv."UNVERIFIABLE", 0) AS "UNVERIFIABLE_COUNT",
         COALESCE(pr."QUANTITY", 0) + COALESCE(mv."MOVEMENT", 0) AS "EXPECTED"
  FROM "KEYS" k
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = k."ACCOUNT_NUMBER"
  LEFT JOIN "PRIOR_ROWS" pr ON pr."ACCOUNT_NUMBER" = k."ACCOUNT_NUMBER" AND pr."SECURITY_ID" = k."SECURITY_ID"
  LEFT JOIN "CURRENT_ROWS" cu ON cu."ACCOUNT_NUMBER" = k."ACCOUNT_NUMBER" AND cu."SECURITY_ID" = k."SECURITY_ID"
  LEFT JOIN "MOVES" mv ON mv."ACCOUNT_NUMBER" = k."ACCOUNT_NUMBER" AND mv."SECURITY_ID" = k."SECURITY_ID"
)
SELECT 'position_quantity' AS "CHECK_NAME",
       CASE WHEN "UNVERIFIABLE_COUNT" > 0 THEN 'unverifiable' WHEN "ACTUAL" IS NULL THEN 'disappeared' WHEN "PRIOR_VALUE" IS NULL THEN 'appeared' ELSE 'mismatch' END AS "CATEGORY",
       {custodian} AS "CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", CAST(NULL AS STRING) AS "CURRENCY", CAST(NULL AS STRING) AS "BALANCE_TYPE",
       {as_of} AS "AS_OF_DATE", "PRIOR_DATE", "PRIOR_VALUE", "MOVEMENT", "EXPECTED", "ACTUAL",
       COALESCE("ACTUAL", 0) - "EXPECTED" AS "DIFFERENCE", "UNVERIFIABLE_COUNT", {_lit(CHECK_CODES['position_quantity'])} AS "REJECTION_CODE"
FROM "EVAL"
WHERE "UNVERIFIABLE_COUNT" > 0 OR NOT ({agree})"""


def cash_query(config: Reconciliation, check: CashCheck, custodian: str, as_of: str) -> str:
    """The cash balance identity of one balance type for one custodian and business date."""
    balance, transaction = f'{SILVER}."CASH_BALANCE"', f'{SILVER}."TRANSACTION"'
    placed, undated = _window(check.movement_date, as_of)
    unverifiable = f'SUM(CASE WHEN ({undated}) THEN 1 ELSE 0 END)' if undated else "CAST(0 AS INTEGER)"
    in_window = f"(({placed}) OR ({undated}))" if undated else f"({placed})"
    kind = _lit(check.balance_type)
    agree = within(config.tolerance("amount"), '"EXPECTED"', 'COALESCE("ACTUAL", 0)')
    return f"""WITH "PRIOR_DATES" AS (
  SELECT "ACCOUNT_NUMBER", "CURRENCY", MAX("AS_OF_DATE") AS "PRIOR_DATE"
  FROM {balance}
  WHERE "CUSTODIAN_ID" = {custodian} AND "BALANCE_TYPE" = {kind} AND "AS_OF_DATE" < {as_of}
  GROUP BY "ACCOUNT_NUMBER", "CURRENCY"
), "PRIOR_ROWS" AS (
  SELECT b."ACCOUNT_NUMBER", b."CURRENCY", b."AMOUNT"
  FROM {balance} b
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = b."ACCOUNT_NUMBER" AND d."CURRENCY" = b."CURRENCY" AND d."PRIOR_DATE" = b."AS_OF_DATE"
  WHERE b."CUSTODIAN_ID" = {custodian} AND b."BALANCE_TYPE" = {kind}
), "CURRENT_ROWS" AS (
  SELECT b."ACCOUNT_NUMBER", b."CURRENCY", b."AMOUNT"
  FROM {balance} b
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = b."ACCOUNT_NUMBER" AND d."CURRENCY" = b."CURRENCY"
  WHERE b."CUSTODIAN_ID" = {custodian} AND b."BALANCE_TYPE" = {kind} AND b."AS_OF_DATE" = {as_of}
), "MOVES" AS (
  SELECT t."ACCOUNT_NUMBER", t."CURRENCY",
         SUM(CASE WHEN ({placed}) THEN t."NET_AMOUNT" ELSE 0 END) AS "MOVEMENT",
         {unverifiable} AS "UNVERIFIABLE"
  FROM {transaction} t
  JOIN "PRIOR_DATES" d ON d."ACCOUNT_NUMBER" = t."ACCOUNT_NUMBER" AND d."CURRENCY" = t."CURRENCY"
  WHERE t."CUSTODIAN_ID" = {custodian} AND t."STATUS" = 'ACTIVE' AND {in_window}
  GROUP BY t."ACCOUNT_NUMBER", t."CURRENCY"
), "EVAL" AS (
  SELECT d."ACCOUNT_NUMBER", d."CURRENCY", d."PRIOR_DATE", pr."AMOUNT" AS "PRIOR_VALUE", cu."AMOUNT" AS "ACTUAL",
         COALESCE(mv."MOVEMENT", 0) AS "MOVEMENT", COALESCE(mv."UNVERIFIABLE", 0) AS "UNVERIFIABLE_COUNT",
         pr."AMOUNT" + COALESCE(mv."MOVEMENT", 0) AS "EXPECTED"
  FROM "PRIOR_DATES" d
  JOIN "PRIOR_ROWS" pr ON pr."ACCOUNT_NUMBER" = d."ACCOUNT_NUMBER" AND pr."CURRENCY" = d."CURRENCY"
  LEFT JOIN "CURRENT_ROWS" cu ON cu."ACCOUNT_NUMBER" = d."ACCOUNT_NUMBER" AND cu."CURRENCY" = d."CURRENCY"
  LEFT JOIN "MOVES" mv ON mv."ACCOUNT_NUMBER" = d."ACCOUNT_NUMBER" AND mv."CURRENCY" = d."CURRENCY"
)
SELECT 'cash_balance' AS "CHECK_NAME",
       CASE WHEN "UNVERIFIABLE_COUNT" > 0 THEN 'unverifiable' WHEN "ACTUAL" IS NULL THEN 'disappeared' ELSE 'mismatch' END AS "CATEGORY",
       {custodian} AS "CUSTODIAN_ID", "ACCOUNT_NUMBER", CAST(NULL AS STRING) AS "SECURITY_ID", "CURRENCY", {kind} AS "BALANCE_TYPE",
       {as_of} AS "AS_OF_DATE", "PRIOR_DATE", "PRIOR_VALUE", "MOVEMENT", "EXPECTED", "ACTUAL",
       COALESCE("ACTUAL", 0) - "EXPECTED" AS "DIFFERENCE", "UNVERIFIABLE_COUNT", {_lit(CHECK_CODES['cash_balance'])} AS "REJECTION_CODE"
FROM "EVAL"
WHERE "UNVERIFIABLE_COUNT" > 0 OR NOT ({agree})"""


def break_query(config: Reconciliation, custodian: str, as_of: str) -> str:
    """Every check, one after the other, as one SELECT with BREAK_COLUMNS."""
    queries = [position_query(config, custodian, as_of)] + [cash_query(config, check, custodian, as_of) for check in config.cash]
    return "\nUNION ALL\n".join(f"SELECT * FROM (\n{query}\n)" for query in queries)


# -- DDL --------------------------------------------------------------------------


def render_tables(pack: DomainPack) -> str:
    return "\n".join(
        [
            f"-- Reconciliation of the {pack.name} domain pack's canonical tables: what CONTROL.RECONCILE found for each custodian and",
            "-- business date, replaced whole by each run, and a log of every run with its row counts. Rendered by astra-data reconciliation render.",
            "",
            f"CREATE ICEBERG TABLE IF NOT EXISTS {BREAKS} (",
            "  \"RUN_ID\"             STRING NOT NULL COMMENT 'The run that found it; see CONTROL.RECONCILIATION_RUNS',",
            f"  \"CHECK_NAME\"         STRING NOT NULL COMMENT {_lit('The identity that failed: ' + ', '.join(CHECKS))},",
            f"  \"CATEGORY\"           STRING NOT NULL COMMENT {_lit('What kind of break: ' + ', '.join(CATEGORIES))},",
            "  \"CUSTODIAN_ID\"       STRING NOT NULL,",
            "  \"ACCOUNT_NUMBER\"     STRING NOT NULL COMMENT 'Account the position or balance is held in',",
            "  \"SECURITY_ID\"        STRING COMMENT 'The security, for a position break',",
            "  \"CURRENCY\"           STRING COMMENT 'The currency, for a cash break',",
            "  \"BALANCE_TYPE\"       STRING COMMENT 'The balance type, for a cash break',",
            "  \"AS_OF_DATE\"         DATE NOT NULL COMMENT 'The business date that was checked',",
            "  \"PRIOR_DATE\"         DATE NOT NULL COMMENT 'The earlier snapshot it was checked against',",
            "  \"PRIOR_VALUE\"        NUMBER(38,8) COMMENT 'Quantity or amount in that snapshot; null if it was not held',",
            "  \"MOVEMENT\"           NUMBER(38,8) NOT NULL COMMENT 'What the transactions between the two dates moved it by',",
            "  \"EXPECTED\"           NUMBER(38,8) NOT NULL COMMENT 'Prior value plus movement',",
            "  \"ACTUAL\"             NUMBER(38,8) COMMENT 'What the business date reports; null if it is not there',",
            "  \"DIFFERENCE\"         NUMBER(38,8) NOT NULL COMMENT 'Actual (zero if absent) minus expected',",
            "  \"UNVERIFIABLE_COUNT\" NUMBER(18,0) NOT NULL COMMENT 'Transactions that made the identity impossible to evaluate; above zero exactly when the category is unverifiable',",
            "  \"REJECTION_CODE\"     STRING NOT NULL COMMENT 'The taxonomy code of the check; see CONTROL.REJECTION_CODES',",
            "  \"DETECTED_AT\"        TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the run found it'",
            ")",
            "BASE_LOCATION = 'control/reconciliation_breaks/'",
            "COMMENT = 'The current reconciliation findings of each custodian and business date. Replaced whole by each CONTROL.RECONCILE run. Rendered by astra-data reconciliation render.';",
            f'ALTER ICEBERG TABLE {BREAKS} MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {CONTROL}."PII" = \'account_number\';',
            "",
            "-- Every run: for which custodian and business date, how many positions and balances were in scope, how many breaks it found.",
            f"CREATE ICEBERG TABLE IF NOT EXISTS {RUNS} (",
            '  "RUN_ID"                STRING NOT NULL,',
            '  "CUSTODIAN_ID"          STRING NOT NULL,',
            '  "AS_OF_DATE"            DATE NOT NULL,',
            '  "STARTED_AT"            TIMESTAMP_NTZ(6) NOT NULL,',
            '  "FINISHED_AT"           TIMESTAMP_NTZ(6) NOT NULL,',
            "  \"POSITIONS_IN_SCOPE\"   NUMBER(18,0) NOT NULL COMMENT 'Position rows of the business date',",
            "  \"BALANCES_IN_SCOPE\"    NUMBER(18,0) NOT NULL COMMENT 'Cash balance rows of the business date',",
            "  \"BREAKS_FOUND\"         NUMBER(18,0) NOT NULL COMMENT 'Rows written to CONTROL.RECONCILIATION_BREAKS, unverifiable ones included'",
            ")",
            "BASE_LOCATION = 'control/reconciliation_runs/'",
            "COMMENT = 'Every CONTROL.RECONCILE run with its row counts. Rendered by astra-data reconciliation render.';",
            "",
        ]
    )


# -- the procedure ------------------------------------------------------------------


def render_procedure(pack: DomainPack) -> str:
    config = pack.reconciliation
    columns = ", ".join(_q(c) for c in ("RUN_ID",) + BREAK_COLUMNS + ("DETECTED_AT",))
    projection = ", ".join(f"b.{_q(c)}" for c in BREAK_COLUMNS)
    query = break_query(config, ":CUSTODIAN_ID", ":AS_OF_DATE")
    indented = "\n".join(f"    {line}" if line else line for line in query.splitlines())
    return f"""-- Reconcile one custodian's business date: the position identity (prior snapshot + the transactions since = this snapshot) and
-- each cash balance identity ({", ".join(c.balance_type for c in config.cash)}), with the tolerance per field type the pack declares. What it finds
-- replaces what an earlier run found for the same custodian and date, in one transaction, so a break that was fixed is gone; the run and its
-- row counts are logged. Rendered by astra-data reconciliation render.
CREATE OR REPLACE PROCEDURE {CONTROL}."RECONCILE"("CUSTODIAN_ID" STRING, "AS_OF_DATE" DATE)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Reconciles the positions and cash balances of a custodian and business date against the transactions between snapshots. Rendered by astra-data reconciliation render.'
AS
$$
DECLARE
  run_id STRING DEFAULT UUID_STRING();
  started_at TIMESTAMP_NTZ(6) DEFAULT SYSDATE();
  positions INTEGER DEFAULT 0;
  balances INTEGER DEFAULT 0;
  found INTEGER DEFAULT 0;
BEGIN
  positions := (SELECT COUNT(*) FROM {SILVER}."POSITION" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "AS_OF_DATE" = :AS_OF_DATE);
  balances := (SELECT COUNT(*) FROM {SILVER}."CASH_BALANCE" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "AS_OF_DATE" = :AS_OF_DATE);

  BEGIN TRANSACTION;
  DELETE FROM {BREAKS} WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "AS_OF_DATE" = :AS_OF_DATE;
  INSERT INTO {BREAKS} ({columns})
  SELECT :run_id, {projection}, SYSDATE()
  FROM (
{indented}
  ) b;
  found := SQLROWCOUNT;
  INSERT INTO {RUNS} ("RUN_ID", "CUSTODIAN_ID", "AS_OF_DATE", "STARTED_AT", "FINISHED_AT", "POSITIONS_IN_SCOPE", "BALANCES_IN_SCOPE", "BREAKS_FOUND")
  SELECT :run_id, :CUSTODIAN_ID, :AS_OF_DATE, :started_at, SYSDATE(), :positions, :balances, :found;
  COMMIT;
  RETURN found || ' break(s) for ' || CUSTODIAN_ID || ' on ' || TO_VARCHAR(AS_OF_DATE) || ' (run ' || run_id || ')';
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
"""


# -- tests ------------------------------------------------------------------------


def render_tests(pack: DomainPack) -> dict[str, str]:
    config = pack.reconciliation
    codes = ", ".join(f"({_lit(check)}, {_lit(code)})" for check, code in CHECK_CODES.items())
    expected, actual = '"EXPECTED"', 'COALESCE("ACTUAL", 0)'
    quantity_agrees = within(config.tolerance("quantity"), expected, actual)
    amount_agrees = within(config.tolerance("amount"), expected, actual)
    in_tolerance = f'CASE WHEN "CHECK_NAME" = \'position_quantity\' THEN ({quantity_agrees}) WHEN "CHECK_NAME" = \'cash_balance\' THEN ({amount_agrees}) ELSE FALSE END'
    return {
        "reconciliation_breaks_carry_their_checks_code.sql": "\n".join(
            [
                "-- Every break carries the taxonomy code of the check that found it. Returns breaks that carry another.",
                'SELECT b."RUN_ID", b."CHECK_NAME", b."REJECTION_CODE"',
                f"FROM {BREAKS} b",
                f'LEFT JOIN (SELECT * FROM VALUES {codes} AS c ("CHECK_NAME", "REJECTION_CODE")) c ON c."CHECK_NAME" = b."CHECK_NAME" AND c."REJECTION_CODE" = b."REJECTION_CODE"',
                'WHERE c."CHECK_NAME" IS NULL;',
                "",
            ]
        ),
        "reconciliation_breaks_are_well_formed.sql": "\n".join(
            [
                "-- A break names a known check and category, and is unverifiable exactly when transactions made it impossible to evaluate.",
                "-- Returns breaks that do not.",
                'SELECT "RUN_ID", "CHECK_NAME", "CATEGORY", "ACCOUNT_NUMBER"',
                f"FROM {BREAKS}",
                f"WHERE \"CHECK_NAME\" NOT IN ({', '.join(_lit(c) for c in CHECKS)})",
                f"   OR \"CATEGORY\" NOT IN ({', '.join(_lit(c) for c in CATEGORIES)})",
                "   OR (\"CATEGORY\" = 'unverifiable') <> (\"UNVERIFIABLE_COUNT\" > 0)",
                "   OR (\"CHECK_NAME\" = 'position_quantity' AND \"SECURITY_ID\" IS NULL)",
                "   OR (\"CHECK_NAME\" = 'cash_balance' AND (\"CURRENCY\" IS NULL OR \"BALANCE_TYPE\" IS NULL));",
                "",
            ]
        ),
        "reconciliation_breaks_are_unique.sql": "\n".join(
            [
                "-- One finding per check, account, security or currency and balance type, custodian and business date. Returns those with more than one.",
                'SELECT "CHECK_NAME", "CUSTODIAN_ID", "ACCOUNT_NUMBER", COALESCE("SECURITY_ID", \'\') AS "SECURITY_ID", COALESCE("CURRENCY", \'\') AS "CURRENCY", COALESCE("BALANCE_TYPE", \'\') AS "BALANCE_TYPE", "AS_OF_DATE", COUNT(*) AS ROW_COUNT',
                f"FROM {BREAKS}",
                'GROUP BY "CHECK_NAME", "CUSTODIAN_ID", "ACCOUNT_NUMBER", COALESCE("SECURITY_ID", \'\'), COALESCE("CURRENCY", \'\'), COALESCE("BALANCE_TYPE", \'\'), "AS_OF_DATE"',
                "HAVING COUNT(*) > 1;",
                "",
            ]
        ),
        "reconciliation_breaks_are_outside_their_tolerance.sql": "\n".join(
            [
                "-- A break that can be evaluated differs by more than the tolerance of its field type. Returns breaks whose values agree.",
                'SELECT "RUN_ID", "CHECK_NAME", "ACCOUNT_NUMBER", "EXPECTED", "ACTUAL"',
                f"FROM {BREAKS}",
                f"WHERE \"CATEGORY\" <> 'unverifiable' AND {in_tolerance};",
                "",
            ]
        ),
        "reconciliation_breaks_add_up.sql": "\n".join(
            [
                "-- Expected is the prior value plus the movement, and the difference is actual minus expected. Returns breaks whose arithmetic is wrong.",
                'SELECT "RUN_ID", "CHECK_NAME", "ACCOUNT_NUMBER", "EXPECTED", "DIFFERENCE"',
                f"FROM {BREAKS}",
                'WHERE "EXPECTED" <> COALESCE("PRIOR_VALUE", 0) + "MOVEMENT" OR "DIFFERENCE" <> COALESCE("ACTUAL", 0) - "EXPECTED";',
                "",
            ]
        ),
    }


# -- the pack and the bundle ----------------------------------------------------------


def packs_with_reconciliation(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[DomainPack], list[Problem]]:
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    return [p for p in packs if p.reconciliation is not None and (domain is None or p.name == domain)], []


def bundle_name(pack: DomainPack) -> str:
    return f"{pack.name.replace('_', '-')}{BUNDLE_SUFFIX}"


def render_bundle(pack: DomainPack) -> dict[str, str]:
    """Every file of the pack's reconciliation bundle, keyed by path relative to the bundle directory."""
    if pack.reconciliation is None:
        raise ValueError(f"domain pack {pack.name} declares no reconciliation")
    files: dict[str, str] = {
        "ddl/reconciliation.sql": render_tables(pack),
        "pipeline/reconcile.sql": render_procedure(pack),
    }
    files.update({f"tests/{name}": text for name, text in render_tests(pack).items()})
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    manifest = [
        f"# Reconciliation of the {pack.name} domain pack's canonical tables: the findings, the procedure that produces them and the",
        f"# invariants they must satisfy. Rendered by astra-data reconciliation render from domains/{pack.name}/reconciliation.yaml; the",
        "# version is a digest of the rendered files. Do not edit.",
        f"bundle: {bundle_name(pack)}",
        f'version: "{digest.hexdigest()[:12]}"',
        f"source: {pack.name}_reconciliation",
        "steps:",
        "  - ddl/reconciliation.sql",
        "  - pipeline/reconcile.sql",
        "tests:",
        "  - tests/*.sql",
        "",
    ]
    files["manifest.yaml"] = "\n".join(manifest)
    return files


def write_bundle(pack: DomainPack, releases_dir: Path) -> Path:
    """Write the bundle, removing files it no longer produces. Returns the bundle directory."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                existing.unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def check_bundle(pack: DomainPack, releases_dir: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every bundle file that is missing, stale or no longer produced."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    problems: list[Problem] = []
    for name, text in files.items():
        path = root / name
        if not path.is_file():
            problems.append(Problem(_display(path, repo_root), None, "not rendered; run astra-data reconciliation render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the reconciliation file or the model changed since it was rendered; run astra-data reconciliation render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data reconciliation render to remove it"))
    return problems


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
