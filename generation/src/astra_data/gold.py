"""Gold read models and the watermark, rendered as a release bundle per domain pack (S3.2.8, ADR 0026).

From `domains/<pack>/read-models.yaml` the bundle renders:

  ddl/gold_tables.sql          one Iceberg table per read model in GOLD, in the consumer's shape,
                               plus GOLD.WATERMARK and CONTROL.GOLD_PUBLISH_LOG
  pipeline/publish.sql         CONTROL.PUBLISH_GOLD(custodian): after a run of the custodian's DAG,
                               rewrites each business date the run touched, model by model, and
                               writes the watermark row for the date last, in one transaction
  pipeline/published_views.sql GOLD.<TABLE>_PUBLISHED: the rows of days that have a watermark
  tests/*.sql                  every Gold day has a watermark, the watermark is written last,
                               one watermark per custodian and date

The per-custodian Tasks DAG (render/tasks.py) ends in <CUSTODIAN>_PUBLISH,
which calls the procedure after every source of the custodian has
processed. The bundle is committed under releases/<pack>-gold and checked
in CI, like the reference-data bundle.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from astra_knowledge.cdm import DomainPack, load_packs
from astra_knowledge.read_models import GOLD_SCHEMA, ReadModel, ReadModels

from astra_core.problems import Problem

BUNDLE_SUFFIX = "-gold"
DB = "{{ DATABASE }}"
CONTROL = f'{DB}."CONTROL"'
SILVER = f'{DB}."SILVER"'
GOLD = f'{DB}."{GOLD_SCHEMA}"'
PUBLISH_LOG = f'{CONTROL}."GOLD_PUBLISH_LOG"'
WATERMARK = f'{GOLD}."WATERMARK"'
PUBLISH_COLUMNS = ('"PUBLISH_ID"', '"PUBLISHED_AT"')


def packs_with_read_models(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[DomainPack], list[Problem]]:
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    return [p for p in packs if p.read_models is not None and (domain is None or p.name == domain)], []


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def _table(model: ReadModel) -> str:
    return f"{GOLD}.{_q(model.table)}"


def _entity_table(pack: DomainPack, model: ReadModel) -> str:
    entities = pack.model(pack.read_models.model_version).entities
    entity = next(e for e in entities if e.name == model.entity)
    return f"{SILVER}.{_q(entity.table)}"


def _source_column(model: ReadModel, gold_name: str) -> str:
    """The entity column a Gold column carries; the custodian and business date columns always carry one."""
    column = model.column(gold_name)
    assert column is not None and column.source is not None
    return column.source


# -- DDL ---------------------------------------------------------------------


def render_tables(pack: DomainPack) -> str:
    read_models: ReadModels = pack.read_models
    lines = [
        f"-- Gold read models of the {pack.name} domain pack: the tables a consumer reads, in the consumer's shape, published per",
        "-- custodian and business date by CONTROL.PUBLISH_GOLD with the watermark written last. Rendered by astra-data gold render.",
        "",
    ]
    for model in read_models.models:
        width = max(len(_q(c.name)) for c in model.columns) + 1
        width = max(width, len('"PUBLISHED_AT"') + 1)
        lines.append(f"-- {model.id}: {model.description}")
        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(model)} (")
        body = []
        for c in model.columns:
            col = c.column
            origin = f"{model.entity}.{c.source}" if c.source else f"{c.expression}"
            body.append(f"  {_q(col.name).ljust(width)} {col.sql_type}{' NOT NULL' if col.required else ''} COMMENT {_lit(f'{col.description} From {origin}.')}")
        body.append(f"  {'\"PUBLISH_ID\"'.ljust(width)} STRING NOT NULL COMMENT 'Publish that wrote the row; see CONTROL.GOLD_PUBLISH_LOG'")
        body.append(f"  {'\"PUBLISHED_AT\"'.ljust(width)} TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written; never later than the watermark of its custodian and business date'")
        lines.append(",\n".join(body))
        lines.append(")")
        lines.append(f"BASE_LOCATION = '{GOLD_SCHEMA.lower()}/{model.table.lower()}/'")
        grain = f"per {model.custodian} and {model.business_date}" if model.dated else f"per {model.custodian}, as a whole"
        lines.append(f"COMMENT = {_lit(f'{model.description} Read model {model.id} of {model.entity}, published {grain}; read {model.table}_PUBLISHED for complete days only. Rendered by astra-data gold render.')};")
        for c in model.columns:
            if c.column.pii:
                lines.append(f"ALTER ICEBERG TABLE {_table(model)} MODIFY COLUMN {_q(c.name)} SET TAG {CONTROL}.\"PII\" = {_lit(c.column.pii)};")
        lines.append("")
    lines += [
        "-- The watermark: one row per custodian and business date, written after every Gold table of the day. A consumer that",
        "-- joins on it, or reads the <TABLE>_PUBLISHED views, never sees a half day.",
        f"CREATE ICEBERG TABLE IF NOT EXISTS {WATERMARK} (",
        '  "CUSTODIAN_ID"  STRING NOT NULL,',
        '  "BUSINESS_DATE" DATE NOT NULL,',
        '  "PUBLISHED_AT"  TIMESTAMP_NTZ(6) NOT NULL COMMENT \'When the day became complete in Gold; later than every row of the day\',',
        '  "PUBLISH_ID"    STRING NOT NULL COMMENT \'The publish that completed the day; see CONTROL.GOLD_PUBLISH_LOG\',',
        '  "RUN_ID"        STRING COMMENT \'The custodian DAG run the publish followed; see CONTROL.CUSTODIAN_RUNS\',',
        '  "ROWS"          NUMBER(18,0) NOT NULL COMMENT \'Gold rows of the day across the dated read models\',',
        '  "DETAIL"        STRING COMMENT \'Rows per read model, as JSON\'',
        ")",
        f"BASE_LOCATION = '{GOLD_SCHEMA.lower()}/watermark/'",
        "COMMENT = 'One row per custodian and business date whose Gold tables are complete; written last by CONTROL.PUBLISH_GOLD. Rendered by astra-data gold render.';",
        "",
        "-- Every publish: which custodian, which DAG run, how many business dates and rows.",
        f"CREATE ICEBERG TABLE IF NOT EXISTS {PUBLISH_LOG} (",
        '  "PUBLISH_ID"     STRING NOT NULL,',
        '  "CUSTODIAN_ID"   STRING NOT NULL,',
        '  "RUN_ID"         STRING COMMENT \'The custodian DAG run the publish followed\',',
        '  "STARTED_AT"     TIMESTAMP_NTZ(6) NOT NULL COMMENT \'Rows changed after the previous publish started are republished\',',
        '  "PUBLISHED_AT"   TIMESTAMP_NTZ(6) NOT NULL,',
        '  "BUSINESS_DATES" NUMBER(18,0) NOT NULL COMMENT \'Business dates rewritten, each with its watermark\',',
        '  "FIRST_DATE"     DATE,',
        '  "LAST_DATE"      DATE,',
        '  "ROWS"           NUMBER(18,0) NOT NULL COMMENT \'Gold rows written, dated and undated read models together\'',
        ")",
        "BASE_LOCATION = 'control/gold_publish_log/'",
        "COMMENT = 'Every Gold publish per custodian. Written by CONTROL.PUBLISH_GOLD. Rendered by astra-data gold render.';",
        "",
    ]
    return "\n".join(lines)


# -- The publish procedure ---------------------------------------------------


def _projection(model: ReadModel) -> str:
    parts = []
    for c in model.columns:
        if c.source:
            parts.append(f"s.{_q(c.source)} AS {_q(c.name)}" if c.source != c.name else f"s.{_q(c.name)}")
        else:
            parts.append(f"({c.expression}) AS {_q(c.name)}")
    return ", ".join(parts)


def _rewrite(pack: DomainPack, model: ReadModel, dated: bool, indent: str) -> list[str]:
    """DELETE the custodian's rows (of the business date) from the Gold table, then INSERT them from Silver."""
    table = _table(model)
    entity = _entity_table(pack, model)
    custodian_col = _q(model.custodian)
    src_custodian = _q(_source_column(model, model.custodian))
    where_gold = f"{custodian_col} = :CUSTODIAN_ID"
    where_silver = f"s.{src_custodian} = :CUSTODIAN_ID"
    if dated:
        where_gold += f" AND {_q(model.business_date)} = :business_date"
        where_silver += f" AND s.{_q(_source_column(model, model.business_date))} = :business_date"
    columns = ", ".join([_q(c.name) for c in model.columns] + list(PUBLISH_COLUMNS))
    return [
        f"{indent}-- {model.id}",
        f"{indent}DELETE FROM {table} WHERE {where_gold};",
        f"{indent}INSERT INTO {table} ({columns})",
        f"{indent}SELECT {_projection(model)}, :publish_id, SYSDATE()",
        f"{indent}FROM {entity} s",
        f"{indent}WHERE {where_silver};",
        f"{indent}n_{model.id} := SQLROWCOUNT;",
    ]


