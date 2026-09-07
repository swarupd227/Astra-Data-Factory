"""Bronze for one source: the raw-lines table and pipe, the file registry, the lines view and intake.

Parsing is a set of dynamic tables (render/parse.py) over the lines view;
the merge stage (S3.2.3) moves rows on to Silver. This module renders what
lands and the first stage, intake, which registers files the landing zone
has loaded for this source so the next stages have a queue to work from.
"""

from __future__ import annotations

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, EXCEPTIONS, LINEAGE, custodian_folder, delivery_patterns, exceptions_table, files_table, like_to_regex, lines_view, lit, pipe_name, procedure, q, raw_lines_table, record_table, runs_table

RAW_LINES_COLUMNS = ("FILE_NAME", "ROW_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT")


def problems(compiled: CompiledConfig) -> list[Problem]:
    """What the source config must say for Bronze to be rendered."""
    found: list[Problem] = []
    path = compiled.provenance["config"]["path"]
    if not compiled.delivery or not compiled.delivery.get("files"):
        found.append(Problem(path, None, "delivery.files is needed to render the source: it says which landed files belong to this source"))
    elif custodian_folder(compiled) is None:
        found.append(Problem(path, None, "delivery.files patterns must all sit under one custodian folder of the landing prefix (for example pershing/GCUS_%_POS_%.dat); the pipe watches that folder"))
    if not compiled.spec.logical_records():
        found.append(Problem(path, None, f"spec {compiled.spec.label} has no detail record to render a Bronze table for"))
    return found


def _patterns(compiled: CompiledConfig) -> list[str]:
    return delivery_patterns(compiled)


def _file_filter(compiled: CompiledConfig, column: str = "FILE_NAME") -> str:
    return "(" + " OR ".join(f"{column} LIKE {lit(p)}" for p in _patterns(compiled)) + ")"


def render_ddl(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    source = compiled.id
    lines = [
        f"-- Bronze for source {source}: the raw lines Snowpipe lands and the file registry. Rendered by astra-data render.",
        f"-- {spec.label} is parsed into typed dynamic tables in pipeline/{source}_parse.sql.",
        f"-- {BRONZE} is filled at deploy time; tables inherit the database's external volume and catalog.",
        "",
        f"-- Raw lines of {source} as delivered, one row per line, written only by the pipe {pipe_name(compiled)}.",
        f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(raw_lines_table(compiled))} (",
        '  "FILE_NAME"          STRING NOT NULL COMMENT \'Path of the file relative to the landing stage\',',
        '  "ROW_NUMBER"         NUMBER(18,0) NOT NULL COMMENT \'1-based line number within the file\',',
        '  "LINE"               STRING COMMENT \'The line as delivered, untouched\',',
        '  "FILE_CONTENT_KEY"   STRING COMMENT \'Checksum of the file as reported by the stage\',',
        '  "FILE_LAST_MODIFIED" TIMESTAMP_NTZ(6) COMMENT \'Last-modified time of the file in S3\',',
        '  "INGESTED_AT"        TIMESTAMP_NTZ(6) NOT NULL COMMENT \'When Snowpipe started scanning the file\'',
        ")",
        f"BASE_LOCATION = 'bronze/{raw_lines_table(compiled).lower()}/'",
        f"COMMENT = {lit(f'Source {source}: raw lines landed under {custodian_folder(compiled)}, untouched. Rendered by astra-data render.')};",
        "-- Raw custodian records carry account numbers and names: masked from the start (ADR 0008).",
        f"ALTER ICEBERG TABLE {BRONZE}.{q(raw_lines_table(compiled))} MODIFY COLUMN \"LINE\" SET TAG {CONTROL}.\"PII\" = 'raw_record';",
        "",
    ]
    lines.append(f"-- The typed record tables of {source} ({', '.join(record_table(compiled, r.label) for r in spec.records if r.type == 'detail')}),")
    lines.append(f"-- the parse problems and the per-file metadata are dynamic tables in pipeline/{source}_parse.sql.")
    lines.append("")
    lines.append(f"-- Every landed file of {source} and where it is in the pipeline.")
    lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(files_table(compiled))} (")
    lines.append(
        ",\n".join(
            [
                '  "FILE_NAME"          STRING NOT NULL',
                '  "FILE_HASH"          STRING COMMENT \'Content hash from CONTROL.FILE_LOAD_LOG\'',
                '  "FILE_LAST_MODIFIED" TIMESTAMP_NTZ(6)',
                '  "ROW_COUNT"          NUMBER(18,0) COMMENT \'Lines Snowpipe loaded, from CONTROL.FILE_LOAD_LOG; the merge waits until the parse has them all\'',
                '  "FIRST_SEEN_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT \'When intake registered the file\'',
                '  "STATUS"             STRING NOT NULL COMMENT \'pending, merged or rejected\'',
                '  "RUN_ID"             STRING COMMENT \'Pipeline run that last changed the status\'',
                '  "STATUS_AT"          TIMESTAMP_NTZ(6) NOT NULL',
            ]
        )
    )
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'bronze/{files_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: landed files and their pipeline status. Rendered by astra-data render.')};")
    lines.append("")
    lines.append(f"-- Every run of {source}: what each stage registered, merged, projected and rejected, and how many exceptions it wrote.")
    lines.append(f"-- ROWS_REJECTED is counted by the stages from their own inputs; the exception store must agree (tests/{source}_rejected_rows_equal_exceptions.sql).")
    lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {BRONZE}.{q(runs_table(compiled))} (")
    lines.append(
        ",\n".join(
            [
                '  "RUN_ID"            STRING NOT NULL',
                '  "STARTED_AT"        TIMESTAMP_NTZ(6) NOT NULL',
                '  "FINISHED_AT"       TIMESTAMP_NTZ(6)',
                '  "FILES_REGISTERED"  NUMBER(18,0) NOT NULL COMMENT \'Files intake registered as pending\'',
                '  "FILES_MERGED"      NUMBER(18,0) NOT NULL',
                '  "FILES_REJECTED"    NUMBER(18,0) NOT NULL COMMENT \'Files rejected whole (unknown merge mode, out of order); their rows stay in Bronze\'',
                '  "ROWS_MERGED"       NUMBER(18,0) NOT NULL COMMENT \'Rows inserted or updated in the Silver source table\'',
                '  "ROWS_PROJECTED"    NUMBER(18,0) NOT NULL COMMENT \'Rows merged into the canonical entity\'',
                '  "ROWS_REJECTED"     NUMBER(18,0) NOT NULL COMMENT \'Source rows a stage rejected: excluded by the parse, unpaired, blank or duplicate keys, unresolved\'',
                '  "EXCEPTIONS_FILE"   NUMBER(18,0) NOT NULL',
                '  "EXCEPTIONS_RECORD" NUMBER(18,0) NOT NULL COMMENT \'Record-level exception rows written; one per rejected row and failure\'',
                '  "EXCEPTIONS_FIELD"  NUMBER(18,0) NOT NULL',
            ]
        )
    )
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'bronze/{runs_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: the run ledger. Rendered by astra-data render.')};")
    lines.append("")
    return "\n".join(lines)


