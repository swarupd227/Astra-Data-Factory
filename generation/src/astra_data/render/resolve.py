"""Resolution for one source: the Silver logical record becomes canonical rows with platform identifiers.

Set-based joins against the replicated reference data (ADR 0015), never a
call to a source system:

  account           the custodian's account number joins the account
                    cross-reference; no row is the not-found code, a closed
                    account the closed code
  security          the configured identifiers are tried in order against
                    the security master's identifier view; exactly one match
                    resolves, several is ambiguous, none is not found
  transaction code  the custodian's code maps to the canonical type through
                    the config's map; a code the map lacks raises the
                    configured code
  price             a missing (or every) price is taken from SILVER.PRICE for
                    the security within the lookback; none raises the
                    configured code

Every failure is an exception row with the rejected row as payload; a
row with an error-severity exception is held back, a row with only
warnings (an inactive security) is projected and flagged. Rows that
resolve are merged into the canonical entity on its key. The reference
implementation is the pattern library's Replica.resolve
(patterns/reference_data.py), which orders identifiers the same way.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from astra_knowledge.cdm import Column, Entity

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig, CompiledMapping, provided_columns
from astra_data.render.names import BRONZE, EXCEPTIONS, REFERENCE, SILVER, exceptions_table, lit, procedure, q, silver_table


def problems(compiled: CompiledConfig) -> list[Problem]:
    """A source that maps nothing has no resolve stage; a source that maps needs its merge (reported by the merge renderer)."""
    return []


def _constant_sql(column: Column, value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return f"{value}::{column.sql_type}"
    if isinstance(value, date):
        return f"TO_DATE({lit(value.isoformat())}, 'YYYY-MM-DD')"
    return f"{lit(str(value))}::{column.sql_type}"


def _transform_sql(mapping: CompiledMapping, expr: str) -> str:
    """Transforms at projection. Implied decimals were realised by the parse from the spec's picture, so they pass through."""
    if mapping.transform is None:
        return expr
    name = mapping.transform.transform.name
    args = mapping.transform.arguments
    if name in ("signed_implied_decimal", "implied_decimal"):
        return expr
    if name == "negate":
        return f"-({expr})"
    if name == "trim":
        return f"TRIM({expr})"
    if name == "upper":
        return f"UPPER({expr})"
    if name == "to_date":
        return f"TRY_TO_DATE({expr}::STRING, {lit(str(args[0]))})"
    if name == "nullif":
        return f"NULLIF({expr}::STRING, {lit(str(args[0]))})"
    return expr


def _mapped_sql(compiled: CompiledConfig, mapping: CompiledMapping) -> str:
    if mapping.source is None:
        return _constant_sql(mapping.column, mapping.constant)
    return _transform_sql(mapping, f"s.{q(mapping.source.name.upper())}")