def render_publish(pack: DomainPack) -> str:
    read_models: ReadModels = pack.read_models
    dated = [m for m in read_models.models if m.dated]
    undated = [m for m in read_models.models if not m.dated]
    counters = "\n".join(f"  n_{m.id} INTEGER DEFAULT 0;" for m in read_models.models)
    dates_union = "\n      UNION\n".join(
        f'      SELECT s.{_q(_source_column(m, m.business_date))} AS "D" FROM {_entity_table(pack, m)} s WHERE s.{_q(_source_column(m, m.custodian))} = :CUSTODIAN_ID AND COALESCE(s."UPDATED_AT", s."LOADED_AT") > :since'
        for m in dated
    )
    undated_block = "\n".join(line for m in undated for line in _rewrite(pack, m, False, "  "))
    dated_block = "\n".join(line for m in dated for line in _rewrite(pack, m, True, "    "))
    day_rows = " + ".join(f":n_{m.id}" for m in dated) or "0"  # bound inside SQL statements
    day_rows_expr = " + ".join(f"n_{m.id}" for m in dated) or "0"  # in scripting expressions, no colon
    undated_rows = " + ".join(f":n_{m.id}" for m in undated) or "0"
    detail = ", ".join(f"'{m.id}', :n_{m.id}" for m in dated)
    return f"""-- Publish Gold for one custodian: after a run of its Tasks DAG, every business date whose canonical rows changed since
-- the previous publish is rewritten read model by read model, and the watermark row for the date is written last, in the
-- same transaction. A consumer that joins on GOLD.WATERMARK sees a day only once every table of it is complete; a rerun of
-- the day (late file, re-delivery) replaces the day and its watermark atomically. Read models without a business date are
-- the custodian's snapshot, rewritten whole before the dated ones. Rendered by astra-data gold render.
CREATE OR REPLACE PROCEDURE {CONTROL}."PUBLISH_GOLD"("CUSTODIAN_ID" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Publishes the Gold read models ({", ".join(m.id for m in read_models.models)}) for a custodian and writes the watermark last; called by the custodian''s <CUSTODIAN>_PUBLISH task.'
AS
$$
DECLARE
  publish_id STRING DEFAULT UUID_STRING();
  started_at TIMESTAMP_NTZ(6) DEFAULT SYSDATE();
  since TIMESTAMP_NTZ(6);
  run_id STRING;
  published INTEGER DEFAULT 0;
  rows_written INTEGER DEFAULT 0;
  first_date DATE;
  last_date DATE;
{counters}
BEGIN
  -- 1. Only after a run of the custodian's DAG that no publish has followed; rows changed since the previous publish
  --    started are what gets republished.
  since := (SELECT COALESCE(MAX("STARTED_AT"), '1900-01-01'::TIMESTAMP_NTZ(6)) FROM {PUBLISH_LOG} WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID);
  run_id := (SELECT MAX_BY("RUN_ID", "STARTED_AT") FROM {CONTROL}."CUSTODIAN_RUNS" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "STARTED_AT" > :since);
  IF (run_id IS NULL) THEN
    RETURN 'nothing to publish for ' || CUSTODIAN_ID;
  END IF;

  -- 2. Snapshots without a business date: the custodian's whole set, before the dated models.
  BEGIN TRANSACTION;
{undated_block}
  COMMIT;

  -- 3. Each business date whose canonical rows changed, oldest first: every dated read model, then the watermark, committed together.
  LET dates RESULTSET := (
    SELECT DISTINCT "D" FROM (
{dates_union}
    ) WHERE "D" IS NOT NULL ORDER BY "D"
  );
  FOR d IN dates DO
    LET business_date DATE := d."D";
    BEGIN TRANSACTION;
{dated_block}
    -- the watermark, last
    MERGE INTO {WATERMARK} w
    USING (SELECT :CUSTODIAN_ID AS "CUSTODIAN_ID", :business_date AS "BUSINESS_DATE") s
      ON w."CUSTODIAN_ID" = s."CUSTODIAN_ID" AND w."BUSINESS_DATE" = s."BUSINESS_DATE"
    WHEN MATCHED THEN UPDATE SET "PUBLISHED_AT" = SYSDATE(), "PUBLISH_ID" = :publish_id, "RUN_ID" = :run_id, "ROWS" = {day_rows}, "DETAIL" = TO_JSON(OBJECT_CONSTRUCT({detail}))
    WHEN NOT MATCHED THEN INSERT ("CUSTODIAN_ID", "BUSINESS_DATE", "PUBLISHED_AT", "PUBLISH_ID", "RUN_ID", "ROWS", "DETAIL")
      VALUES (s."CUSTODIAN_ID", s."BUSINESS_DATE", SYSDATE(), :publish_id, :run_id, {day_rows}, TO_JSON(OBJECT_CONSTRUCT({detail})));
    COMMIT;
    published := published + 1;
    rows_written := rows_written + {day_rows_expr};
    first_date := COALESCE(first_date, business_date);
    last_date := business_date;
  END FOR;

  -- 4. The publish itself.
  INSERT INTO {PUBLISH_LOG} ("PUBLISH_ID", "CUSTODIAN_ID", "RUN_ID", "STARTED_AT", "PUBLISHED_AT", "BUSINESS_DATES", "FIRST_DATE", "LAST_DATE", "ROWS")
  SELECT :publish_id, :CUSTODIAN_ID, :run_id, :started_at, SYSDATE(), :published, :first_date, :last_date, :rows_written + ({undated_rows});
  RETURN published || ' business date(s) published for ' || CUSTODIAN_ID || ' after run ' || run_id || ' (publish ' || publish_id || ')';
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
"""


