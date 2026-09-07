"""Parse for one source: dynamic tables that turn raw lines into typed columns from the spec.

Three kinds of object, all computed from the source's lines view:

  <SOURCE>_CLASSIFIED       a view: each line with the record type its match
                            rule gives it and whether it is too long
  <SOURCE>_<RECORD>         a dynamic Iceberg table per detail record type: one
                            row per line of that type with every field typed by
                            the spec's offsets, pictures, formats and codes; rows
                            with a record-level problem are excluded
  <SOURCE>_PARSE_PROBLEMS   a dynamic table of every file, record and field
                            level problem with its rejection code
  <SOURCE>_FILE_METADATA    a dynamic table per file: line and record counts,
                            excluded rows, problem counts, header and trailer
                            values

The value rules are those of the pattern library (patterns/values.py,
patterns/numerics.py), which is the oracle these tables are checked
against: blank is NULL, a declared code is kept as written, integers are
digits, implied decimals take their scale from the picture and their sign
from the sign field through the foundation's SIGNED_IMPLIED_DECIMAL, dates
follow the declared format and all zeros is NULL. TARGET_LAG is the
config's processing.target_lag_minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from astra_knowledge.registry import Field, Record, SourceSpec

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, WAREHOUSE_BY_TIER, classified_view, file_metadata_table, lines_view, lit, parse_problems_table, q, record_table, sql_type

DATE_FORMATS = {
    "YYYYMMDD": "YYYYMMDD",
    "YYYY-MM-DD": "YYYY-MM-DD",
    "MMDDYYYY": "MMDDYYYY",
    "DDMMYYYY": "DDMMYYYY",
    "YYMMDD": "YYMMDD",
    "MM/DD/YYYY": "MM/DD/YYYY",
    "DD/MM/YYYY": "DD/MM/YYYY",
}
TIME_FORMATS = {"HHMMSS": "HH24MISS", "HHMM": "HH24MI", "HH:MM:SS": "HH24:MI:SS", "HH:MM": "HH24:MI"}
TRUE_VALUES = ("Y", "T", "1", "TRUE", "YES")
FALSE_VALUES = ("N", "F", "0", "FALSE", "NO")
COLUMNS = ("FILE_NAME", "LINE_NUMBER", "RECORD", "FIELD", "LEVEL", "CODE", "MESSAGE")


@dataclass(frozen=True)
class FieldSql:
    """The SQL that types one field and, when it can go wrong, the condition, code and message of its problem."""

    value: str
    problem: str | None = None  # boolean SQL
    code: str | None = None  # SQL expression yielding the rejection code
    message: str | None = None  # SQL expression


def problems(compiled: CompiledConfig) -> list[Problem]:
    """What the spec must say for the parse to be rendered."""
    spec = compiled.spec
    path = compiled.provenance["config"]["path"]
    found: list[Problem] = []
    if spec.format == "delimited" and (spec.file.get("quote") or spec.file.get("escape")):
        found.append(Problem(path, None, f"spec {spec.label} declares quoting or escaping for a delimited file; parsing quoted delimited files is not rendered yet (the pattern library handles them; a COPY with a CSV file format is the rendering, F3.2)"))
    for record in spec.records:
        for field in record.fields:
            if field.type == "date" and (field.format or "YYYYMMDD").upper() not in DATE_FORMATS:
                found.append(Problem(path, None, f"spec {spec.label}: field {record.label}.{field.name} has date format '{field.format}', which the renderer does not support; formats are {', '.join(DATE_FORMATS)}"))
            if field.type == "time" and (field.format or "HHMMSS").upper() not in TIME_FORMATS:
                found.append(Problem(path, None, f"spec {spec.label}: field {record.label}.{field.name} has time format '{field.format}', which the renderer does not support; formats are {', '.join(TIME_FORMATS)}"))
    return found


# -- one field -----------------------------------------------------------------


def raw_sql(spec: SourceSpec, field: Field) -> str:
    """The characters of a field on a classified line."""
    if spec.format == "fixed_width":
        start, length = field.position
        return f'SUBSTR("PADDED", {start}, {length})'
    return f'SPLIT_PART("LINE", {lit(spec.file["delimiter"])}, {field.column})'


def _codes(field: Field) -> str:
    return ", ".join(lit(v) for v, _ in field.codes)


def _array(values) -> str:
    return "ARRAY_CONSTRUCT(" + ", ".join(lit(v) for v in sorted(values)) + ")"


def field_sql(spec: SourceSpec, record: Record, field: Field) -> FieldSql:
    raw = raw_sql(spec, field)
    trimmed = f"TRIM({raw})"
    explicit = spec.format == "delimited"
    kind = field.type
    picture = field.picture

    if kind == "string":
        return FieldSql(f"IFF({trimmed} = '', NULL, RTRIM({raw}))")

    if kind == "code":
        if not field.codes:
            return FieldSql(f"NULLIF({trimmed}, '')")
        codes = _codes(field)
        value = f"CASE WHEN {raw} IN ({codes}) THEN {raw} WHEN {trimmed} IN ({codes}) THEN {trimmed} WHEN {trimmed} = '' THEN NULL ELSE {trimmed} END"
        problem = f"NOT ({raw} IN ({codes}) OR {trimmed} IN ({codes}) OR {trimmed} = '')"
        return FieldSql(value, problem, lit("FIELD_CODE_UNKNOWN"), f"'''' || {trimmed} || ''' is not a declared code; declared codes are ' || {lit(', '.join(repr(v) for v, _ in field.codes))}")

    if kind == "integer":
        digits = "[0-9]+" if not explicit else "[+-]?[0-9]+"
        text = f"REPLACE({trimmed}, ',', '')" if explicit else trimmed
        value = f"IFF(REGEXP_LIKE({text}, '{digits}'), TO_NUMBER({text}, 38, 0), NULL)"
        problem = f"{text} <> '' AND NOT REGEXP_LIKE({text}, '{digits}')"
        return FieldSql(value, problem, lit("FIELD_NOT_NUMERIC"), f"'''' || {trimmed} || ''' is not ' || {lit('an integer' if explicit else 'all digits')}")

    if kind == "decimal":
        target = sql_type(field)
        if explicit:
            text = f"REPLACE({trimmed}, ',', '')"
            value = f"IFF(REGEXP_LIKE({text}, '[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)'), TO_NUMBER({text}, 38, 12)::{target}, NULL)"
            problem = f"{text} <> '' AND NOT REGEXP_LIKE({text}, '[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)')"
            return FieldSql(value, problem, lit("FIELD_NOT_NUMERIC"), f"'''' || {trimmed} || ''' is not a number'")
        scale = picture.scale if picture else 0
        if field.sign_field is None and field.sign_style != "leading":
            value = f"IFF(REGEXP_LIKE({trimmed}, '[0-9]+'), {CONTROL}.\"IMPLIED_DECIMAL\"({raw}, {scale})::{target}, NULL)"
            problem = f"{trimmed} <> '' AND NOT REGEXP_LIKE({trimmed}, '[0-9]+')"
            return FieldSql(value, problem, lit("FIELD_NOT_NUMERIC"), f"'''' || {trimmed} || ''' is not all digits'")
        convention = field.sign_convention
        positive, negative, unknown = (convention.positive, convention.negative, convention.unknown) if convention else ({"+"}, {"-"}, {" ", ""})
        if field.sign_style == "leading":
            digits = f"IFF(LEFT({trimmed}, 1) IN ('+', '-'), TRIM(SUBSTR({trimmed}, 2)), {trimmed})"
            sign = f"IFF(LEFT({trimmed}, 1) IN ('+', '-'), LEFT({trimmed}, 1), '+')"
        else:
            sign_field = record.field(field.sign_field)
            digits = raw
            sign = raw_sql(spec, sign_field)
        arguments = f"{digits}, {sign}, {scale}, {_array(positive)}, {_array(negative)}, {_array(unknown)}"
        value = f'{CONTROL}."SIGNED_IMPLIED_DECIMAL"({arguments})::{target}'
        reason = f'{CONTROL}."SIGNED_IMPLIED_DECIMAL_PROBLEM"({arguments})'
        return FieldSql(value, f"{reason} IS NOT NULL", f"IFF({reason} LIKE '%not all digits', 'FIELD_NOT_NUMERIC', 'FIELD_SIGN_INVALID')", reason)

    if kind == "date":
        fmt = DATE_FORMATS[(field.format or "YYYYMMDD").upper()]
        blank = f"({trimmed} = '' OR REGEXP_LIKE({trimmed}, '0+'))"
        value = f"IFF({blank}, NULL, TRY_TO_DATE({trimmed}, {lit(fmt)}))"
        problem = f"NOT {blank} AND TRY_TO_DATE({trimmed}, {lit(fmt)}) IS NULL"
        return FieldSql(value, problem, lit("FIELD_DATE_INVALID"), f"'''' || {trimmed} || ''' is not a date in the format {field.format or 'YYYYMMDD'}'")

    if kind == "time":
        fmt = TIME_FORMATS[(field.format or "HHMMSS").upper()]
        value = f"IFF({trimmed} = '', NULL, TRY_TO_TIME({trimmed}, {lit(fmt)}))"
        problem = f"{trimmed} <> '' AND TRY_TO_TIME({trimmed}, {lit(fmt)}) IS NULL"
        return FieldSql(value, problem, lit("FIELD_TIME_INVALID"), f"'''' || {trimmed} || ''' is not a time in the format {field.format or 'HHMMSS'}'")

    if kind == "boolean":
        upper = f"UPPER({trimmed})"
        true_list = ", ".join(lit(v) for v in TRUE_VALUES)
        false_list = ", ".join(lit(v) for v in FALSE_VALUES)
        value = f"CASE WHEN {upper} IN ({true_list}) THEN TRUE WHEN {upper} IN ({false_list}) THEN FALSE ELSE NULL END"
        problem = f"{trimmed} <> '' AND {upper} NOT IN ({true_list}, {false_list})"
        return FieldSql(value, problem, lit("FIELD_BOOLEAN_INVALID"), f"'''' || {trimmed} || ''' is not a boolean (Y/N, T/F, 1/0)'")

    return FieldSql(f"IFF({trimmed} = '', NULL, RTRIM({raw}))")


def required_sql(spec: SourceSpec, record: Record, field: Field) -> str | None:
    """The condition under which a required field is blank without any other problem."""
    if not field.required:
        return None
    sql = field_sql(spec, record, field)
    return f"({sql.value}) IS NULL" + (f" AND NOT ({sql.problem})" if sql.problem else "")


# -- objects -------------------------------------------------------------------


def _match_sql(record: Record) -> str:
    rule = record.match
    if rule is None:
        return "TRUE"
    if rule.kind == "position":
        return f'SUBSTR("PADDED", {rule.start}, {rule.length}) = {lit(rule.value)}'
    if rule.kind == "end_marker":
        return f'SPLIT_PART(SUBSTR("PADDED", {rule.start}), {lit(rule.end_marker)}, 1) = {lit(rule.value)}'
    return f'SPLIT_PART("LINE", {lit("__DELIM__")}, {rule.column}) = {lit(rule.value)}'


def render_classified(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    source = compiled.id
    if spec.format == "fixed_width":
        length = spec.record_length
        padded = f'RPAD("LINE", {length})'
        too_long = f'LENGTH("LINE") > {length}'
    else:
        padded = '"LINE"'
        too_long = "FALSE"
    cases = "\n".join(f"         WHEN {_match_sql(r).replace('__DELIM__', spec.file.get('delimiter', ','))} THEN {lit(r.label)}" for r in spec.records)
    return "\n".join(
        [
            f"-- Each raw line of {source} with the record type its match rule gives it. Rendered by astra-data render.",
            f"CREATE OR REPLACE VIEW {BRONZE}.{q(classified_view(compiled))}",
            f"COMMENT = {lit(f'Lines of source {source} classified by record type ({spec.label}).')}",
            "AS",
            'SELECT "FILE_NAME", "LINE_NUMBER", "LINE",',
            f'       {padded} AS "PADDED",',
            '       LENGTH("LINE") AS "LINE_LENGTH",',
            "       CASE",
            cases,
            '       END AS "RECORD_TYPE",',
            f'       {too_long} AS "TOO_LONG"',
            f"FROM {BRONZE}.{q(lines_view(compiled))};",
            "",
        ]
    )


def _dynamic_header(compiled: CompiledConfig, name: str, comment: str, iceberg: bool = True) -> list[str]:
    tier = compiled.source["tier"]
    lines = [f"CREATE OR REPLACE DYNAMIC {'ICEBERG ' if iceberg else ''}TABLE {BRONZE}.{q(name)}", f"  TARGET_LAG = '{compiled.target_lag_minutes} minutes'", f"  WAREHOUSE = {WAREHOUSE_BY_TIER[tier]}"]
    if iceberg:
        lines.append(f"  BASE_LOCATION = 'bronze/{name.lower()}/'")
    lines.append(f"  COMMENT = {lit(comment)}")
    lines.append("AS")
    return lines


def render_record(compiled: CompiledConfig, record: Record) -> str:
    spec = compiled.spec
    fields = [f for f in record.fields if f.name != "filler"]
    width = max(len(q(f.name.upper())) for f in fields) + 1
    selects = ['       "FILE_NAME",', '       "LINE_NUMBER",']
    for f in fields:
        selects.append(f"       {field_sql(spec, record, f).value} AS {q(f.name.upper())},")
    selects[-1] = selects[-1].rstrip(",")
    return "\n".join(
        [
            f"-- Record type '{record.label}' of {spec.label}: every field typed by the spec; lines with a record-level problem are excluded.",
            *_dynamic_header(compiled, record_table(compiled, record.label), f"Source {compiled.id}: record {record.label} of {spec.label} parsed. TARGET_LAG from the config."),
            "SELECT",
            *selects,
            f"FROM {BRONZE}.{q(classified_view(compiled))}",
            f"WHERE \"RECORD_TYPE\" = {lit(record.label)} AND NOT \"TOO_LONG\";",
            "",
        ]
    )


def render_problems(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    classified = f"{BRONZE}.{q(classified_view(compiled))}"
    columns = ", ".join(q(c) for c in COLUMNS)
    selects: list[str] = []
    selects.append(
        f"SELECT \"FILE_NAME\", \"LINE_NUMBER\", NULL, NULL, 'record', 'RECORD_TYPE_UNKNOWN', 'no record type matches this line'\n"
        f"FROM {classified} WHERE \"RECORD_TYPE\" IS NULL"
    )
    if spec.format == "fixed_width":
        selects.append(
            f"SELECT \"FILE_NAME\", \"LINE_NUMBER\", \"RECORD_TYPE\", NULL, 'record', 'RECORD_TOO_LONG', 'line is ' || \"LINE_LENGTH\" || ' characters, longer than the record length {spec.record_length}'\n"
            f"FROM {classified} WHERE \"TOO_LONG\""
        )
    for kind in ("header", "trailer"):
        record = next((r for r in spec.records if r.type == kind), None)
        if record is None:
            continue
        selects.append(
            f"SELECT \"FILE_NAME\", \"LINE_NUMBER\", {lit(record.label)}, NULL, 'record', 'RECORD_DUPLICATE_HEADER', 'a second {record.label} record; a file has at most one'\n"
            f"FROM (SELECT \"FILE_NAME\", \"LINE_NUMBER\", ROW_NUMBER() OVER (PARTITION BY \"FILE_NAME\" ORDER BY \"LINE_NUMBER\") AS \"NTH\" FROM {classified} WHERE \"RECORD_TYPE\" = {lit(record.label)}) WHERE \"NTH\" > 1"
        )
        selects.append(
            f"SELECT \"FILE_NAME\", 0, NULL, NULL, 'file', 'FILE_{kind.upper()}_MISSING', 'the file has no {kind} record'\n"
            f"FROM {classified} GROUP BY \"FILE_NAME\" HAVING COUNT_IF(\"RECORD_TYPE\" = {lit(record.label)}) = 0"
        )
    for record in spec.records:
        for f in record.fields:
            if f.name == "filler":
                continue
            sql = field_sql(spec, record, f)
            if sql.problem:
                selects.append(
                    f"SELECT \"FILE_NAME\", \"LINE_NUMBER\", {lit(record.label)}, {lit(f.name)}, 'field', {sql.code}, {sql.message}\n"
                    f"FROM {classified} WHERE \"RECORD_TYPE\" = {lit(record.label)} AND NOT \"TOO_LONG\" AND ({sql.problem})"
                )
            required = required_sql(spec, record, f)
            if required:
                selects.append(
                    f"SELECT \"FILE_NAME\", \"LINE_NUMBER\", {lit(record.label)}, {lit(f.name)}, 'field', 'FIELD_REQUIRED_BLANK', 'required field is blank'\n"
                    f"FROM {classified} WHERE \"RECORD_TYPE\" = {lit(record.label)} AND NOT \"TOO_LONG\" AND ({required})"
                )
    body = "\nUNION ALL\n".join(selects)
    return "\n".join(
        [
            f"-- Every problem parsing {compiled.id} raises, with its rejection code: file level (a missing header or trailer),",
            "-- record level (the line is excluded from its record table) and field level (the value is NULL, the row kept).",
            *_dynamic_header(compiled, parse_problems_table(compiled), f"Source {compiled.id}: parse problems by file, line and field with rejection codes. TARGET_LAG from the config."),
            f"SELECT {columns} FROM (",
            body,
            ");",
            "",
        ]
    )


def render_file_metadata(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    classified = f"{BRONZE}.{q(classified_view(compiled))}"
    problems_table = f"{BRONZE}.{q(parse_problems_table(compiled))}"
    counts = [f"       COUNT_IF(\"RECORD_TYPE\" = {lit(r.label)}) AS {q(r.label.upper() + '_COUNT')}," for r in spec.records]
    meta_selects: list[str] = []
    joins: list[str] = []
    for kind in ("header", "trailer"):
        record = next((r for r in spec.records if r.type == kind), None)
        if record is None:
            continue
        alias = kind[0]
        fields = [f for f in record.fields if f.name not in ("filler", "record_type")]
        meta_selects += [f"       {alias}.{q(f.name.upper())} AS {q(kind.upper() + '_' + f.name.upper())}," for f in fields]
        inner = ", ".join(f"{field_sql(spec, record, f).value} AS {q(f.name.upper())}" for f in fields)
        joins.append(
            f"LEFT JOIN (SELECT \"FILE_NAME\", {inner} FROM {classified} WHERE \"RECORD_TYPE\" = {lit(record.label)} QUALIFY ROW_NUMBER() OVER (PARTITION BY \"FILE_NAME\" ORDER BY \"LINE_NUMBER\") = 1) {alias}\n  ON {alias}.\"FILE_NAME\" = l.\"FILE_NAME\""
        )
    totals = [(r.record, r.total_field) for r in compiled.dq_rules if r.kind == "control_total" and r.aggregate == "sum"]
    totals = sorted(set(totals))
    for n, (label, field_name) in enumerate(totals):
        alias = f"s{n}"
        column = q(f"{label.upper()}_{field_name.upper()}_TOTAL")
        meta_selects.append(f"       {alias}.\"TOTAL\" AS {column},")
        joins.append(f"LEFT JOIN (SELECT \"FILE_NAME\", SUM({q(field_name.upper())}) AS \"TOTAL\" FROM {BRONZE}.{q(record_table(compiled, label))} GROUP BY \"FILE_NAME\") {alias}\n  ON {alias}.\"FILE_NAME\" = l.\"FILE_NAME\"")
    return "\n".join(
        [
            f"-- Per file of {compiled.id}: what was parsed, what was excluded and why, the header and trailer values" + (" and the totals the control totals check" if totals else "") + ".",
            *_dynamic_header(compiled, file_metadata_table(compiled), f"Source {compiled.id}: per-file counts, excluded rows, problem counts, header and trailer values. TARGET_LAG from the config."),
            "SELECT l.\"FILE_NAME\",",
            "       l.\"LINE_COUNT\",",
            *[c.replace("       COUNT_IF", "       l.").replace(f"(\"RECORD_TYPE\" = ", "(") for c in []],
            *[f"       l.{q(r.label.upper() + '_COUNT')}," for r in spec.records],
            "       COALESCE(p.\"EXCLUDED_ROWS\", 0) AS \"EXCLUDED_ROWS\",",
            "       COALESCE(p.\"FIELD_PROBLEMS\", 0) AS \"FIELD_PROBLEMS\",",
            "       COALESCE(p.\"FILE_PROBLEMS\", 0) AS \"FILE_PROBLEMS\",",
            *meta_selects,
            "       l.\"FIRST_LINE_AT\",",
            "       l.\"LAST_LINE_AT\"",
            "FROM (",
            "  SELECT \"FILE_NAME\",",
            "         COUNT(*) AS \"LINE_COUNT\",",
            *[f"  {c}" for c in counts],
            "         MIN(\"INGESTED_AT\") AS \"FIRST_LINE_AT\",",
            "         MAX(\"INGESTED_AT\") AS \"LAST_LINE_AT\"",
            f"  FROM {BRONZE}.{q(lines_view(compiled))} r",
            f"  JOIN {classified} c USING (\"FILE_NAME\", \"LINE_NUMBER\")",
            "  GROUP BY \"FILE_NAME\"",
            ") l",
            "LEFT JOIN (",
            "  SELECT \"FILE_NAME\",",
            "         COUNT_IF(\"LEVEL\" = 'record') AS \"EXCLUDED_ROWS\",",
            "         COUNT_IF(\"LEVEL\" = 'field') AS \"FIELD_PROBLEMS\",",
            "         COUNT_IF(\"LEVEL\" = 'file') AS \"FILE_PROBLEMS\"",
            f"  FROM {problems_table}",
            "  GROUP BY \"FILE_NAME\"",
            ") p ON p.\"FILE_NAME\" = l.\"FILE_NAME\"",
            *joins,
            ";",
            "",
        ]
    )


def render_parse(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    parts = [
        f"-- Parse {compiled.id} ({spec.label}, {spec.format}): raw lines become typed columns through dynamic tables.",
        f"-- TARGET_LAG {compiled.target_lag_minutes} minutes from processing.target_lag_minutes. Rendered by astra-data render.",
        "",
        render_classified(compiled),
    ]
    for record in spec.records:
        if record.type == "detail":
            parts.append(render_record(compiled, record))
    parts.append(render_problems(compiled))
    parts.append(render_file_metadata(compiled))
    return "\n".join(parts)


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"pipeline/{compiled.id}_parse.sql": render_parse(compiled)}
