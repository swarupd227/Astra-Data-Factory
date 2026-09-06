"""Bronze for one source: typed tables, the file registry, problems, the lines view and intake.

The parse stage (S3.2.2) fills the typed tables from the lines view and
records every problem with its rejection code; the merge stage (S3.2.3)
moves rows on to Silver. This module renders what those stages write to
and the first stage, intake, which registers files the landing zone has
loaded for this source so the next stages have a queue to work from.
"""

from __future__ import annotations

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, LINEAGE, LogicalColumn, files_table, lines_view, lit, logical_columns, metadata_columns, problems_table, procedure, q, record_table, sql_type


def problems(compiled: CompiledConfig) -> list[Problem]:
    """What the source config must say for Bronze to be rendered."""
    found: list[Problem] = []
    path = compiled.provenance["config"]["path"]
    if not compiled.delivery or not compiled.delivery.get("files"):
        found.append(Problem(path, None, "delivery.files is needed to render the source: it says which landed files belong to this source"))
    if not compiled.spec.logical_records():
        found.append(Problem(path, None, f"spec {compiled.spec.label} has no detail record to render a Bronze table for"))
    return found


def _patterns(compiled: CompiledConfig) -> list[str]:
    return [f["pattern"] for f in compiled.delivery["files"]]


def _file_filter(compiled: CompiledConfig, column: str = "FILE_NAME") -> str:
    return "(" + " OR ".join(f"{column} LIKE {lit(p)}" for p in _patterns(compiled)) + ")"


def _column_line(column: LogicalColumn, width: int) -> str:
    field = column.field
    parts = [f"  {q(column.name).ljust(width)} {sql_type(field)}"]
    if field.required or column.key:
        parts.append("NOT NULL")
    comment = (field.description or f"{field.name} of the {column.record} record.").rstrip()
    if not comment.endswith("."):
        comment += "."
    if field.picture:
        comment += f" Picture {field.picture.text}."
    if field.codes:
        comment += " Codes: " + "; ".join(f"'{v}' = {m}" for v, m in field.codes) + "."
    parts.append(f"COMMENT {lit(comment)}")
    return " ".join(parts)


def render_ddl(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    source = compiled.id
    lines = [
        f"-- Bronze for source {source}: {spec.label} parsed into typed tables, one per logical record,",
        "-- plus the file registry and the problems raised while parsing. Rendered by astra-data render.",
        f"-- {BRONZE} is filled at deploy time; tables inherit the database's external volume and catalog.",
        "",
    ]
    for label in spec.logical_records():
        columns = logical_columns(spec, label)
        width = max(len(q(c.name)) for c in columns + [LogicalColumn("PARSED_AT", columns[0].field, "")]) + 1
        table = record_table(compiled, label)
        lines.append(f"-- Logical record '{label}' of {spec.label}: one row per parsed record.")
        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(table)} (")
        body = [_column_line(c, width) for c in columns]
        body += [
            f"  {q('FILE_NAME').ljust(width)} STRING NOT NULL COMMENT 'Landed file the record came from.'",
            f"  {q('LINE_NUMBER').ljust(width)} NUMBER(18,0) NOT NULL COMMENT 'Line of the file, from 1.'",
            f"  {q('RUN_ID').ljust(width)} STRING NOT NULL COMMENT 'Pipeline run that parsed the record.'",
            f"  {q('PARSED_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL",
        ]
        lines.append(",\n".join(body))
        lines.append(")")
        lines.append(f"BASE_LOCATION = 'bronze/{table.lower()}/'")
        lines.append(f"COMMENT = {lit(f'Source {source}: logical record {label} of spec {spec.label}, as parsed. Rendered by astra-data render.')};")
        lines.append("")

    meta = metadata_columns(spec, "header") + metadata_columns(spec, "trailer")
    width = max([len(q(c.name)) for c in meta] + [len('"FILE_LAST_MODIFIED"')]) + 1
    lines.append(f"-- Every landed file of {source} and where it is in the pipeline; header and trailer values once parsed.")
    lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(files_table(compiled))} (")
    body = [
        f"  {q('FILE_NAME').ljust(width)} STRING NOT NULL",
        f"  {q('FILE_HASH').ljust(width)} STRING COMMENT 'Content hash from CONTROL.FILE_LOAD_LOG'",
        f"  {q('FILE_LAST_MODIFIED').ljust(width)} TIMESTAMP_NTZ(6)",
        f"  {q('FIRST_SEEN_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When intake registered the file'",
        f"  {q('STATUS').ljust(width)} STRING NOT NULL COMMENT 'pending, parsed, merged or rejected'",
        f"  {q('RUN_ID').ljust(width)} STRING COMMENT 'Pipeline run that last changed the status'",
        f"  {q('STATUS_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL",
        f"  {q('LINE_COUNT').ljust(width)} NUMBER(18,0) COMMENT 'Lines in the file, once parsed'",
        f"  {q('PROBLEM_COUNT').ljust(width)} NUMBER(18,0) COMMENT 'Problems raised while parsing'",
    ]
    body += [_column_line(c, width).replace(" NOT NULL", "") for c in meta]
    lines.append(",\n".join(body))
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'bronze/{files_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: landed files and their pipeline status. Rendered by astra-data render.')};")
    lines.append("")

    lines.append(f"-- Every problem raised while parsing {source}, with the rejection code from the domain pack's taxonomy.")
    lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(problems_table(compiled))} (")
    lines.append(
        ",\n".join(
            [
                '  "FILE_NAME"   STRING NOT NULL',
                '  "LINE_NUMBER" NUMBER(18,0) NOT NULL COMMENT \'0 for a file-level problem\'',
                '  "RECORD"      STRING COMMENT \'Record type the line was read as\'',
                '  "FIELD"       STRING COMMENT \'Field at fault, for field-level problems\'',
                '  "LEVEL"       STRING NOT NULL COMMENT \'file, record or field\'',
                '  "CODE"        STRING NOT NULL COMMENT \'Rejection code; see CONTROL.REJECTION_CODES\'',
                '  "MESSAGE"     STRING NOT NULL',
                '  "RUN_ID"      STRING NOT NULL',
                '  "RAISED_AT"   TIMESTAMP_NTZ(6) NOT NULL',
            ]
        )
    )
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'bronze/{problems_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: parse problems by file, line and field, each with its rejection code. Rendered by astra-data render.')};")
    lines.append("")
    return "\n".join(lines)