# -- Views and tests ---------------------------------------------------------


def render_views(pack: DomainPack) -> str:
    read_models: ReadModels = pack.read_models
    lines = [
        "-- What a consumer reads: each Gold table joined to the watermark, so only days every table of which is complete are",
        "-- visible. A snapshot without a business date is visible once its custodian has any published day. Rendered by",
        "-- astra-data gold render.",
        "",
    ]
    for m in read_models.models:
        view = f"{GOLD}.{_q(m.table + '_PUBLISHED')}"
        if m.dated:
            join = f'JOIN {WATERMARK} w ON w."CUSTODIAN_ID" = g.{_q(m.custodian)} AND w."BUSINESS_DATE" = g.{_q(m.business_date)}'
            meaning = f"rows of {m.table} whose {m.custodian} and {m.business_date} have a watermark"
        else:
            join = f'WHERE EXISTS (SELECT 1 FROM {WATERMARK} w WHERE w."CUSTODIAN_ID" = g.{_q(m.custodian)})'
            meaning = f"rows of {m.table} whose {m.custodian} has a published day"
        lines += [
            f"CREATE OR REPLACE VIEW {view}",
            f"COMMENT = {_lit(f'{m.description} Complete days only: {meaning}. Rendered by astra-data gold render.')}",
            "AS",
            f"SELECT g.*",
            f"FROM {_table(m)} g",
            f"{join};",
            "",
        ]
    return "\n".join(lines)