def render_resolve(compiled: CompiledConfig) -> str:
    entity: Entity = compiled.target_entity
    model = compiled.model
    res = compiled.resolution
    source = compiled.id
    custodian = lit(compiled.source["custodian"])
    silver = f"{SILVER}.{q(silver_table(compiled))}"
    canonical = f"{SILVER}.{q(entity.table)}"
    exceptions = f"{EXCEPTIONS}.{q(exceptions_table(compiled))}"
    work = f"{BRONZE}.{q(source.upper() + '_RESOLVED')}"
    config_version = lit(compiled.provenance["config"]["sha256"][:12])
    merge_keys = [k.upper() for k in compiled.spec.merge.keys]
    key_text = " || ', ' || ".join(f"'{k.lower()}=' || COALESCE(s.{q(k)}::STRING, 'NULL')" for k in merge_keys)
    provided = provided_columns(entity, model, res)

    # -- the joins -----------------------------------------------------------
    joins: list[str] = []
    selects: list[str] = ['s.*']
    if res.account:
        xref = f"{REFERENCE}.{q(res.account.feed.table)}"
        joins.append(f'LEFT JOIN {xref} a ON a."CUSTODIAN_ID" = {custodian} AND a."CUSTODIAN_ACCOUNT_NUMBER" = s.{q(res.account.source.name.upper())}')
        selects += ['a."ACCOUNT_ID" AS "R_ACCOUNT_ID"', 'a."FIRM_ID" AS "R_FIRM_ID"', 'a."STATUS" AS "R_ACCOUNT_STATUS"']
    if res.security:
        identifiers = f"{REFERENCE}.{q(res.security.feed.table + '_IDENTIFIERS')}"
        master = f"{REFERENCE}.{q(res.security.feed.table)}"
        status_cases: list[str] = []
        id_cases: list[str] = []
        for i, lookup in enumerate(res.security.by, start=1):
            alias = f"i{i}"
            joins.append(
                f'LEFT JOIN (SELECT "IDENTIFIER_VALUE", MIN("SECURITY_ID") AS "SECURITY_ID", COUNT(*) AS "MATCHES" FROM {identifiers} WHERE "IDENTIFIER_TYPE" = {lit(lookup.identifier)} GROUP BY "IDENTIFIER_VALUE") {alias} '
                f'ON {alias}."IDENTIFIER_VALUE" = s.{q(lookup.source.name.upper())}'
            )
            status_cases.append(f"WHEN {alias}.\"MATCHES\" > 1 THEN 'ambiguous' WHEN {alias}.\"MATCHES\" = 1 THEN 'found'")
            id_cases.append(f"WHEN {alias}.\"MATCHES\" = 1 THEN {alias}.\"SECURITY_ID\"")
        selects.append("CASE " + " ".join(status_cases) + " ELSE 'not_found' END AS \"R_SECURITY_STATUS\"")
        selects.append("CASE " + " ".join(id_cases) + " END AS \"R_SECURITY_ID\"")
        selects.append(f"COALESCE({', '.join(f's.{q(l.source.name.upper())}' for l in res.security.by)}) AS \"R_CUSTODIAN_SECURITY_ID\"")
        selects.append(f'(SELECT m."STATUS" FROM {master} m WHERE m."SECURITY_ID" = CASE {" ".join(id_cases)} END) AS "R_SECURITY_MASTER_STATUS"')
    if res.transaction_code:
        rows = ", ".join(f"({lit(code)}, {lit(kind)})" for code, kind in res.transaction_code.map.items())
        joins.append(f'LEFT JOIN (SELECT * FROM VALUES {rows} AS v ("CODE", "TYPE")) tc ON tc."CODE" = s.{q(res.transaction_code.source.name.upper())}')
        selects.append('tc."TYPE" AS "R_TRANSACTION_TYPE"')
    if res.price and entity.column("PRICE") is not None:
        price_source = next((m for m in compiled.mappings if m.column.name == "PRICE"), None)
        as_of = next((m for m in compiled.mappings if m.column.name in ("AS_OF_DATE", "TRADE_DATE", "PRICE_DATE")), None)
        as_of_sql = f"s.{q(as_of.source.name.upper())}" if as_of and as_of.source else "CURRENT_DATE()"
        security_id = '"R_SECURITY_ID"' if res.security else "NULL"
        selects.append(f"{as_of_sql} AS \"R_PRICE_AS_OF\"")
        selects.append((_mapped_sql(compiled, price_source) if price_source else "NULL") + ' AS "R_SOURCE_PRICE"')
    select_sql = ",\n         ".join(selects)

    # -- exceptions ----------------------------------------------------------
    def exception(code: str, level: str, message: str, condition: str, field: str = "NULL") -> str:
        return (
            f'  INSERT INTO {exceptions} ("EXCEPTION_ID", "REJECTION_CODE", "LEVEL", "STAGE", "ENTITY", "CUSTODIAN_ID", "FIELD_NAME", "MESSAGE", "RECORD_KEY", "PAYLOAD", "RAISED_AT", "STATUS", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "RUN_ID", "LOADED_AT")\n'
            f"  SELECT UUID_STRING(), {lit(code)}, {lit(level)}, 'resolution', {lit(entity.name)}, {custodian}, {field}, {message}, {key_text}, TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)), SYSDATE(), 'OPEN', {custodian}, s.\"LAST_FILE\", s.\"LAST_LINE\", {config_version}, :RUN_ID, SYSDATE()\n"
            f"  FROM {work} s WHERE {condition};"
        )

    exception_blocks: list[str] = []
    holds: list[str] = []
    if res.account:
        acct = q(res.account.source.name.upper())
        exception_blocks.append(exception(res.account.not_found, "record", f"'account ' || COALESCE(s.{acct}::STRING, 'NULL') || ' is not in the account cross-reference'", 's."R_ACCOUNT_ID" IS NULL', lit(res.account.source.name)))
        holds.append('s."R_ACCOUNT_ID" IS NULL')
        if res.account.require_open:
            exception_blocks.append(exception(res.account.closed, "record", f"'account ' || s.{acct}::STRING || ' is closed in the account system'", "s.\"R_ACCOUNT_STATUS\" = 'CLOSED'", lit(res.account.source.name)))
            holds.append("s.\"R_ACCOUNT_STATUS\" = 'CLOSED'")
    if res.security:
        tried = ", ".join(f"{l.identifier} ' || COALESCE(s.{q(l.source.name.upper())}::STRING, 'NULL') || '" for l in res.security.by)
        exception_blocks.append(exception(res.security.not_found, "record", f"'no security in the security master matches {tried}'", "s.\"R_SECURITY_STATUS\" = 'not_found'"))
        exception_blocks.append(exception(res.security.ambiguous, "record", f"'more than one security in the security master matches {tried}'", "s.\"R_SECURITY_STATUS\" = 'ambiguous'"))
        holds += ["s.\"R_SECURITY_STATUS\" = 'not_found'", "s.\"R_SECURITY_STATUS\" = 'ambiguous'"]
        exception_blocks.append(exception(res.security.inactive, "record", "'security ' || s.\"R_SECURITY_ID\" || ' is inactive in the security master'", "s.\"R_SECURITY_STATUS\" = 'found' AND s.\"R_SECURITY_MASTER_STATUS\" = 'INACTIVE'"))
        if res.security.require_active:
            holds.append("(s.\"R_SECURITY_STATUS\" = 'found' AND s.\"R_SECURITY_MASTER_STATUS\" = 'INACTIVE')")
    if res.transaction_code:
        code_col = q(res.transaction_code.source.name.upper())
        exception_blocks.append(exception(res.transaction_code.unmapped, "record", f"'transaction code ' || COALESCE(s.{code_col}::STRING, 'NULL') || ' has no mapping to a canonical transaction type'", 's."R_TRANSACTION_TYPE" IS NULL', lit(res.transaction_code.source.name)))
        holds.append('s."R_TRANSACTION_TYPE" IS NULL')
    price_sql = None
    if res.price and entity.column("PRICE") is not None:
        lookback = res.price.lookback_days
        looked_up = (
            f'(SELECT p."PRICE" FROM {SILVER}."PRICE" p WHERE p."SECURITY_ID" = s."R_SECURITY_ID" AND p."PRICE_TYPE" = {lit(res.price.price_type)} '
            f'AND p."PRICE_DATE" <= s."R_PRICE_AS_OF" AND p."PRICE_DATE" >= DATEADD(\'day\', -{lookback}, s."R_PRICE_AS_OF") '
            f'ORDER BY p."PRICE_DATE" DESC LIMIT 1)'
        )
        price_sql = looked_up if res.price.when == "always" else f'COALESCE(s."R_SOURCE_PRICE", {looked_up})'
        exception_blocks.append(exception(res.price.missing, "record", f"'no price for security ' || COALESCE(s.\"R_SECURITY_ID\", 'NULL') || ' within {lookback} days of ' || s.\"R_PRICE_AS_OF\"::STRING", f"s.\"R_PRICE\" IS NULL"))
        holds.append('s."R_PRICE" IS NULL')
    hold_sql = " OR ".join(holds) if holds else "FALSE"

    # -- the projection ------------------------------------------------------
    values: dict[str, str] = {}
    for mapping in compiled.mappings:
        values[mapping.column.name] = _mapped_sql(compiled, mapping)
    if res.price and entity.column("PRICE") is not None:
        values["PRICE"] = 's."R_PRICE"'
    if res.account:
        values.setdefault("ACCOUNT_ID", 's."R_ACCOUNT_ID"')
        values.setdefault("FIRM_ID", 's."R_FIRM_ID"')
    if res.security:
        values.setdefault("SECURITY_ID", 's."R_SECURITY_ID"')
        values.setdefault("CUSTODIAN_SECURITY_ID", 's."R_CUSTODIAN_SECURITY_ID"')
    if res.transaction_code:
        values.setdefault("TRANSACTION_TYPE", 's."R_TRANSACTION_TYPE"')
        values.setdefault("CUSTODIAN_TRANSACTION_CODE", f's.{q(res.transaction_code.source.name.upper())}')
    values.setdefault("CUSTODIAN_ID", custodian)
    values["SOURCE_SYSTEM"] = custodian
    values["SOURCE_FILE"] = 's."LAST_FILE"'
    values["SOURCE_LINE"] = 's."LAST_LINE"'
    values["CONFIG_VERSION"] = config_version
    columns = [c.name for c in model.table_columns(entity) if c.name in values]
    keys = list(entity.key)
    key_join = " AND ".join(f"t.{q(k)} = r.{q(k)}" for k in keys)
    updates = ", ".join(f"{q(c)} = r.{q(c)}" for c in columns if c not in keys and c != "LOADED_AT")
    projection = ",\n         ".join(f"{values[c]} AS {q(c)}" for c in columns)
    price_column = f',\n         {price_sql} AS "R_PRICE"' if price_sql else ""

    return f"""-- Resolve {source}: this run's active Silver rows become {entity.name} rows with platform identifiers, by joining
-- the replicated reference data; what cannot be resolved is an exception with the configured code. Rendered by astra-data render.
CREATE OR REPLACE PROCEDURE {BRONZE}.{q(procedure(compiled, "RESOLVE"))}(RUN_ID STRING)
RETURNS INTEGER
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Resolves {source} into {entity.table}: {", ".join(k for k, v in (("account", res.account), ("security", res.security), ("transaction code", res.transaction_code), ("price", res.price)) if v) or "no resolution configured"}; exceptions to {exceptions_table(compiled)}.'
AS
$$
DECLARE
  projected INTEGER DEFAULT 0;
BEGIN
  -- 1. The rows this run merged, with what the reference data says about them.
  CREATE OR REPLACE TEMPORARY TABLE {work} AS
  SELECT {select_sql}
  FROM {silver} s
  {chr(10).join('  ' + j for j in joins)}
  WHERE s."RUN_ID" = :RUN_ID AND s."RETIRED_AT" IS NULL;
{f'''  ALTER TABLE {work} ADD COLUMN "R_PRICE" {entity.column("PRICE").sql_type};
  UPDATE {work} s SET "R_PRICE" = {price_sql};''' if price_sql else ''}

  -- 2. Exceptions, one per failed resolution, with the row as payload.
{chr(10).join(exception_blocks)}

  -- 3. Rows with an error-severity exception are held back; the rest are merged into the canonical entity on its key.
  MERGE INTO {canonical} t
  USING (
    SELECT {projection}
    FROM {work} s
    WHERE NOT ({hold_sql})
  ) r ON {key_join}
  WHEN MATCHED THEN UPDATE SET {updates}, "UPDATED_AT" = SYSDATE()
  WHEN NOT MATCHED THEN INSERT ({", ".join(q(c) for c in columns)}, "LOADED_AT") VALUES ({", ".join(f'r.{q(c)}' for c in columns)}, SYSDATE());
  projected := SQLROWCOUNT;
  RETURN projected;
END;
$$;
"""


def render(compiled: CompiledConfig) -> dict[str, str]:
    if not compiled.mappings:
        return {}
    return {f"pipeline/{compiled.id}_resolve.sql": render_resolve(compiled)}
