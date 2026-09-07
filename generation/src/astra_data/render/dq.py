"""Data metric functions for one source: one per dq_rule, plus what the spec alone can measure.

Every dq_rule of the config is one DMF or one DMF association (ADR 0025):
a rule a system function can measure is attached as that function on the
rule's column; any other rule is a custom DMF rendered here and attached
to the rule's table. Custom DMFs return a number that is zero when the
rule holds: the control total returns the gap between the trailer value
and the file's records; the others return the count of rows that fail.
Rows that fail are listed by the rendered tests (tests/<source>_dq_*.sql).

The spec alone gives row counts, nulls in required fields and duplicates
of a single-column merge key; those are attached too, before the rules.
"""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.dq import CompiledDqRule
from astra_data.render.names import BRONZE, CONTROL, SILVER, file_metadata_table, lit, q, record_table, silver_table


def dmf_name(compiled: CompiledConfig, rule: CompiledDqRule) -> str:
    return f"{compiled.id}_{rule.id}".upper()


def rule_table(compiled: CompiledConfig, rule: CompiledDqRule) -> tuple[str, str]:
    """The table a rule measures and the ALTER verb for it."""
    if rule.table == "metadata":
        return f"{BRONZE}.{q(file_metadata_table(compiled))}", "ALTER DYNAMIC TABLE"
    if rule.table == "record":
        return f"{BRONZE}.{q(record_table(compiled, rule.record))}", "ALTER DYNAMIC TABLE"
    return f"{SILVER}.{q(silver_table(compiled))}", "ALTER ICEBERG TABLE"


def _value(v) -> str:
    return lit(str(v)) if isinstance(v, str) else str(v)


def failing_condition(rule: CompiledDqRule) -> str:
    """The condition, over the rule's columns, that a failing row satisfies; None for a control total."""
    c = [q(col.name) for col in rule.columns]
    if rule.kind == "accepted_values":
        return f"{c[0]} IS NOT NULL AND {c[0]} NOT IN ({', '.join(_value(v) for v in rule.values)})"
    if rule.kind == "range":
        bounds = []
        if rule.minimum is not None:
            bounds.append(f"{c[0]} < {_value(rule.minimum)}")
        if rule.maximum is not None:
            bounds.append(f"{c[0]} > {_value(rule.maximum)}")
        return f"{c[0]} IS NOT NULL AND ({' OR '.join(bounds)})"
    if rule.kind == "condition":
        return f"NOT COALESCE(({rule.condition}), FALSE)"
    if rule.kind == "not_null":
        return f"{c[0]} IS NULL"
    return None  # unique and control_total are group measures


def gap_sql(rule: CompiledDqRule) -> str:
    """Per file: the trailer's total minus what the file holds; zero when they agree."""
    trailer, total = (q(col.name) for col in rule.columns)
    return f"COALESCE({trailer}, 0) - COALESCE({total}, 0)"


def custom_dmf_body(rule: CompiledDqRule) -> str:
    if rule.kind == "control_total":
        return f"SELECT COALESCE(SUM(ABS({gap_sql(rule)})), 0) FROM ARG_T"
    if rule.kind == "unique":
        keys = ", ".join(q(col.name) for col in rule.columns)
        return f"SELECT COALESCE(SUM(\"N\" - 1), 0) FROM (SELECT COUNT(*) AS \"N\" FROM ARG_T GROUP BY {keys} HAVING COUNT(*) > 1)"
    return f"SELECT COUNT(*) FROM ARG_T WHERE {failing_condition(rule)}"


def render_custom_dmf(compiled: CompiledConfig, rule: CompiledDqRule) -> list[str]:
    name = f"{CONTROL}.{q(dmf_name(compiled, rule))}"
    args = ", ".join(f"{q(col.name)} {col.sql_type}" for col in rule.columns)
    meaning = "the gap between the trailer total and the file's records, summed over files" if rule.kind == "control_total" else "the number of rows that fail"
    return [
        f"CREATE OR REPLACE DATA METRIC FUNCTION {name}(ARG_T TABLE({args}))",
        "RETURNS NUMBER",
        f"COMMENT = {lit(f'{compiled.id} rule {rule.id} ({rule.level}, {rule.severity}): {rule.check}. Returns {meaning}; 0 when the rule holds.')}",
        "AS",
        "$$",
        f"  {custom_dmf_body(rule)}",
        "$$;",
    ]


def render_rule(compiled: CompiledConfig, rule: CompiledDqRule) -> list[str]:
    table, alter = rule_table(compiled, rule)
    columns = ", ".join(q(col.name) for col in rule.columns)
    lines = [f"-- {rule.id} ({rule.kind}, {rule.level}, {rule.severity}): {rule.check}"]
    if rule.system_function:
        lines.append(f"{alter} {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.{rule.system_function} ON ({columns});")
    else:
        lines += render_custom_dmf(compiled, rule)
        lines.append(f"{alter} {table} ADD DATA METRIC FUNCTION {CONTROL}.{q(dmf_name(compiled, rule))} ON ({columns});")
    lines.append("")
    return lines


def render_dmfs(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    lines = [
        f"-- Data metric functions of {compiled.id}: what the spec alone gives (row counts, nulls in required fields, duplicates of a",
        "-- single-column merge key) and one function or association per dq_rule of the config. Custom functions return 0 when the",
        "-- rule holds: the gap for a control total, the failing rows otherwise. Measured whenever the tables change. Rendered by",
        "-- astra-data render.",
        "",
    ]
    merge_keys = tuple(spec.merge.keys) if spec.merge else ()
    scheduled: list[str] = []
    for record in spec.records:
        if record.type != "detail":
            continue
        table = f"{BRONZE}.{q(record_table(compiled, record.label))}"
        names = [f.name.upper() for f in record.fields if f.name != "filler"]
        lines.append(f"-- {record.label}")
        lines.append(f"ALTER DYNAMIC TABLE {table} SET DATA_METRIC_SCHEDULE = 'TRIGGER_ON_CHANGES';")
        scheduled.append(table)
        lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.ROW_COUNT ON ();")
        for f in record.fields:
            if f.required and f.name != "filler":
                lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.NULL_COUNT ON ({q(f.name.upper())});")
        if len(merge_keys) == 1 and merge_keys[0].upper() in names:
            lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.DUPLICATE_COUNT ON ({q(merge_keys[0].upper())});")
        lines.append("")
    if compiled.dq_rules:
        lines.append("-- dq_rules of the config")
        for rule in compiled.dq_rules:
            table, alter = rule_table(compiled, rule)
            if table not in scheduled:
                lines.append(f"{alter} {table} SET DATA_METRIC_SCHEDULE = 'TRIGGER_ON_CHANGES';")
                scheduled.append(table)
        lines.append("")
        for rule in compiled.dq_rules:
            lines += render_rule(compiled, rule)
    return "\n".join(lines)


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"dq/dmf_{compiled.id}.sql": render_dmfs(compiled)}
