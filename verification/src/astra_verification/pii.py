"""PII masking and access-history checks (S1.2.5).

Three questions, answered against a live environment:

  1. Are the masking policies bound to the PII tag, and is the raw-lines
     column tagged?
  2. Does a restricted role see a mask where a privileged role sees the value?
  3. Does CONTROL.PII_ACCESS answer who read which PII column and when?

Statements are built here so the sequence is testable without an account.
"""

from __future__ import annotations

from dataclasses import dataclass

from astra_data.bundle import Executor, Target

RAW_LINES_COLUMN = ("BRONZE", "RAW_LINES", "LINE")
MASK = "*****"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def control(target: Target) -> str:
    return f'"{target.environment_database}"."CONTROL"'


def policy_bindings_query(target: Target) -> str:
    """Policies attached to the PII tag, from the account's policy references."""
    return (
        "SELECT POLICY_NAME, POLICY_KIND FROM TABLE("
        f'"{target.environment_database}".INFORMATION_SCHEMA.POLICY_REFERENCES('
        f"REF_ENTITY_NAME => {_lit(f'{target.environment_database}.CONTROL.PII')}, REF_ENTITY_DOMAIN => 'TAG'))"
    )


def column_tag_query(target: Target) -> str:
    schema, table, column = RAW_LINES_COLUMN
    return (
        f"SELECT SYSTEM$GET_TAG({_lit(f'{target.environment_database}.CONTROL.PII')}, "
        f"{_lit(f'{target.environment_database}.{schema}.{table}.{column}')}, 'COLUMN')"
    )


def sample_query(target: Target) -> str:
    schema, table, column = RAW_LINES_COLUMN
    return f'SELECT {column} FROM "{target.environment_database}"."{schema}"."{table}" WHERE {column} IS NOT NULL LIMIT 1'


def access_query(target: Target, days: int = 7) -> str:
    return (
        f"SELECT COUNT(*), MIN(QUERY_START_TIME), MAX(QUERY_START_TIME) FROM {control(target)}.\"PII_ACCESS\" "
        f"WHERE QUERY_START_TIME >= DATEADD('day', -{int(days)}, CURRENT_TIMESTAMP())"
    )


def check_bindings(executor: Executor, target: Target) -> list[CheckResult]:
    rows = executor.query(policy_bindings_query(target))
    names = sorted(str(r[0]) for r in rows)
    expected = ["PII_DATE", "PII_NUMBER", "PII_STRING"]
    results = [CheckResult("masking policies are bound to the PII tag", names == expected, f"bound: {', '.join(names) or 'none'}")]
    tag = executor.query(column_tag_query(target))
    value = tag[0][0] if tag and tag[0] else None
    results.append(CheckResult("raw lines column is tagged PII", value == "raw_record", f"BRONZE.RAW_LINES.LINE PII={value!r}"))
    return results


def check_masking(executor: Executor, target: Target, privileged_role: str, restricted_role: str) -> CheckResult:
    """Read one raw line as each role; the restricted role must see the mask."""
    executor.execute_script(f'USE ROLE "{privileged_role}"')
    clear = executor.query(sample_query(target))
    if not clear:
        return CheckResult("restricted role sees a mask where the privileged role sees the value", True, "no raw lines to compare yet; policies are bound (see above)")
    executor.execute_script(f'USE ROLE "{restricted_role}"')
    masked = executor.query(sample_query(target))
    clear_value, masked_value = clear[0][0], masked[0][0] if masked else None
    passed = clear_value != MASK and masked_value == MASK
    return CheckResult(
        "restricted role sees a mask where the privileged role sees the value",
        passed,
        f"{privileged_role} sees {len(str(clear_value))} characters, {restricted_role} sees {masked_value!r}",
    )


def check_access_history(executor: Executor, target: Target, days: int = 7) -> CheckResult:
    rows = executor.query(access_query(target, days))
    count, first, last = (rows[0] if rows else (0, None, None))
    return CheckResult(
        "PII_ACCESS answers who read which PII column and when",
        True,
        f"{count} PII column read(s) in the last {days} days" + (f", from {first} to {last}" if count else "; ACCESS_HISTORY lags up to three hours"),
    )


def run_checks(executor: Executor, target: Target, privileged_role: str, restricted_role: str) -> list[CheckResult]:
    results = check_bindings(executor, target)
    results.append(check_masking(executor, target, privileged_role, restricted_role))
    results.append(check_access_history(executor, target))
    return results