def render_lines_view(compiled: CompiledConfig) -> str:
    source = compiled.id
    return "\n".join(
        [
            f"-- The raw lines of {source}: BRONZE.RAW_LINES restricted to the files delivery.files names for this source.",
            "-- Every stage reads the source through this view, never RAW_LINES directly. Rendered by astra-data render.",
            f"CREATE OR REPLACE VIEW {BRONZE}.{q(lines_view(compiled))}",
            f"COMMENT = {lit(f'Raw lines of source {source}: files matching ' + ', '.join(_patterns(compiled)) + '.')}",
            "AS",
            'SELECT "FILE_NAME", "ROW_NUMBER" AS "LINE_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT"',
            f'FROM {BRONZE}."RAW_LINES"',
            f"WHERE {_file_filter(compiled, q('FILE_NAME'))};",
            "",
        ]
    )


def render_intake(compiled: CompiledConfig) -> str:
    source = compiled.id
    files = f"{BRONZE}.{q(files_table(compiled))}"
    return f"""-- Intake for {source}: register every file the landing zone has loaded for this source that the
-- pipeline has not seen, as pending. The next stages take pending files in arrival order. Rendered by astra-data render.
CREATE OR REPLACE PROCEDURE {BRONZE}.{q(procedure(compiled, "INTAKE"))}(RUN_ID STRING)
RETURNS INTEGER
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Registers newly loaded files of {source} as pending in {files_table(compiled)}.'
AS
$$
DECLARE
  registered INTEGER DEFAULT 0;
BEGIN
  INSERT INTO {files} ("FILE_NAME", "FILE_HASH", "FILE_LAST_MODIFIED", "FIRST_SEEN_AT", "STATUS", "RUN_ID", "STATUS_AT")
  SELECT l."FILE_NAME", l."FILE_HASH", l."FILE_LAST_MODIFIED", SYSDATE(), 'pending', :RUN_ID, SYSDATE()
  FROM {CONTROL}."FILE_LOAD_LOG" l
  WHERE l."STATUS" = 'LOADED'
    AND {_file_filter(compiled, 'l."FILE_NAME"')}
    AND NOT EXISTS (SELECT 1 FROM {files} f WHERE f."FILE_NAME" = l."FILE_NAME");
  registered := SQLROWCOUNT;
  RETURN registered;
END;
$$;
"""


def render_process(compiled: CompiledConfig, stages: list[str]) -> str:
    source = compiled.id
    calls = "\n".join(f"  CALL {BRONZE}.{q(procedure(compiled, stage))}(:run_id);" for stage in stages)
    return f"""-- Process {source}: one run of every stage the bundle has, in order. Each stage is a procedure that
-- takes the run id; later releases add stages here without changing the task. Rendered by astra-data render.
CREATE OR REPLACE PROCEDURE {BRONZE}.{q(procedure(compiled, "PROCESS"))}()
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Runs the stages of source {source}: {", ".join(s.lower() for s in stages)}.'
AS
$$
DECLARE
  run_id STRING DEFAULT UUID_STRING();
BEGIN
{calls}
  RETURN run_id;
END;
$$;
"""


STAGES = ["INTAKE"]


def render(compiled: CompiledConfig) -> dict[str, str]:
    source = compiled.id
    return {
        f"ddl/bronze_{source}.sql": render_ddl(compiled),
        f"pipeline/{source}_lines.sql": render_lines_view(compiled),
        f"pipeline/{source}_intake.sql": render_intake(compiled),
        f"pipeline/{source}_process.sql": render_process(compiled, STAGES),
    }


__all__ = ["LINEAGE", "STAGES", "problems", "render", "render_ddl", "render_intake", "render_lines_view", "render_process"]
