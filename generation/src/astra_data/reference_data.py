"""Reference-data replication, rendered from the domain pack as a release bundle.

For every feed in `domains/<pack>/reference-data.yaml` the bundle
`releases/<pack>-reference-data/` carries:

  ddl/reference_tables.sql       the replica, its staging, change and conflict tables
  pipeline/replicate_<feed>.sql  a procedure that loads the newest snapshot files,
                                 computes the delta, brings the replica in line and
                                 records the run with its row counts
  pipeline/lookups.sql           one view per feed with alternate identifiers, so
                                 resolution is one join
  pipeline/tasks.sql             a serverless task per feed on the feed's schedule
  tests/*.sql                    keys unique, last run not failed, replica fresh

The semantics are those of the pattern library's reference-data pattern
(astra_knowledge.patterns.reference_data), which is the oracle. The bundle
is committed and checked in CI; the deploy pipeline deploys it like any
other. `sync_statements` brings CONTROL.REFERENCE_FEEDS in line with the
feeds so the stale detector knows how often each is expected.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from astra_knowledge.cdm import DomainPack, load_packs
from astra_knowledge.reference_data import REFERENCE_SCHEMA, Feed, ReferenceData

from astra_core.problems import Problem
from astra_data.bundle import Executor, Target

DB = "{{ DATABASE }}"
CONTROL = f'{DB}."CONTROL"'
REFERENCE = f'{DB}."{REFERENCE_SCHEMA}"'
STAGE = f'@{DB}."{REFERENCE_SCHEMA}"."LANDING"'
FILE_FORMAT = f"{DB}.{REFERENCE_SCHEMA}.CSV"
BUNDLE_SUFFIX = "-reference-data"


def packs_with_reference_data(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[DomainPack], list[Problem]]:
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    if domain is not None:
        packs = [p for p in packs if p.name == domain]
        if not packs:
            return [], [Problem(Path(domains_dir).as_posix(), None, f"no domain pack named '{domain}'")]
    return [p for p in packs if p.reference_data is not None], []


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def _table(feed: Feed, suffix: str = "") -> str:
    return f"{REFERENCE}.{_q(feed.table + suffix)}"


def _procedure(feed: Feed) -> str:
    return f"{REFERENCE}.{_q('REPLICATE_' + feed.table)}"


def _task(feed: Feed) -> str:
    return f"{REFERENCE}.{_q('REFERENCE_' + feed.table + '_REPLICATE')}"


# -- DDL ---------------------------------------------------------------------


def render_tables(reference: ReferenceData) -> str:
    lines = [
        f"-- Reference-data replicas of the {reference.domain} pack: one replica per feed with its staging, change and conflict tables.",
        "-- Rendered by astra-data reference render. Do not edit; change the feeds file and re-render.",
        "",
    ]
    for feed in reference.feeds:
        width = max(len(c.name) for c in feed.columns) + 2
        keys = ", ".join(_q(k) for k in feed.key)
        columns = [f"  {_q(c.name).ljust(width)} {c.sql_type}{' NOT NULL' if c.required else ''} COMMENT {_lit(c.description + (f' PII: {c.pii}.' if c.pii else ''))}" for c in feed.columns]

        lines.append(f"-- {feed.name} ({feed.system}): key ({', '.join(feed.key)}); replicated from {STAGE}/{feed.file.folder}/")
        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(feed)} (")
        lines.append(",\n".join(columns + [f"  {_q('REPLICATED_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was last written by a replication run.'", f"  {_q('RUN_ID').ljust(width)} STRING NOT NULL COMMENT 'Replication run that last wrote the row; see CONTROL.REFERENCE_DATA_RUNS.'"]))
        lines.append(")")
        lines.append(f"BASE_LOCATION = 'reference/{feed.table.lower()}/'")
        lines.append(f"COMMENT = {_lit(f'{feed.description} Replica of {feed.system}; every row references its replication run.')};")
        lines.append("")

        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(feed, '_STAGING')} (")
        lines.append(",\n".join([f"  {_q(c.name).ljust(width)} {c.sql_type}" for c in feed.columns] + [f"  {_q('SOURCE_FILE').ljust(width)} STRING NOT NULL", f"  {_q('SOURCE_ROW').ljust(width)} NUMBER(18,0) NOT NULL"]))
        lines.append(")")
        lines.append(f"BASE_LOCATION = 'reference/{feed.table.lower()}_staging/'")
        lines.append(f"COMMENT = 'The newest {feed.name} snapshot as loaded, before the delta is computed. Truncated by every run.';")
        lines.append("")

        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(feed, '_CHANGES')} (")
        lines.append(",\n".join([f"  {_q('RUN_ID').ljust(width)} STRING NOT NULL", f"  {_q('CHANGED_AT').ljust(width)} TIMESTAMP_NTZ(6) NOT NULL", f"  {_q('CHANGE').ljust(width)} STRING NOT NULL COMMENT 'inserted, updated or deleted'"] + [f"  {_q(k).ljust(width)} {feed.column(k).sql_type} NOT NULL" for k in feed.key] + [f"  {_q('BEFORE').ljust(width)} STRING COMMENT 'The row before the change, as JSON; null when inserted'", f"  {_q('AFTER').ljust(width)} STRING COMMENT 'The row after the change, as JSON; null when deleted'"]))
        lines.append(")")
        lines.append(f"BASE_LOCATION = 'reference/{feed.table.lower()}_changes/'")
        lines.append(f"COMMENT = 'Every change a replication run made to {feed.table}: the delta since any run is the rows with a later RUN_ID.';")
        lines.append("")

        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(feed, '_CONFLICTS')} (")
        lines.append(",\n".join([f"  {_q('RUN_ID').ljust(width)} STRING NOT NULL"] + [f"  {_q(k).ljust(width)} {feed.column(k).sql_type}" for k in feed.key] + [f"  {_q('ROW_COUNT').ljust(width)} NUMBER(18,0) NOT NULL", f"  {_q('REJECTION_CODE').ljust(width)} STRING NOT NULL"]))
        lines.append(")")
        lines.append(f"BASE_LOCATION = 'reference/{feed.table.lower()}_conflicts/'")
        lines.append(f"COMMENT = 'Snapshot keys a run left out because they were blank or repeated ({feed.rejections.conflict}); the replica keeps what it had for them.';")
        lines.append("")
    return "\n".join(lines)


# -- replication procedure ---------------------------------------------------


def render_procedure(reference: ReferenceData, feed: Feed) -> str:
    cols = [c.name for c in feed.columns]
    col_list = ", ".join(_q(c) for c in cols)
    key_list = ", ".join(_q(k) for k in feed.key)
    key_join = " AND ".join(f"r.{_q(k)} = s.{_q(k)}" for k in feed.key)
    key_not_null = " AND ".join(f"{_q(k)} IS NOT NULL" for k in feed.key)
    differs = " OR ".join(f"s.{_q(c)} IS DISTINCT FROM r.{_q(c)}" for c in cols if c not in feed.key)
    casts = ", ".join(f"${i}::{c.sql_type}" for i, c in enumerate(feed.columns, start=1))
    before_json = "OBJECT_CONSTRUCT_KEEP_NULL(" + ", ".join(f"'{c}', r.{_q(c)}" for c in cols) + ")::STRING"
    after_json = "OBJECT_CONSTRUCT_KEEP_NULL(" + ", ".join(f"'{c}', s.{_q(c)}" for c in cols) + ")::STRING"
    updates = ", ".join(f"{_q(c)} = s.{_q(c)}" for c in cols if c not in feed.key)
    replica, staging, changes, conflicts = _table(feed), _table(feed, "_STAGING"), _table(feed, "_CHANGES"), _table(feed, "_CONFLICTS")
    runs = f'{CONTROL}."REFERENCE_DATA_RUNS"'
    clean = f"(SELECT {col_list} FROM {staging} WHERE {key_not_null} QUALIFY COUNT(*) OVER (PARTITION BY {key_list}) = 1)"

    return f"""-- {feed.name} ({feed.system}): load the newest snapshot files, compute the delta against the replica,
