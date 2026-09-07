"""Merge for one source: the Silver table of its logical record, the exceptions it raises, and the MERGE stage.

The stage takes every pending file of the source in arrival order once
its parse is complete, and applies it the way the pattern library's merge
does (patterns/merge.py, the oracle):

  refresh   every active row of the file's scope is replaced by the rows in
            the file as of the file's business date: rows in the file are
            inserted or updated, rows not in the file are retired
  update    rows in the file are inserted or updated on their keys; every
            other row of the scope is carried forward untouched

Where the spec pairs records (patterns/pairing.py), the file's rows are
the paired rows: a record whose partner never appeared, a second record
for the same keys, or a record with a blank key is an exception and not
merged. A blank merge key or a second row for the same keys in one file
is an exception too; the first row is kept. A file whose merge mode the
header does not declare, or whose business date is earlier than what
Silver already holds for its scope, is rejected whole. Every merge is
logged in CONTROL.MERGE_LOG, from which stale refreshes are alerted.
"""

from __future__ import annotations

from astra_knowledge.registry import Pairing, SourceSpec

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig
from astra_data.render.names import (
    BRONZE,
    CONTROL,
    EXCEPTIONS,
    SILVER,
    LogicalColumn,
    exceptions_table,
    file_metadata_table,
    files_table,
    lines_view,
    lit,
    logical_columns,
    parse_problems_table,
    procedure,
    q,
    record_table,
    runs_table,
    silver_table,
    sql_type,
)

LINEAGE_COLUMNS = ("BUSINESS_DATE", "FIRST_FILE", "LAST_FILE", "LAST_LINE", "FIRST_MERGED_AT", "LAST_MERGED_AT", "RUN_ID", "RETIRED_AT", "RETIRED_BY_FILE")


def problems(compiled: CompiledConfig) -> list[Problem]:
    spec = compiled.spec
    path = compiled.provenance["config"]["path"]
    if spec.merge is None:
        return [Problem(path, None, f"spec {spec.label} declares no merge block, so the source cannot be merged into Silver; say how a file changes Silver (refresh or update, scope, business date, keys)")]
    return []


def logical_record(spec: SourceSpec) -> str:
    return spec.merge.record or spec.logical_records()[0]


def _pairing(spec: SourceSpec, label: str) -> Pairing | None:
    return next((p for p in spec.pairings if p.name == label), None)


def _scope_columns(spec: SourceSpec) -> list[str]:
    return [f"SCOPE_{name.upper()}" for name in spec.merge.scope]


def _header_column(name: str) -> str:
    return f"HEADER_{name.upper()}"


# -- DDL ---------------------------------------------------------------------


