"""Names the renderers share: tables, procedures, tasks, the bundle, and SQL types of spec fields."""

from __future__ import annotations

from dataclasses import dataclass

from astra_data.compiler import CompiledConfig
from astra_data.layout import LogicalColumn, logical_columns, metadata_columns, sql_type  # noqa: F401  (re-exported for the renderers)

DB = "{{ DATABASE }}"
BRONZE = f'{DB}."BRONZE"'
CONTROL = f'{DB}."CONTROL"'
SILVER = f'{DB}."SILVER"'
EXCEPTIONS = f'{DB}."EXCEPTIONS"'
REFERENCE = f'{DB}."REFERENCE"'

WAREHOUSE_BY_TIER = {"simple": "{{ WAREHOUSE_SIMPLE }}", "medium": "{{ WAREHOUSE_MEDIUM }}", "complex": "{{ WAREHOUSE_COMPLEX }}"}

# Names in generated SQL are always quoted; a source id or field name that is
# a reserved word is then safe.
LINEAGE = ("FILE_NAME", "LINE_NUMBER", "RUN_ID", "PARSED_AT")


def q(identifier: str) -> str:
    return f'"{identifier}"'


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def bundle_name(compiled: CompiledConfig) -> str:
    return compiled.id.replace("_", "-")


def source_name(compiled: CompiledConfig) -> str:
    return compiled.id.upper()


def task_name(compiled: CompiledConfig, stage: str = "PROCESS") -> str:
    """<CUSTODIAN>_... so that CONTROL.DETECT_TASK_FAILURES attributes a failure to the custodian (ADR 0007)."""
    custodian = compiled.source["custodian"].upper()
    source = source_name(compiled)
    prefix = source if source.startswith(custodian + "_") else f"{custodian}_{source}"
    return f"{prefix}_{stage}"


def gate_task_name(compiled: CompiledConfig) -> str:
    """The root task of the custodian's DAG, shared by every source of the custodian (ADR 0024)."""
    return f"{compiled.source['custodian'].upper()}_GATE"


def publish_task_name(compiled: CompiledConfig) -> str:
    """The last task of the custodian's DAG: publishes Gold and the watermark after every source has processed (ADR 0026)."""
    return f"{compiled.source['custodian'].upper()}_PUBLISH"


def record_table(compiled: CompiledConfig, record_label: str) -> str:
    return f"{source_name(compiled)}_{record_label.upper()}"


def files_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_FILES"


def problems_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_PROBLEMS"


def lines_view(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_LINES"


def raw_lines_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_RAW_LINES"


def classified_view(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_CLASSIFIED"


def parse_problems_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_PARSE_PROBLEMS"


def file_metadata_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_FILE_METADATA"


def silver_table(compiled: CompiledConfig) -> str:
    """The source's logical record as merged into Silver."""
    label = compiled.spec.merge.record if compiled.spec.merge and compiled.spec.merge.record else compiled.spec.logical_records()[0]
    return f"{source_name(compiled)}_{label.upper()}"


def exceptions_table(compiled: CompiledConfig) -> str:
    return source_name(compiled)


def runs_table(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_RUNS"


def pipe_name(compiled: CompiledConfig) -> str:
    return f"{source_name(compiled)}_PIPE"


def delivery_patterns(compiled: CompiledConfig) -> list[str]:
    return [f["pattern"] for f in (compiled.delivery or {}).get("files") or []]


def custodian_folder(compiled: CompiledConfig) -> str | None:
    """The folder under the landing prefix every delivery pattern sits in, or None when they disagree or have none."""
    folders: set[str] = set()
    for pattern in delivery_patterns(compiled):
        literal = pattern.split("%", 1)[0]
        if "/" not in literal:
            return None
        folders.add(literal.rsplit("/", 1)[0] + "/")
    return folders.pop() if len(folders) == 1 else None


def like_to_regex(pattern: str) -> str:
    """A SQL LIKE pattern as the regular expression Snowflake's PATTERN clause takes."""
    out: list[str] = []
    for char in pattern:
        if char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        elif char in ".^$*+?()[]{}|\\":
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def procedure(compiled: CompiledConfig, stage: str) -> str:
    return f"{source_name(compiled)}_{stage}"


# Column layout (types and logical record columns) lives in astra_data.layout, shared with the compiler.