def render_pipe(compiled: CompiledConfig) -> str:
    source = compiled.id
    folder = custodian_folder(compiled)
    patterns = _patterns(compiled)
    regex = "|".join(like_to_regex(p) for p in patterns)
    if len(patterns) > 1:
        regex = f"({regex})"
    table = f"{BRONZE}.{q(raw_lines_table(compiled))}"
    return "\n".join(
        [
            f"-- Snowpipe for {source}: every file under the custodian folder {folder} that matches the delivery patterns",
            f"-- is loaded on arrival into {raw_lines_table(compiled)}, one row per line, untouched. The pipe reuses the",
            "-- foundation's storage integration, stage, file format and S3 event notification (ADR 0003).",
            "-- Created once: a pipe's COPY cannot be altered, and recreating it loses events that arrive in between and",
            "-- its load history. To change the folder or patterns, drop the pipe in a quiet window and redeploy.",
            "-- Rendered by astra-data render.",
            f"CREATE PIPE IF NOT EXISTS {BRONZE}.{q(pipe_name(compiled))}",
            "  AUTO_INGEST = TRUE",
            f"  COMMENT = {lit(f'Loads {folder} files of source {source} matching ' + ', '.join(patterns) + ' into ' + raw_lines_table(compiled) + '.')}",
            "AS",
            f"COPY INTO {table} ({', '.join(q(c) for c in RAW_LINES_COLUMNS)})",
            "FROM (",
            "  SELECT METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, $1, METADATA$FILE_CONTENT_KEY,",
            "         METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME",
            f'  FROM @{BRONZE}."LANDING"/{folder}',
            ")",
            f"FILE_FORMAT = (FORMAT_NAME = '{{{{ DATABASE }}}}.BRONZE.RAW_LINES')",
            f"PATTERN = {lit('.*' + regex)};",
            "",
        ]
    )