def render_ddl(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    source = compiled.id
    label = logical_record(spec)
    columns = logical_columns(spec, label)
    width = max([len(q(c.name)) for c in columns] + [len('"RETIRED_BY_FILE"')]) + 1
    keys = ", ".join(spec.merge.keys)
    lines = [
        f"-- Silver for source {source}: the logical record '{label}' of {spec.label} as merged, one active row per",
        f"-- scope and key ({keys}); rows a refresh retires stay with RETIRED_AT set. Rendered by astra-data render.",
        "",
        f"CREATE ICEBERG TABLE IF NOT EXISTS {SILVER}.{q(silver_table(compiled))} (",
    ]
    body = [f"  {q(c.name).ljust(width)} {sql_type(c.field)}{' NOT NULL' if c.name.lower() in [k.lower() for k in spec.merge.keys] else ''} COMMENT {lit((c.field.description or f'{c.field.name} of the {c.record} record.').rstrip())}" for c in columns]
    body += [f"  {q(s).ljust(width)} STRING COMMENT 'Scope of the file that carried the row: header field {s[6:].lower()}'" for s in _scope_columns(spec)]
    body += [
        f"  {q('BUSINESS_DATE').ljust(width)} DATE COMMENT 'Business date of the file that last carried the row'",
        f"  {q('FIRST_FILE').ljust(width)} STRING NOT NULL COMMENT 'File that first carried the row'",
        f"  {q('LAST_FILE').ljust(width)} STRING NOT NULL COMMENT 'File that last carried the row'",
        f"  {q('LAST_LINE').ljust(width)} NUMBER(18,0) COMMENT 'Line of LAST_FILE the row came from'",
        f"  {q('FIRST_MERGED_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL",
        f"  {q('LAST_MERGED_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL",
        f"  {q('RUN_ID').ljust(width)} STRING NOT NULL COMMENT 'Pipeline run that last wrote the row'",
        f"  {q('RETIRED_AT').ljust(width)} TIMESTAMP_NTZ(6) COMMENT 'Set when a refresh no longer carried the row; NULL while active'",
        f"  {q('RETIRED_BY_FILE').ljust(width)} STRING COMMENT 'The refresh file that retired the row'",
    ]
    lines.append(",\n".join(body))
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'silver/{silver_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: logical record {label} of {spec.label} merged by mode ({spec.merge.mode_field}: ' + ', '.join(f'{k} = {v}' for k, v in spec.merge.modes.items()) + f'); keys {keys}. Rendered by astra-data render.')};")
    lines.append("")
    lines.append(f"-- Exceptions of {source}: rows and files the pipeline could not merge, with their rejection code, in the")
    lines.append("-- shape of the canonical Exception entity plus the row as parsed (PAYLOAD). Rendered by astra-data render.")
    lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {EXCEPTIONS}.{q(exceptions_table(compiled))} (")
    lines.append(
        ",\n".join(
            [
                '  "EXCEPTION_ID"   STRING NOT NULL',
                '  "REJECTION_CODE" STRING NOT NULL COMMENT \'Code from the rejection taxonomy; see CONTROL.REJECTION_CODES\'',
                '  "LEVEL"          STRING NOT NULL COMMENT \'file, record or field\'',
                '  "STAGE"          STRING NOT NULL COMMENT \'Pipeline stage that raised it: parse, merge or resolution\'',
                '  "ENTITY"         STRING COMMENT \'Canonical entity the rejected record was meant for, when known\'',
                '  "CUSTODIAN_ID"   STRING NOT NULL',
                '  "FIELD_NAME"     STRING COMMENT \'Source field at fault, for field-level exceptions\'',
                '  "RAW_VALUE"      STRING COMMENT \'Value as received, for field-level exceptions\'',
                '  "MESSAGE"        STRING NOT NULL',
                '  "RECORD_KEY"     STRING COMMENT \'Reconciliation identity of the rejected record, as text\'',
                '  "PAYLOAD"        STRING NOT NULL COMMENT \'The full source record, as JSON: the raw line for a parse problem, the parsed row for a merge or resolution exception, the file metadata for a file-level exception\'',
                '  "RAISED_AT"      TIMESTAMP_NTZ(6) NOT NULL',
                '  "STATUS"         STRING NOT NULL COMMENT \'NEW when written; then RESOLVED, AUTO_RESOLVED or DISMISSED by triage\'',
                '  "RESOLUTION"     STRING',
                '  "RESOLVED_BY"    STRING',
                '  "RESOLVED_AT"    TIMESTAMP_NTZ(6)',
                '  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT \'Custodian id\'',
                '  "SOURCE_FILE"    STRING NOT NULL',
                '  "SOURCE_LINE"    NUMBER(18,0) COMMENT \'0 for a file-level exception\'',
                '  "CONFIG_VERSION" STRING NOT NULL COMMENT \'Digest of the config whose bundle raised it\'',
                '  "RUN_ID"         STRING NOT NULL',
                '  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL',
                '  "UPDATED_AT"     TIMESTAMP_NTZ(6)',
            ]
        )
    )
    lines.append(")")
    lines.append(f"BASE_LOCATION = 'exceptions/{exceptions_table(compiled).lower()}/'")
    lines.append(f"COMMENT = {lit(f'Source {source}: exceptions raised by the pipeline stages, with rejection codes. Rendered by astra-data render.')};")
    lines.append("")
    return "\n".join(lines)


# -- the MERGE procedure -----------------------------------------------------


def _exception_insert(compiled: CompiledConfig, code: str, level: str, message: str, *, line: str = "0", record_key: str = "NULL", payload: str | None = None, field: str = "NULL") -> str:
    """A file-level exception; its payload is the file's metadata row unless another payload is given."""
    custodian = lit(compiled.source["custodian"])
    config_version = lit(compiled.provenance["config"]["sha256"][:12])
    metadata = f"{BRONZE}.{q(file_metadata_table(compiled))}"
    payload = payload or f'(SELECT TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)) FROM {metadata} m WHERE m."FILE_NAME" = :file_name)'
    return (
        f'INSERT INTO {EXCEPTIONS}.{q(exceptions_table(compiled))} ("EXCEPTION_ID", "REJECTION_CODE", "LEVEL", "STAGE", "CUSTODIAN_ID", "FIELD_NAME", "MESSAGE", "RECORD_KEY", "PAYLOAD", "RAISED_AT", "STATUS", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "RUN_ID", "LOADED_AT")\n'
        f"    SELECT UUID_STRING(), {lit(code)}, {lit(level)}, 'merge', {custodian}, {field}, {message}, {record_key}, {payload}, SYSDATE(), 'NEW', {custodian}, :file_name, {line}, {config_version}, :RUN_ID, SYSDATE();"
    )


def _parse_exceptions(compiled: CompiledConfig) -> str:
    """Route the file's parse problems to the exception store: the raw line is the payload, the file metadata for file-level ones."""
    custodian = lit(compiled.source["custodian"])
    config_version = lit(compiled.provenance["config"]["sha256"][:12])
    problems = f"{BRONZE}.{q(parse_problems_table(compiled))}"
    lines = f"{BRONZE}.{q(lines_view(compiled))}"
    metadata = f"{BRONZE}.{q(file_metadata_table(compiled))}"
    return (
        f'INSERT INTO {EXCEPTIONS}.{q(exceptions_table(compiled))} ("EXCEPTION_ID", "REJECTION_CODE", "LEVEL", "STAGE", "CUSTODIAN_ID", "FIELD_NAME", "MESSAGE", "RECORD_KEY", "PAYLOAD", "RAISED_AT", "STATUS", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "RUN_ID", "LOADED_AT")\n'
        f"    SELECT UUID_STRING(), p.\"CODE\", p.\"LEVEL\", 'parse', {custodian}, p.\"FIELD\", p.\"MESSAGE\", NULL,\n"
        f"           IFF(p.\"LEVEL\" = 'file',\n"
        f"               (SELECT TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)) FROM {metadata} m WHERE m.\"FILE_NAME\" = :file_name),\n"
        f"               TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL('FILE_NAME', l.\"FILE_NAME\", 'LINE_NUMBER', l.\"LINE_NUMBER\", 'RECORD', p.\"RECORD\", 'LINE', l.\"LINE\"))),\n"
        f"           SYSDATE(), 'NEW', {custodian}, p.\"FILE_NAME\", p.\"LINE_NUMBER\", {config_version}, :RUN_ID, SYSDATE()\n"
        f"    FROM {problems} p\n"
        f"    LEFT JOIN {lines} l ON l.\"FILE_NAME\" = p.\"FILE_NAME\" AND l.\"LINE_NUMBER\" = p.\"LINE_NUMBER\"\n"
        f"    WHERE p.\"FILE_NAME\" = :file_name;"
    )


def _row_exceptions(compiled: CompiledConfig, rows: str, code: str, level: str, message: str, key_text: str, condition: str, record_label: str, field: str = "NULL") -> str:
    """Insert one exception per row of `rows` that satisfies `condition`, with the row as payload."""
    custodian = lit(compiled.source["custodian"])
    config_version = lit(compiled.provenance["config"]["sha256"][:12])
    return (
        f'INSERT INTO {EXCEPTIONS}.{q(exceptions_table(compiled))} ("EXCEPTION_ID", "REJECTION_CODE", "LEVEL", "STAGE", "CUSTODIAN_ID", "FIELD_NAME", "MESSAGE", "RECORD_KEY", "PAYLOAD", "RAISED_AT", "STATUS", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "RUN_ID", "LOADED_AT")\n'
        f"    SELECT UUID_STRING(), {lit(code)}, {lit(level)}, 'merge', {custodian}, {field}, {message}, {key_text}, TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)), SYSDATE(), 'NEW', {custodian}, \"FILE_NAME\", \"LINE_NUMBER\", {config_version}, :RUN_ID, SYSDATE()\n"
        f"    FROM {rows} WHERE {condition};"
    )


def _key_text_sql(names: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return " || ', ' || ".join(f"'{n}=' || COALESCE({prefix}{q(n.upper())}::STRING, 'NULL')" for n in names)


def _rows_sql(compiled: CompiledConfig, label: str) -> tuple[str, list[str]]:
    """SQL that selects the file's logical rows into the merge rows table, and the pairing exception statements."""
    spec = compiled.spec
    pairing = _pairing(spec, label)
    columns = logical_columns(spec, label)
    if pairing is None:
        table = f"{BRONZE}.{q(record_table(compiled, label))}"
        select = ", ".join(q(c.name) for c in columns)
        return f'SELECT "FILE_NAME", "LINE_NUMBER", {select} FROM {table} WHERE "FILE_NAME" = :file_name', []

    keys = [k.upper() for k in pairing.keys]
    key_list = ", ".join(q(k) for k in keys)
    key_not_null = " AND ".join(f"{q(k)} IS NOT NULL" for k in keys)
    aliases = {record: f"r{i}" for i, record in enumerate(pairing.records)}
    ctes = []
    for record in pairing.records:
        alias = aliases[record]
        ctes.append(
            f"{alias} AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY \"LINE_NUMBER\") AS \"NTH\" "
            f"FROM {BRONZE}.{q(record_table(compiled, record))} WHERE \"FILE_NAME\" = :file_name AND {key_not_null})"
        )
    first = aliases[pairing.records[0]]
    selects = [f'{first}."FILE_NAME"', f'{first}."LINE_NUMBER"']
    for c in columns:
        alias = aliases[c.record]
        selects.append(f"{alias}.{q(c.field.name.upper())} AS {q(c.name)}")
    joins = " ".join(f"JOIN {aliases[r]} ON " + " AND ".join(f"{aliases[r]}.{q(k)} = {first}.{q(k)}" for k in keys) + f" AND {aliases[r]}.\"NTH\" = 1" for r in pairing.records[1:])
    rows_sql = f"WITH {', '.join(ctes)}\n    SELECT {', '.join(selects)} FROM {first} {joins} WHERE {first}.\"NTH\" = 1"

    exceptions: list[str] = []
    for record in pairing.records:
        table = f"{BRONZE}.{q(record_table(compiled, record))}"
        blank = " OR ".join(f"{q(k)} IS NULL" for k in keys)
        exceptions.append(_row_exceptions(compiled, table, "PAIR_KEY_BLANK", "record", lit(f"key field blank; the {record} record cannot be paired"), _key_text_sql(pairing.keys), f'"FILE_NAME" = :file_name AND ({blank})', record))
        ranked = f"(SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY \"LINE_NUMBER\") AS \"NTH\" FROM {table} WHERE \"FILE_NAME\" = :file_name AND {key_not_null})"
        exceptions.append(_row_exceptions(compiled, ranked, "PAIR_DUPLICATE", "record", lit(f"a second {record} record for the same keys in the file; the first is kept"), _key_text_sql(pairing.keys), '"NTH" > 1', record))
        others = [r for r in pairing.records if r != record]
        partner_missing = " OR ".join(f"NOT EXISTS (SELECT 1 FROM {BRONZE}.{q(record_table(compiled, o))} o WHERE o.\"FILE_NAME\" = :file_name AND " + " AND ".join(f"o.{q(k)} = x.{q(k)}" for k in keys) + ")" for o in others)
        unpaired = f"(SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY \"LINE_NUMBER\") AS \"NTH\" FROM {table} WHERE \"FILE_NAME\" = :file_name AND {key_not_null}) x WHERE x.\"NTH\" = 1 AND ({partner_missing}))"
        exceptions.append(_row_exceptions(compiled, unpaired, "PAIR_INCOMPLETE", "record", lit(f"{record} record whose partner record never appeared in the file"), _key_text_sql(pairing.keys), "TRUE", record))
    return rows_sql, exceptions


def render_merge(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    rule = spec.merge
    source = compiled.id
    label = logical_record(spec)
    columns = logical_columns(spec, label)
    keys = [k.upper() for k in rule.keys]
    scope_fields = list(rule.scope)
    scope_columns = _scope_columns(spec)
    silver = f"{SILVER}.{q(silver_table(compiled))}"
    files = f"{BRONZE}.{q(files_table(compiled))}"
    metadata = f"{BRONZE}.{q(file_metadata_table(compiled))}"
    parse_problems = f"{BRONZE}.{q(parse_problems_table(compiled))}"
    rows = f"{BRONZE}.{q(source.upper() + '_MERGE_ROWS')}"
    merge_log = f'{CONTROL}."MERGE_LOG"'
    pairing = _pairing(spec, label)
    physical_records = list(pairing.records) if pairing else [label]

    mode_cases = " ".join(f"WHEN {lit(code)} THEN {lit(mode)}" for code, mode in rule.modes.items())
    accepted = ", ".join(f"''{code}'' = {mode}" for code, mode in rule.modes.items())
    scope_declares = "\n".join(f"    LET {s.lower()} STRING := (SELECT {q(_header_column(f))}::STRING FROM {metadata} WHERE \"FILE_NAME\" = :file_name);" for s, f in zip(scope_columns, scope_fields))
    scope_match_t = " AND ".join(f"t.{q(s)} IS NOT DISTINCT FROM :{s.lower()}" for s in scope_columns) or "TRUE"
    scope_text = " || ', ' || ".join(f"'{f}=' || COALESCE(:{s.lower()}, 'NULL')" for s, f in zip(scope_columns, scope_fields)) or "'all'"
    key_join = " AND ".join(f"t.{q(k)} = s.{q(k)}" for k in keys)
    key_list = ", ".join(q(k) for k in keys)
    key_not_null = " AND ".join(f"{q(k)} IS NOT NULL" for k in keys)
    key_blank = " OR ".join(f"{q(k)} IS NULL" for k in keys)
    column_list = ", ".join(q(c.name) for c in columns)
    updates = ", ".join(f"{q(c.name)} = s.{q(c.name)}" for c in columns if c.name not in keys)
    scope_inserts = "".join(f", :{s.lower()}" for s in scope_columns)
    scope_column_list = "".join(f", {q(s)}" for s in scope_columns)
    rows_sql, pairing_exceptions = _rows_sql(compiled, label)
    parse_checks = "\n".join(
        f"    LET {r}_expected NUMBER := (SELECT {q(r.upper() + '_COUNT')} - (SELECT COUNT(*) FROM {parse_problems} p WHERE p.\"FILE_NAME\" = :file_name AND p.\"LEVEL\" = 'record' AND p.\"RECORD\" = {lit(r)}) FROM {metadata} WHERE \"FILE_NAME\" = :file_name);\n"
        f"    LET {r}_parsed NUMBER := (SELECT COUNT(*) FROM {BRONZE}.{q(record_table(compiled, r))} WHERE \"FILE_NAME\" = :file_name);\n"
        f"    IF ({r}_parsed <> {r}_expected) THEN CONTINUE; END IF;"
        for r in physical_records
    )
    pairing_block = "\n".join(f"    {stmt}\n    rejected := rejected + SQLROWCOUNT;" for stmt in pairing_exceptions)

    return f"""-- Merge {source} into Silver: every pending file whose parse is complete, in arrival order, the way the pattern
-- library's merge does (refresh replaces the scope, update merges on keys{'; records paired on ' + ', '.join(pairing.keys) if pairing else ''}).
-- Rendered by astra-data render.
CREATE OR REPLACE PROCEDURE {BRONZE}.{q(procedure(compiled, "MERGE"))}(RUN_ID STRING)
RETURNS INTEGER
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Merges pending files of {source} into {silver_table(compiled)} by the header mode ({', '.join(f'{k} = {v}' for k, v in rule.modes.items())}); exceptions to {exceptions_table(compiled)}; every merge logged in CONTROL.MERGE_LOG.'
AS
$$
DECLARE
  pending CURSOR FOR
    SELECT "FILE_NAME", "ROW_COUNT" FROM {files} WHERE "STATUS" = 'pending' ORDER BY "FILE_LAST_MODIFIED", "FILE_NAME";
  merged INTEGER DEFAULT 0;
  rejected_files INTEGER DEFAULT 0;
  rows_merged INTEGER DEFAULT 0;
  rejected INTEGER DEFAULT 0;
BEGIN
  FOR f IN pending DO
    LET file_name STRING := f."FILE_NAME";
    LET row_count NUMBER := f."ROW_COUNT";

    -- 1. Parsed completely? The dynamic tables refresh on their own lag; a file is merged once every line is classified
    --    and every record row is in its table.
    LET line_count NUMBER := (SELECT "LINE_COUNT" FROM {metadata} WHERE "FILE_NAME" = :file_name);
    IF (line_count IS NULL OR row_count IS NULL OR line_count <> row_count) THEN CONTINUE; END IF;
{parse_checks}

    -- 2. Every parse problem of the file goes to the exception store with the source line as payload; the lines the
    --    parse excluded are rejected rows of this run.
    {_parse_exceptions(compiled)}
    rejected := rejected + (SELECT COUNT(DISTINCT p."LINE_NUMBER") FROM {parse_problems} p WHERE p."FILE_NAME" = :file_name AND p."LEVEL" = 'record');

    -- 3. The mode the header declares, the scope and the business date.
    LET mode_code STRING := (SELECT {q(_header_column(rule.mode_field))}::STRING FROM {metadata} WHERE "FILE_NAME" = :file_name);
    LET mode STRING := (SELECT CASE :mode_code {mode_cases} END);
    LET business_date DATE := (SELECT {q(_header_column(rule.business_date_field))} FROM {metadata} WHERE "FILE_NAME" = :file_name);
{scope_declares}
    LET header_count NUMBER := (SELECT "HEADER_COUNT" FROM {metadata} WHERE "FILE_NAME" = :file_name);
    IF (mode IS NULL) THEN
      LET message STRING := (SELECT IFF(:header_count = 0, 'the file has no header record, so the merge mode is unknown', 'merge mode ''' || COALESCE(:mode_code, 'NULL') || ''' in header field {rule.mode_field} is not one of {accepted}'));
      {_exception_insert(compiled, "MERGE_MODE_UNKNOWN", "file", ":message")}
      UPDATE {files} SET "STATUS" = 'rejected', "RUN_ID" = :RUN_ID, "STATUS_AT" = SYSDATE() WHERE "FILE_NAME" = :file_name;
      rejected_files := rejected_files + 1;
      CONTINUE;
    END IF;

    -- 4. Out of order: a business date earlier than what Silver already holds for the scope.
    LET latest DATE := (SELECT MAX(t."BUSINESS_DATE") FROM {silver} t WHERE t."RETIRED_AT" IS NULL AND {scope_match_t});
    IF (business_date IS NOT NULL AND latest IS NOT NULL AND business_date < latest) THEN
      LET message STRING := (SELECT 'business date ' || :business_date::STRING || ' is earlier than the ' || :latest::STRING || ' already in Silver for this scope; the file is out of order and was not merged');
      {_exception_insert(compiled, "MERGE_OUT_OF_ORDER", "file", ":message")}
      UPDATE {files} SET "STATUS" = 'rejected', "RUN_ID" = :RUN_ID, "STATUS_AT" = SYSDATE() WHERE "FILE_NAME" = :file_name;
      rejected_files := rejected_files + 1;
      CONTINUE;
    END IF;

    -- 5. The file's logical rows{', paired on ' + ', '.join(pairing.keys) if pairing else ''}.
{pairing_block}
    CREATE OR REPLACE TEMPORARY TABLE {rows} AS
    {rows_sql};

    -- 6. Blank keys and duplicate keys within the file are exceptions; the first row for a key is kept.
    {_row_exceptions(compiled, rows, "MERGE_KEY_BLANK", "record", lit("key field blank; the row cannot be merged"), _key_text_sql(rule.keys), key_blank, label)}
    rejected := rejected + SQLROWCOUNT;
    DELETE FROM {rows} WHERE {key_blank};
    {_row_exceptions(compiled, f'(SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY "LINE_NUMBER") AS "NTH" FROM {rows})', "MERGE_DUPLICATE_KEY", "record", lit("a second row for the same keys in the same file; the first is kept"), _key_text_sql(rule.keys), '"NTH" > 1', label)}
    rejected := rejected + SQLROWCOUNT;
    DELETE FROM {rows} WHERE ("FILE_NAME", "LINE_NUMBER") IN (SELECT "FILE_NAME", "LINE_NUMBER" FROM (SELECT "FILE_NAME", "LINE_NUMBER", ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY "LINE_NUMBER") AS "NTH" FROM {rows}) WHERE "NTH" > 1);

    -- 7. Insert or update on scope and keys.
    LET updated NUMBER := (SELECT COUNT(*) FROM {rows} s JOIN {silver} t ON {key_join} WHERE t."RETIRED_AT" IS NULL AND {scope_match_t});
    LET inserted NUMBER := (SELECT COUNT(*) FROM {rows});
    inserted := inserted - updated;
    MERGE INTO {silver} t
    USING {rows} s ON {key_join} AND {scope_match_t}
    WHEN MATCHED THEN UPDATE SET {updates}, "BUSINESS_DATE" = :business_date, "LAST_FILE" = :file_name, "LAST_LINE" = s."LINE_NUMBER", "LAST_MERGED_AT" = SYSDATE(), "RUN_ID" = :RUN_ID, "RETIRED_AT" = NULL, "RETIRED_BY_FILE" = NULL
    WHEN NOT MATCHED THEN INSERT ({column_list}{scope_column_list}, "BUSINESS_DATE", "FIRST_FILE", "LAST_FILE", "LAST_LINE", "FIRST_MERGED_AT", "LAST_MERGED_AT", "RUN_ID")
      VALUES ({", ".join(f's.{q(c.name)}' for c in columns)}{scope_inserts}, :business_date, :file_name, :file_name, s."LINE_NUMBER", SYSDATE(), SYSDATE(), :RUN_ID);

    -- 8. A refresh retires every active row of the scope the file no longer carries; an update carries them forward.
    LET retired NUMBER := 0;
    LET carried NUMBER := 0;
    IF (mode = 'refresh') THEN
      UPDATE {silver} t SET "RETIRED_AT" = SYSDATE(), "RETIRED_BY_FILE" = :file_name, "RUN_ID" = :RUN_ID
      WHERE t."RETIRED_AT" IS NULL AND {scope_match_t} AND NOT EXISTS (SELECT 1 FROM {rows} s WHERE {key_join});
      retired := SQLROWCOUNT;
    ELSE
      carried := (SELECT COUNT(*) FROM {silver} t WHERE t."RETIRED_AT" IS NULL AND {scope_match_t} AND NOT EXISTS (SELECT 1 FROM {rows} s WHERE {key_join}));
    END IF;

    -- 9. The merge log and the file's status.
    INSERT INTO {merge_log} ("CUSTODIAN_ID", "SOURCE_ID", "SCOPE", "BUSINESS_DATE", "MODE", "FILE_NAME", "ROWS_INSERTED", "ROWS_UPDATED", "ROWS_CARRIED", "ROWS_RETIRED", "LOADED_AT")
    VALUES ({lit(compiled.source['custodian'])}, {lit(source)}, {scope_text}, :business_date, :mode, :file_name, :inserted, :updated, :carried, :retired, SYSDATE());
    UPDATE {files} SET "STATUS" = 'merged', "RUN_ID" = :RUN_ID, "STATUS_AT" = SYSDATE() WHERE "FILE_NAME" = :file_name;
    merged := merged + 1;
    rows_merged := rows_merged + inserted + updated;
  END FOR;
  UPDATE {BRONZE}.{q(runs_table(compiled))} SET "FILES_MERGED" = :merged, "FILES_REJECTED" = :rejected_files, "ROWS_MERGED" = :rows_merged, "ROWS_REJECTED" = "ROWS_REJECTED" + :rejected WHERE "RUN_ID" = :RUN_ID;
  RETURN merged;
END;
$$;
"""


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {
        f"ddl/silver_{compiled.id}.sql": render_ddl(compiled),
        f"pipeline/{compiled.id}_merge.sql": render_merge(compiled),
    }