-- bring the replica in line and record the run with its row counts. Rendered by astra-data reference render.
CREATE OR REPLACE PROCEDURE {_procedure(feed)}(SNAPSHOT_DATE DATE)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Replicates {feed.name} from {feed.system} snapshots under {feed.file.folder}/. Records every run in CONTROL.REFERENCE_DATA_RUNS and every change in {feed.table}_CHANGES.'
AS
$$
DECLARE
  run_id STRING DEFAULT UUID_STRING();
  started_at TIMESTAMP_NTZ DEFAULT SYSDATE();
  snapshot DATE DEFAULT COALESCE(:SNAPSHOT_DATE, CURRENT_DATE());
  files_loaded INTEGER DEFAULT 0;
  rows_source INTEGER DEFAULT 0;
  rows_conflict INTEGER DEFAULT 0;
  rows_inserted INTEGER DEFAULT 0;
  rows_updated INTEGER DEFAULT 0;
  rows_deleted INTEGER DEFAULT 0;
  rows_unchanged INTEGER DEFAULT 0;
  rows_total INTEGER DEFAULT 0;
BEGIN
  -- 1. The newest snapshot: files under the feed's folder that no earlier run has loaded.
  TRUNCATE TABLE {staging};
  COPY INTO {staging} ({col_list}, "SOURCE_FILE", "SOURCE_ROW")
  FROM (SELECT {casts}, METADATA$FILENAME, METADATA$FILE_ROW_NUMBER FROM {STAGE}/{feed.file.folder}/)
  FILE_FORMAT = (FORMAT_NAME = '{FILE_FORMAT}')
  PATTERN = {_lit(feed.file.pattern)}
  ON_ERROR = 'ABORT_STATEMENT';
  SELECT COUNT(*), COUNT(DISTINCT "SOURCE_FILE") INTO :rows_source, :files_loaded FROM {staging};

  IF (files_loaded = 0) THEN
    INSERT INTO {runs} (RUN_ID, FEED_ID, DOMAIN, STARTED_AT, FINISHED_AT, STATUS, SNAPSHOT_DATE, FILES_LOADED, ROWS_SOURCE, ROWS_TOTAL)
    SELECT :run_id, '{feed.id}', '{reference.domain}', :started_at, SYSDATE(), 'skipped', :snapshot, 0, 0, COUNT(*) FROM {replica};
    RETURN 'skipped: no new snapshot files under {feed.file.folder}/';
  END IF;

  -- 2. Conflicts: keys blank or repeated in the snapshot stay out; the replica keeps what it had.
  INSERT INTO {conflicts} (RUN_ID, {key_list}, ROW_COUNT, REJECTION_CODE)
  SELECT :run_id, {key_list}, COUNT(*), '{feed.rejections.conflict}'
  FROM {staging}
  GROUP BY {key_list}
  HAVING COUNT(*) > 1 OR NOT ({key_not_null});
  SELECT COALESCE(SUM(ROW_COUNT), 0) INTO :rows_conflict FROM {conflicts} WHERE RUN_ID = :run_id;

  -- 3. The delta: what the snapshot removes, adds and changes.
  INSERT INTO {changes} (RUN_ID, CHANGED_AT, CHANGE, {key_list}, BEFORE, AFTER)
  SELECT :run_id, SYSDATE(), 'deleted', {", ".join(f"r.{_q(k)}" for k in feed.key)}, {before_json}, NULL
  FROM {replica} r
  WHERE NOT EXISTS (SELECT 1 FROM {clean} s WHERE {key_join})
    AND NOT EXISTS (SELECT 1 FROM {conflicts} c WHERE c.RUN_ID = :run_id AND {" AND ".join(f"c.{_q(k)} IS NOT DISTINCT FROM r.{_q(k)}" for k in feed.key)})
  UNION ALL
  SELECT :run_id, SYSDATE(), 'inserted', {", ".join(f"s.{_q(k)}" for k in feed.key)}, NULL, {after_json}
  FROM {clean} s
  WHERE NOT EXISTS (SELECT 1 FROM {replica} r WHERE {key_join})
  UNION ALL
  SELECT :run_id, SYSDATE(), 'updated', {", ".join(f"s.{_q(k)}" for k in feed.key)}, {before_json}, {after_json}
  FROM {clean} s
  JOIN {replica} r ON {key_join}
  WHERE {differs};
  SELECT COUNT_IF(CHANGE = 'inserted'), COUNT_IF(CHANGE = 'updated'), COUNT_IF(CHANGE = 'deleted')
    INTO :rows_inserted, :rows_updated, :rows_deleted
  FROM {changes} WHERE RUN_ID = :run_id;

  -- 4. Bring the replica in line.
  DELETE FROM {replica} r
  WHERE EXISTS (SELECT 1 FROM {changes} c WHERE c.RUN_ID = :run_id AND c.CHANGE = 'deleted' AND {" AND ".join(f"c.{_q(k)} = r.{_q(k)}" for k in feed.key)});
  MERGE INTO {replica} r
  USING {clean} s ON {key_join}
  WHEN MATCHED AND ({differs}) THEN UPDATE SET {updates}, "REPLICATED_AT" = SYSDATE(), "RUN_ID" = :run_id
  WHEN NOT MATCHED THEN INSERT ({col_list}, "REPLICATED_AT", "RUN_ID") VALUES ({", ".join(f"s.{_q(c)}" for c in cols)}, SYSDATE(), :run_id);
  SELECT COUNT(*) INTO :rows_total FROM {replica};
  rows_unchanged := rows_source - rows_conflict - rows_inserted - rows_updated;

  -- 5. The run and its counts.
  INSERT INTO {runs} (RUN_ID, FEED_ID, DOMAIN, STARTED_AT, FINISHED_AT, STATUS, SNAPSHOT_DATE, FILES_LOADED, ROWS_SOURCE, ROWS_CONFLICT, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED, ROWS_UNCHANGED, ROWS_TOTAL)
  VALUES (:run_id, '{feed.id}', '{reference.domain}', :started_at, SYSDATE(), 'succeeded', :snapshot, :files_loaded, :rows_source, :rows_conflict, :rows_inserted, :rows_updated, :rows_deleted, :rows_unchanged, :rows_total);
  RETURN 'succeeded: ' || :rows_source || ' source rows, ' || :rows_inserted || ' inserted, ' || :rows_updated || ' updated, ' || :rows_deleted || ' deleted, ' || :rows_conflict || ' in conflict; replica holds ' || :rows_total;
