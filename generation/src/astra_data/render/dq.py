"""Data metric functions on one source's Bronze tables.

What can be measured from the spec alone is attached here: row counts,
nulls in required fields, duplicates of a single-column merge key. The
DQ rules a config states in words are documented with the source and
become rendered checks in F3.3, where the DQ Generator gives them shape.
"""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, q, record_table


def render_dmfs(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    lines = [
        f"-- Data metric functions on the Bronze tables of {compiled.id}: row counts, nulls in required fields,",
        "-- duplicates of a single-column merge key. Measured whenever the tables change. Rendered by astra-data render.",
        "",
    ]
    merge_keys = tuple(spec.merge.keys) if spec.merge else ()
    for record in spec.records:
        if record.type != "detail":
            continue
        table = f"{BRONZE}.{q(record_table(compiled, record.label))}"
        names = [f.name.upper() for f in record.fields if f.name != "filler"]
        lines.append(f"-- {record.label}")
        lines.append(f"ALTER DYNAMIC TABLE {table} SET DATA_METRIC_SCHEDULE = 'TRIGGER_ON_CHANGES';")
        lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.ROW_COUNT ON ();")
        for f in record.fields:
            if f.required and f.name != "filler":
                lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.NULL_COUNT ON ({q(f.name.upper())});")
        if len(merge_keys) == 1 and merge_keys[0].upper() in names:
            lines.append(f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.DUPLICATE_COUNT ON ({q(merge_keys[0].upper())});")
        lines.append("")
    if compiled.dq_rules:
        lines.append("-- DQ rules stated by the config, rendered as checks by the DQ Generator (F3.3):")
        for rule in compiled.dq_rules:
            lines.append(f"--   {rule['id']} ({rule['level']}, {rule.get('severity', 'error')}): {rule['check']}")
        lines.append("")
    return "\n".join(lines)


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"dq/dmf_{compiled.id}.sql": render_dmfs(compiled)}
