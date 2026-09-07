"""Names the renderers share: tables, procedures, tasks, the bundle, and SQL types of spec fields."""

from __future__ import annotations

from dataclasses import dataclass

from astra_knowledge.registry import Field, SourceSpec

from astra_data.compiler import CompiledConfig

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


def sql_type(field: Field) -> str:
    """The Snowflake type a parsed field lands in, from its logical type and picture."""
    picture = field.picture
    if field.type in ("string", "code"):
        return "STRING"
    if field.type == "integer":
        return f"NUMBER({picture.digits if picture and picture.digits else 18},0)"
    if field.type == "decimal":
        if picture and picture.is_numeric:
            return f"NUMBER({max(picture.precision, 1)},{picture.scale})"
        return "NUMBER(38,12)"
    if field.type == "date":
        return "DATE"
    if field.type == "time":
        return "TIME"
    if field.type == "boolean":
        return "BOOLEAN"
    return "STRING"


@dataclass(frozen=True)
class LogicalColumn:
    name: str  # column name in the Bronze table
    field: Field
    record: str  # physical record the field comes from
    key: bool = False


def logical_columns(spec: SourceSpec, label: str) -> list[LogicalColumn]:
    """The columns of a logical record's Bronze table, in the order the pattern library emits them.

    A pairing (S2.2.4) yields the keys once, then every other field of each
    record, prefixing a name both records use with its record label. Fillers
    are not kept.
    """
    pairing = next((p for p in spec.pairings if p.name == label), None)
    columns: list[LogicalColumn] = []
    if pairing is None:
        record = spec.record(label)
        if record is None:
            return []
        return [LogicalColumn(f.name.upper(), f, record.label) for f in record.fields if f.name != "filler"]
    records = [spec.record(r) for r in pairing.records]
    first = records[0]
    for key in pairing.keys:
        field = first.field(key)
        if field is not None:
            columns.append(LogicalColumn(key.upper(), field, first.label, key=True))
    shared = {f.name for f in records[0].fields} & {f.name for r in records[1:] for f in r.fields}
    for record in records:
        for f in record.fields:
            if f.name in pairing.keys or f.name == "filler":
                continue
            name = f"{record.label}_{f.name}" if f.name in shared else f.name
            columns.append(LogicalColumn(name.upper(), f, record.label))
    return columns


def metadata_columns(spec: SourceSpec, kind: str) -> list[LogicalColumn]:
    """Header or trailer fields kept on the file registry, prefixed HEADER_ or TRAILER_."""
    record = next((r for r in spec.records if r.type == kind), None)
    if record is None:
        return []
    return [LogicalColumn(f"{kind.upper()}_{f.name.upper()}", f, record.label) for f in record.fields if f.name not in ("filler", "record_type")]