def render_tests(pack: DomainPack) -> dict[str, str]:
    read_models: ReadModels = pack.read_models
    tests: dict[str, str] = {}
    for m in read_models.models:
        if not m.dated:
            continue
        cust, date = _q(m.custodian), _q(m.business_date)
        tests[f"{m.id}_days_have_a_watermark.sql"] = "\n".join(
            [
                f"-- {m.table}: every custodian and business date in Gold has a watermark row. Returns days without one.",
                f"SELECT g.{cust}, g.{date}, COUNT(*) AS ROW_COUNT",
                f"FROM {_table(m)} g",
                f'LEFT JOIN {WATERMARK} w ON w."CUSTODIAN_ID" = g.{cust} AND w."BUSINESS_DATE" = g.{date}',
                'WHERE w."CUSTODIAN_ID" IS NULL',
                f"GROUP BY g.{cust}, g.{date};",
                "",
            ]
        )
        tests[f"{m.id}_watermark_written_last.sql"] = "\n".join(
            [
                f"-- {m.table}: no row of a day was written after the day's watermark. Returns rows newer than their watermark.",
                f'SELECT g.{cust}, g.{date}, g."PUBLISHED_AT", w."PUBLISHED_AT" AS WATERMARK_AT',
                f"FROM {_table(m)} g",
                f'JOIN {WATERMARK} w ON w."CUSTODIAN_ID" = g.{cust} AND w."BUSINESS_DATE" = g.{date}',
                'WHERE g."PUBLISHED_AT" > w."PUBLISHED_AT";',
                "",
            ]
        )
    tests["watermark_one_per_custodian_and_date.sql"] = "\n".join(
        [
            "-- One watermark row per custodian and business date. Returns pairs with more than one.",
            'SELECT "CUSTODIAN_ID", "BUSINESS_DATE", COUNT(*) AS ROW_COUNT',
            f"FROM {WATERMARK}",
            'GROUP BY "CUSTODIAN_ID", "BUSINESS_DATE"',
            "HAVING COUNT(*) > 1;",
            "",
        ]
    )
    return tests