EXCEPTION
  WHEN OTHER THEN
    INSERT INTO {runs} (RUN_ID, FEED_ID, DOMAIN, STARTED_AT, FINISHED_AT, STATUS, SNAPSHOT_DATE, FILES_LOADED, ROWS_SOURCE, ERROR)
    VALUES (:run_id, '{feed.id}', '{reference.domain}', :started_at, SYSDATE(), 'failed', :snapshot, :files_loaded, :rows_source, :SQLERRM);
    RAISE;
END;
$$;
"""


def render_lookups(reference: ReferenceData) -> str:
    lines = [
        "-- Resolution views: one row per identifier a custodian record may carry, so resolution is a single join",
        "-- against the replica and never a call to the source system. Rendered by astra-data reference render.",
        "",
    ]
    for feed in reference.feeds:
        if not feed.resolves.identifiers:
            lines.append(f"-- {feed.name}: records resolve by the key ({', '.join(feed.key)}) directly against {_table(feed)}.")
            lines.append("")
            continue
        keys = ", ".join(_q(k) for k in feed.key)
        selects = [f"SELECT {keys}, '{name}' AS IDENTIFIER_TYPE, {_q(name)} AS IDENTIFIER_VALUE, {i} AS PRIORITY FROM {_table(feed)} WHERE {_q(name)} IS NOT NULL" for i, name in enumerate(feed.resolves.identifiers, start=1)]
        lines.append(f"-- {feed.name}: identifiers tried in order {', '.join(feed.resolves.identifiers)}; a value matching more than one key is {feed.rejections.ambiguous}, none is {feed.rejections.not_found}.")
        lines.append(f"CREATE OR REPLACE VIEW {_table(feed, '_IDENTIFIERS')}")
        identifiers = ", ".join(feed.resolves.identifiers)
        lines.append(f"COMMENT = 'Alternate identifiers of {feed.table} unpivoted for resolution joins: {identifiers}.'")
        lines.append("AS")
        lines.append("\nUNION ALL\n".join(selects) + ";")
        lines.append("")
    return "\n".join(lines)


def render_tasks(reference: ReferenceData) -> str:
    lines = [
        "-- One serverless task per feed on its schedule. Task failures are alerted by CONTROL.DETECT_TASK_FAILURES;",
        "-- a feed with no successful run within its expected interval by CONTROL.DETECT_STALE_REFERENCE_DATA.",
        "",
    ]
    for feed in reference.feeds:
        lines.append(f"CREATE OR REPLACE TASK {_task(feed)}")
        lines.append(f"  SCHEDULE = 'USING CRON {feed.schedule.cron} {feed.schedule.timezone}'")
        lines.append("  USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'")
        lines.append("  SUSPEND_TASK_AFTER_NUM_FAILURES = 10")
        lines.append(f"  COMMENT = 'Replicates {feed.name} ({feed.system}) every day at {feed.schedule.cron} {feed.schedule.timezone}; expected at least every {feed.expected_every_hours} hours.'")
        lines.append("AS")
        lines.append(f"  CALL {_procedure(feed)}(NULL);")
        lines.append(f"ALTER TASK {_task(feed)} RESUME;")
        lines.append("")
    return "\n".join(lines)


def render_tests(reference: ReferenceData) -> dict[str, str]:
    tests: dict[str, str] = {}
    runs = f'{CONTROL}."REFERENCE_DATA_RUNS"'
    for feed in reference.feeds:
        keys = ", ".join(_q(k) for k in feed.key)
        tests[f"{feed.id}_key_unique.sql"] = "\n".join(
            [
                f"-- {feed.name}: the key ({', '.join(feed.key)}) identifies one replica row. Returns keys with more than one.",
                f"SELECT {keys}, COUNT(*) AS ROW_COUNT",
                f"FROM {_table(feed)}",
                f"GROUP BY {keys}",
                "HAVING COUNT(*) > 1;",
                "",
            ]
        )
        tests[f"{feed.id}_last_run_not_failed.sql"] = "\n".join(
            [
                f"-- {feed.name}: the most recent replication run did not fail. Returns the run when it did.",
                "SELECT RUN_ID, STARTED_AT, STATUS, ERROR",
                f"FROM (SELECT * FROM {runs} WHERE FEED_ID = '{feed.id}' QUALIFY ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) = 1)",
                "WHERE STATUS = 'failed';",
                "",
            ]
        )
        tests[f"{feed.id}_fresh.sql"] = "\n".join(
            [
                f"-- {feed.name}: once replicated, replicated again within {feed.expected_every_hours} hours. Returns the feed when the last success is older.",
                "SELECT FEED_ID, MAX(FINISHED_AT) AS LAST_SUCCESS",
                f"FROM {runs}",
                f"WHERE FEED_ID = '{feed.id}' AND STATUS = 'succeeded'",
                "GROUP BY FEED_ID",
                f"HAVING MAX(FINISHED_AT) < DATEADD('hour', -{feed.expected_every_hours}, SYSDATE());",
                "",
            ]
        )
    return tests


def bundle_name(pack: DomainPack) -> str:
    return f"{pack.name.replace('_', '-')}{BUNDLE_SUFFIX}"


def render_bundle(pack: DomainPack) -> dict[str, str]:
    """Every file of the pack's reference-data bundle, keyed by path relative to the bundle directory."""
    reference = pack.reference_data
    if reference is None:
        raise ValueError(f"domain pack {pack.name} declares no reference data")
    files: dict[str, str] = {"ddl/reference_tables.sql": render_tables(reference)}
    for feed in reference.feeds:
        files[f"pipeline/replicate_{feed.id}.sql"] = render_procedure(reference, feed)
    files["pipeline/lookups.sql"] = render_lookups(reference)
    files["pipeline/tasks.sql"] = render_tasks(reference)
    files.update({f"tests/{name}": text for name, text in render_tests(reference).items()})

    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    steps = ["ddl/reference_tables.sql"] + [f"pipeline/replicate_{f.id}.sql" for f in reference.feeds] + ["pipeline/lookups.sql", "pipeline/tasks.sql"]
    manifest = [
        f"# Reference-data replication of the {pack.name} domain pack. Rendered by astra-data reference render from",
        f"# domains/{pack.name}/reference-data.yaml; the version is a digest of the rendered files. Do not edit.",
        f"bundle: {bundle_name(pack)}",
        f'version: "{digest.hexdigest()[:12]}"',
        f"source: {pack.name}_reference_data",
        "steps:",
        *[f"  - {step}" for step in steps],
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
            problems.append(Problem(_display(path, repo_root), None, "not rendered; run astra-data reference render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the feeds changed since it was rendered; run astra-data reference render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data reference render to remove it"))
    return problems


# -- CONTROL.REFERENCE_FEEDS -------------------------------------------------


def sync_statements(packs: list[DomainPack], target: Target, *, root: Path | None = None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> list[str]:
    """One transaction that makes CONTROL.REFERENCE_FEEDS match the packs' feeds; feeds that left are disabled."""
    table = f'"{target.environment_database}"."CONTROL"."REFERENCE_FEEDS"'
    updated_at = _lit(clock().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")) + "::TIMESTAMP_NTZ"
    statements = ["BEGIN TRANSACTION"]
    rows: list[str] = []
    for pack in packs:
        reference = pack.reference_data
        if reference is None:
            continue
        source = _display(reference.path, root)
        for feed in reference.feeds:
            rows.append("(" + ", ".join([_lit(feed.id), _lit(pack.name), _lit(feed.name), _lit(feed.system), _lit(feed.table), _lit(feed.schedule.cron), _lit(feed.schedule.timezone), str(feed.expected_every_hours), _lit(feed.stale_severity), _lit(source)]) + ")")
    if rows:
        statements.append(
            f"MERGE INTO {table} t "
            f"USING (SELECT * FROM VALUES {', '.join(rows)} AS v (FEED_ID, DOMAIN, NAME, SYSTEM, TABLE_NAME, SCHEDULE_CRON, TIMEZONE, EXPECTED_EVERY_HOURS, STALE_SEVERITY, SOURCE)) s "
            f"ON t.FEED_ID = s.FEED_ID AND t.DOMAIN = s.DOMAIN "
            f"WHEN MATCHED THEN UPDATE SET NAME = s.NAME, SYSTEM = s.SYSTEM, TABLE_NAME = s.TABLE_NAME, SCHEDULE_CRON = s.SCHEDULE_CRON, TIMEZONE = s.TIMEZONE, "
            f"EXPECTED_EVERY_HOURS = s.EXPECTED_EVERY_HOURS, STALE_SEVERITY = s.STALE_SEVERITY, ENABLED = TRUE, SOURCE = s.SOURCE, UPDATED_AT = {updated_at} "
            f"WHEN NOT MATCHED THEN INSERT (FEED_ID, DOMAIN, NAME, SYSTEM, TABLE_NAME, SCHEDULE_CRON, TIMEZONE, EXPECTED_EVERY_HOURS, STALE_SEVERITY, ENABLED, SOURCE, UPDATED_AT) "
            f"VALUES (s.FEED_ID, s.DOMAIN, s.NAME, s.SYSTEM, s.TABLE_NAME, s.SCHEDULE_CRON, s.TIMEZONE, s.EXPECTED_EVERY_HOURS, s.STALE_SEVERITY, TRUE, s.SOURCE, {updated_at})"
        )
    for pack in packs:
        keep = ", ".join(_lit(f.id) for f in (pack.reference_data.feeds if pack.reference_data else ()))
        condition = f"AND FEED_ID NOT IN ({keep})" if keep else ""
        statements.append(f"UPDATE {table} SET ENABLED = FALSE, UPDATED_AT = {updated_at} WHERE ENABLED AND DOMAIN = {_lit(pack.name)} {condition}".rstrip())
    statements.append("COMMIT")
    return statements


def sync(executor: Executor, packs: list[DomainPack], target: Target, root: Path | None = None) -> int:
    executor.execute_script(";\n".join(sync_statements(packs, target, root=root)) + ";")
    return sum(len(p.reference_data.feeds) for p in packs if p.reference_data)


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