def render_lines_view(compiled: CompiledConfig) -> str:
    source = compiled.id
    return "\n".join(
        [
            f"-- The raw lines of {source}: what its pipe landed in {raw_lines_table(compiled)}, with the line number named.",
            "-- Every stage reads the source through this view. Rendered by astra-data render.",
            f"CREATE OR REPLACE VIEW {BRONZE}.{q(lines_view(compiled))}",
            f"COMMENT = {lit(f'Raw lines of source {source}: files under {custodian_folder(compiled)} matching ' + ', '.join(_patterns(compiled)) + '.')}",
            "AS",
            'SELECT "FILE_NAME", "ROW_NUMBER" AS "LINE_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT"',
            f"FROM {BRONZE}.{q(raw_lines_table(compiled))};",
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
  INSERT INTO {files} ("FILE_NAME", "FILE_HASH", "FILE_LAST_MODIFIED", "ROW_COUNT", "FIRST_SEEN_AT", "STATUS", "RUN_ID", "STATUS_AT")
  SELECT l."FILE_NAME", l."FILE_HASH", l."FILE_LAST_MODIFIED", l."ROW_COUNT", SYSDATE(), 'pending', :RUN_ID, SYSDATE()
  FROM {CONTROL}."FILE_LOAD_LOG" l
  WHERE l."STATUS" = 'LOADED'
    AND {_file_filter(compiled, 'l."FILE_NAME"')}
    AND NOT EXISTS (SELECT 1 FROM {files} f WHERE f."FILE_NAME" = l."FILE_NAME");
  registered := SQLROWCOUNT;
  UPDATE {BRONZE}.{q(runs_table(compiled))} SET "FILES_REGISTERED" = :registered WHERE "RUN_ID" = :RUN_ID;
  RETURN registered;
END;
$$;
"""


def render_process(compiled: CompiledConfig, stages: list[str]) -> str:
    source = compiled.id
    calls = "\n".join(f"  CALL {BRONZE}.{q(procedure(compiled, stage))}(:run_id);" for stage in stages)
    runs = f"{BRONZE}.{q(runs_table(compiled))}"
    exceptions = f"{EXCEPTIONS}.{q(exceptions_table(compiled))}"
    return f"""-- Process {source}: one run of every stage the bundle has, in order. Each stage is a procedure that
-- takes the run id and writes what it did to the run ledger; later releases add stages here without changing the task.
-- Rendered by astra-data render.
CREATE OR REPLACE PROCEDURE {BRONZE}.{q(procedure(compiled, "PROCESS"))}()
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Runs the stages of source {source}: {", ".join(s.lower() for s in stages)}; records the run in {runs_table(compiled)}.'
AS
$$
DECLARE
  run_id STRING DEFAULT UUID_STRING();
BEGIN
  INSERT INTO {runs} ("RUN_ID", "STARTED_AT", "FILES_REGISTERED", "FILES_MERGED", "FILES_REJECTED", "ROWS_MERGED", "ROWS_PROJECTED", "ROWS_REJECTED", "EXCEPTIONS_FILE", "EXCEPTIONS_RECORD", "EXCEPTIONS_FIELD")
  VALUES (:run_id, SYSDATE(), 0, 0, 0, 0, 0, 0, 0, 0, 0);
{calls}
  UPDATE {runs} r SET
    "FINISHED_AT" = SYSDATE(),
    "EXCEPTIONS_FILE" = (SELECT COUNT(*) FROM {exceptions} e WHERE e."RUN_ID" = :run_id AND e."LEVEL" = 'file'),
    "EXCEPTIONS_RECORD" = (SELECT COUNT(*) FROM {exceptions} e WHERE e."RUN_ID" = :run_id AND e."LEVEL" = 'record'),
    "EXCEPTIONS_FIELD" = (SELECT COUNT(*) FROM {exceptions} e WHERE e."RUN_ID" = :run_id AND e."LEVEL" = 'field')
  WHERE r."RUN_ID" = :run_id;
  RETURN run_id;
END;
$$;
"""


STAGES = ["INTAKE", "MERGE"]  # RESOLVE follows when the config maps into a canonical entity


def render(compiled: CompiledConfig) -> dict[str, str]:
    source = compiled.id
    return {
        f"ddl/bronze_{source}.sql": render_ddl(compiled),
        f"pipeline/{source}_pipe.sql": render_pipe(compiled),
        f"pipeline/{source}_lines.sql": render_lines_view(compiled),
        f"pipeline/{source}_intake.sql": render_intake(compiled),
        f"pipeline/{source}_process.sql": render_process(compiled, list(STAGES) + (["RESOLVE"] if compiled.mappings else [])),
    }


__all__ = ["LINEAGE", "RAW_LINES_COLUMNS", "STAGES", "problems", "render", "render_ddl", "render_intake", "render_lines_view", "render_pipe", "render_process"]