# -- The bundle --------------------------------------------------------------


def bundle_name(pack: DomainPack) -> str:
    return f"{pack.name.replace('_', '-')}{BUNDLE_SUFFIX}"


def render_bundle(pack: DomainPack) -> dict[str, str]:
    """Every file of the pack's Gold bundle, keyed by path relative to the bundle directory."""
    if pack.read_models is None:
        raise ValueError(f"domain pack {pack.name} declares no read models")
    files: dict[str, str] = {
        "ddl/gold_tables.sql": render_tables(pack),
        "pipeline/publish.sql": render_publish(pack),
        "pipeline/published_views.sql": render_views(pack),
    }
    files.update({f"tests/{name}": text for name, text in render_tests(pack).items()})
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    manifest = [
        f"# Gold read models and the watermark of the {pack.name} domain pack. Rendered by astra-data gold render from",
        f"# domains/{pack.name}/read-models.yaml; the version is a digest of the rendered files. Do not edit.",
        f"bundle: {bundle_name(pack)}",
        f'version: "{digest.hexdigest()[:12]}"',
        f"source: {pack.name}_gold",
        "steps:",
        "  - ddl/gold_tables.sql",
        "  - pipeline/publish.sql",
        "  - pipeline/published_views.sql",
        "tests:",
        "  - tests/*.sql",
        "",
    ]
    files["manifest.yaml"] = "\n".join(manifest)
    return files


def write_bundle(pack: DomainPack, releases_dir: Path) -> Path:
    """Write the bundle, removing files it no longer produces. Returns the bundle directory."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                existing.unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def check_bundle(pack: DomainPack, releases_dir: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every bundle file that is missing, stale or no longer produced."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    problems: list[Problem] = []
    for name, text in files.items():
        path = root / name
        if not path.is_file():
            problems.append(Problem(_display(path, repo_root), None, "not rendered; run astra-data gold render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the read models changed since it was rendered; run astra-data gold render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data gold render to remove it"))
    return problems


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
