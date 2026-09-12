"""Dry-run a drafted config in a sandbox: sample files in, a report out, the sandbox gone (S4.1.1, ADR 0030).

A BSA drafts a config and has sample files from the custodian. Before
asking for promotion they want to know what the pipeline would make of
them. The dry run compiles and renders the config exactly as the deploy
pipeline would, creates a sandbox database (S1.2.3), deploys the canonical
model, the reference-data tables and the source bundle into it, stages the
sample files and loads them the way Snowpipe would, refreshes the parse
tables, runs the process procedure (intake, merge, resolve), and reads
back what happened: rows parsed per record, rows and files rejected by
code, the rendered tests and the control-total gaps, the run ledger. The
report is written to a directory as Markdown and JSON, and the sandbox is
destroyed whatever happened.

What the sandbox does not have: the landing pipe (files are loaded from an
internal stage), the tasks DAG (the process procedure is called directly)
and the data metric function bindings (the SANDBOX role may not bind
them; the same checks run as the rendered tests and the gap query).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_core.problems import Problem
from astra_data.bundle import Bundle, Executor, Target, deploy, load_bundle, render, run_tests
from astra_data.compiler import CompileError, CompiledConfig, compile_config
from astra_data.render import RenderError, write_bundle
from astra_data.render.dq import gap_sql
from astra_data.render.names import custodian_folder, delivery_patterns, exceptions_table, file_metadata_table, files_table, parse_problems_table, procedure, raw_lines_table, record_table, runs_table, silver_table
from astra_verification.sandbox import SandboxSpec, create, destroy

BUDGET_SECONDS = 600
SKIPPED_STEPS = ("pipe.sql", "tasks.sql", "dmf_")  # no landing pipe, no DAG, no DMF bindings in a sandbox
STAGE = "DRYRUN"
CONTROL_TABLES = ("FILE_LOAD_LOG", "MERGE_LOG", "REJECTION_CODES", "CUSTODIAN_RUNS", "ALERTS")


class DryRunError(RuntimeError):
    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.format() for p in problems))


@dataclass(frozen=True)
class Sample:
    path: Path
    file_name: str  # as the pipe would see it: <custodian folder>/<name>
    lines: int


@dataclass
class Phase:
    name: str
    seconds: float
    detail: str = ""


@dataclass
class DryRunReport:
    config: str
    source: str
    bundle_version: str
    sandbox: str
    task_id: str
    started_at: str
    samples: list[Sample]
    phases: list[Phase] = field(default_factory=list)
    run_id: str = ""
    parsed: dict[str, int] = field(default_factory=dict)  # record label -> rows
    files: list[dict] = field(default_factory=list)  # per file: metadata row
    parse_problems: list[dict] = field(default_factory=list)  # code, level, rows
    exceptions: list[dict] = field(default_factory=list)  # code, level, stage, rows
    ledger: dict = field(default_factory=dict)
    silver_rows: int | None = None
    canonical_rows: int | None = None
    control_totals: list[dict] = field(default_factory=list)  # rule, file, trailer, actual, gap
    tests: list[dict] = field(default_factory=list)  # test, passed, failing_rows, sample
    destroyed: bool = False
    error: str | None = None
    seconds: float = 0.0

    @property
    def rejected_rows(self) -> int:
        return sum(e["rows"] for e in self.exceptions if e["level"] == "record")

    @property
    def within_budget(self) -> bool:
        return self.seconds <= BUDGET_SECONDS

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        return "ran" if self.run_id else "incomplete"

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "source": self.source,
            "bundle_version": self.bundle_version,
            "sandbox": self.sandbox,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "seconds": round(self.seconds, 1),
            "budget_seconds": BUDGET_SECONDS,
            "within_budget": self.within_budget,
            "samples": [{"file": s.file_name, "lines": s.lines} for s in self.samples],
            "phases": [{"phase": p.name, "seconds": round(p.seconds, 1), "detail": p.detail} for p in self.phases],
            "run_id": self.run_id,
            "parsed": self.parsed,
            "files": self.files,
            "parse_problems": self.parse_problems,
            "exceptions": self.exceptions,
            "rejected_rows": self.rejected_rows,
            "ledger": self.ledger,
            "silver_rows": self.silver_rows,
            "canonical_rows": self.canonical_rows,
            "control_totals": self.control_totals,
            "tests": self.tests,
            "destroyed": self.destroyed,
            "error": self.error,
        }


# -- inputs ------------------------------------------------------------------


def compile_and_render(config: Path, repo: Path, out: Path) -> tuple[CompiledConfig, Bundle]:
    """The config exactly as the deploy pipeline would take it. Raises DryRunError with every problem."""
    registry, problems = Registry.load(repo / "specs", repo)
    if problems:
        raise DryRunError(problems)
    catalog, problems = Catalog.load(repo / "rules", repo, registry)
    if problems:
        raise DryRunError(problems)
    packs, problems = load_packs(repo / "domains", repo)
    if problems:
        raise DryRunError(problems)
    try:
        compiled = compile_config(config, registry=registry, catalog=catalog, packs=packs, root=repo)
        root = write_bundle(compiled, out)
    except (CompileError, RenderError) as exc:
        raise DryRunError(exc.problems) from exc
    return compiled, load_bundle(root, repo)


def samples_for(compiled: CompiledConfig, paths: list[Path]) -> list[Sample]:
    """Each sample file under the custodian's folder, as the pipe would name it, with its line count; a name no delivery pattern matches is refused."""
    folder = (custodian_folder(compiled) or compiled.source["custodian"]).strip("/")
    patterns = [re.compile("^" + re.escape(p).replace("%", ".*").replace("_", ".") + "$") for p in delivery_patterns(compiled)]
    samples: list[Sample] = []
    problems: list[Problem] = []
    for path in paths:
        if not path.is_file():
            problems.append(Problem(str(path), None, "no such sample file"))
            continue
        name = f"{folder}/{path.name}"
        if not any(p.match(name) for p in patterns):
            problems.append(Problem(str(path), None, f"{name} matches none of the delivery patterns ({', '.join(delivery_patterns(compiled))}); the pipe would not load it, so the dry run does not either"))
            continue
        lines = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
        samples.append(Sample(path, name, lines))
    if problems:
        raise DryRunError(problems)
    if not samples:
        raise DryRunError([Problem(str(compiled.path), None, "at least one sample file is needed")])
    return samples


def sandbox_bundle(bundle: Bundle) -> Bundle:
    """The source bundle without the steps a sandbox cannot take: the landing pipe, the tasks DAG, the DMF bindings."""
    steps = tuple(s for s in bundle.steps if not any(s.name.endswith(x) or s.name.startswith(x) for x in SKIPPED_STEPS))
    return replace(bundle, steps=steps)


# -- the statements ----------------------------------------------------------


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def control_statements(spec: SandboxSpec) -> list[str]:
    """The control tables the bundle writes, shaped like the environment's; the rejection taxonomy copied so codes resolve."""
    env = f'"{spec.environment_database}"."CONTROL"'
    sbx = f'"{spec.database}"."CONTROL"'
    statements = [f'CREATE ICEBERG TABLE {sbx}."{t}" LIKE {env}."{t}"' for t in CONTROL_TABLES]
    statements.append(f'INSERT INTO {sbx}."REJECTION_CODES" SELECT * FROM {env}."REJECTION_CODES"')
    return statements


def stage_statements(spec: SandboxSpec) -> list[str]:
    return [
        f'CREATE STAGE "{spec.database}"."BRONZE"."{STAGE}" COMMENT = \'Sample files of the dry run; loaded the way the landing pipe loads them.\'',
    ]


def put_statement(spec: SandboxSpec, sample: Sample) -> str:
    folder = sample.file_name.rsplit("/", 1)[0]
    return f'PUT file://{sample.path.resolve().as_posix()} @"{spec.database}"."BRONZE"."{STAGE}"/{folder}/ AUTO_COMPRESS = FALSE OVERWRITE = TRUE'


def load_statements(spec: SandboxSpec, compiled: CompiledConfig, samples: list[Sample]) -> list[str]:
    """COPY the staged files into the raw lines table as the pipe does, and log them as the reconcile task would."""
    db = f'"{spec.database}"'
    table = f'{db}."BRONZE"."{raw_lines_table(compiled)}"'
    folder = samples[0].file_name.rsplit("/", 1)[0]
    files = ", ".join(_lit(s.file_name.rsplit("/", 1)[1]) for s in samples)
    statements = [
        f'COPY INTO {table} ("FILE_NAME", "ROW_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT")\n'
        f"FROM (\n  SELECT METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, $1, METADATA$FILE_CONTENT_KEY, METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME\n"
        f'  FROM @{db}."BRONZE"."{STAGE}"/{folder}/\n)\n'
        f"FILES = ({files})\n"
        "FILE_FORMAT = (TYPE = CSV FIELD_DELIMITER = NONE SKIP_HEADER = 0 SKIP_BLANK_LINES = FALSE TRIM_SPACE = FALSE EMPTY_FIELD_AS_NULL = FALSE ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE ESCAPE = NONE ESCAPE_UNENCLOSED_FIELD = NONE FIELD_OPTIONALLY_ENCLOSED_BY = NONE ENCODING = 'UTF8')\n"
        "ON_ERROR = ABORT_STATEMENT"
    ]
    for sample in samples:
        statements.append(
            f'INSERT INTO {db}."CONTROL"."FILE_LOAD_LOG" ("FILE_NAME", "FILE_LAST_MODIFIED", "STATUS", "FILE_HASH", "FILE_SIZE", "ROW_COUNT", "DETAIL", "OBSERVED_AT")\n'
            f"SELECT {_lit(sample.file_name)}, SYSDATE(), 'LOADED', MD5({_lit(sample.file_name)}), {sample.path.stat().st_size}, {sample.lines}, 'Dry run: loaded from the sandbox stage', SYSDATE()"
        )
    return statements


def refresh_statements(spec: SandboxSpec, compiled: CompiledConfig) -> list[str]:
    """Refresh the parse dynamic tables now, in dependency order, instead of waiting for the target lag."""
    db = f'"{spec.database}"'
    tables = [record_table(compiled, r.label) for r in compiled.spec.records if r.type == "detail"]
    tables += [parse_problems_table(compiled), file_metadata_table(compiled)]
    return [f'ALTER DYNAMIC TABLE {db}."BRONZE"."{t}" REFRESH' for t in tables]


def process_statement(spec: SandboxSpec, compiled: CompiledConfig) -> str:
    return f'CALL "{spec.database}"."BRONZE"."{procedure(compiled, "PROCESS")}"()'


# -- reading back ------------------------------------------------------------


def report_queries(spec: SandboxSpec, compiled: CompiledConfig, run_id: str) -> dict[str, str]:
    db = f'"{spec.database}"'
    queries: dict[str, str] = {}
    for record in compiled.spec.records:
        if record.type == "detail":
            queries[f"parsed:{record.label}"] = f'SELECT COUNT(*) FROM {db}."BRONZE"."{record_table(compiled, record.label)}"'
    queries["files"] = f'SELECT * FROM {db}."BRONZE"."{file_metadata_table(compiled)}" ORDER BY "FILE_NAME"'
    queries["parse_problems"] = f'SELECT "CODE", "LEVEL", COUNT(*) FROM {db}."BRONZE"."{parse_problems_table(compiled)}" GROUP BY "CODE", "LEVEL" ORDER BY "LEVEL", "CODE"'
    queries["exceptions"] = f'SELECT "REJECTION_CODE", "LEVEL", "STAGE", COUNT(*) FROM {db}."EXCEPTIONS"."{exceptions_table(compiled)}" WHERE "RUN_ID" = {_lit(run_id)} GROUP BY "REJECTION_CODE", "LEVEL", "STAGE" ORDER BY "LEVEL", "STAGE", "REJECTION_CODE"'
    queries["ledger"] = f'SELECT "FILES_REGISTERED", "FILES_MERGED", "FILES_REJECTED", "ROWS_MERGED", "ROWS_PROJECTED", "ROWS_REJECTED", "EXCEPTIONS_FILE", "EXCEPTIONS_RECORD", "EXCEPTIONS_FIELD" FROM {db}."BRONZE"."{runs_table(compiled)}" WHERE "RUN_ID" = {_lit(run_id)}'
    queries["file_status"] = f'SELECT "FILE_NAME", "STATUS", "ROW_COUNT" FROM {db}."BRONZE"."{files_table(compiled)}" ORDER BY "FILE_NAME"'
    if compiled.spec.merge:
        queries["silver_rows"] = f'SELECT COUNT(*) FROM {db}."SILVER"."{silver_table(compiled)}" WHERE "RETIRED_AT" IS NULL'
    if compiled.target_entity is not None:
        queries["canonical_rows"] = f'SELECT COUNT(*) FROM {db}."SILVER"."{compiled.target_entity.table}" WHERE "CUSTODIAN_ID" = {_lit(compiled.source["custodian"])}'
    for rule in compiled.dq_rules:
        if rule.kind == "control_total":
            trailer, total = (f'"{c.name}"' for c in rule.columns)
            queries[f"control_total:{rule.id}"] = f'SELECT "FILE_NAME", {trailer}, {total}, {gap_sql(rule)} FROM {db}."BRONZE"."{file_metadata_table(compiled)}" ORDER BY "FILE_NAME"'
    return queries


def _describe_columns(executor: Executor, spec: SandboxSpec, compiled: CompiledConfig) -> list[str]:
    rows = executor.query(f'SELECT "COLUMN_NAME" FROM "{spec.database}".INFORMATION_SCHEMA.COLUMNS WHERE "TABLE_SCHEMA" = \'BRONZE\' AND "TABLE_NAME" = {_lit(file_metadata_table(compiled))} ORDER BY "ORDINAL_POSITION"')
    return [r[0] for r in rows]


# -- the run -----------------------------------------------------------------


def dry_run(
    config: Path,
    samples: list[Path],
    spec: SandboxSpec,
    executor: Executor,
    *,
    repo: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    cdm_ddl: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    on_before_destroy: Callable[[Executor, SandboxSpec, CompiledConfig, DryRunReport], None] | None = None,
) -> DryRunReport:
    """Everything above, in order, in one sandbox that is destroyed at the end.

    `on_before_destroy`, when given, runs after the report is read back and before the
    sandbox is dropped, with the executor, spec, compiled config and report; a caller
    that needs more than the report (S4.1.3's replay reads the canonical rows) does its
    own extra queries there, while the sandbox still exists. An exception it raises is
    recorded like any other failure and still leaves the sandbox destroyed.
    """
    started = monotonic()
    started_at = clock()
    spec = replace(spec, schemas=tuple(dict.fromkeys(spec.schemas + ("REFERENCE",))))  # the reference replicas live there
    work = Path(out) / "bundle"
    compiled, bundle = compile_and_render(Path(config), repo, work)
    files = samples_for(compiled, [Path(p) for p in samples])
    report = DryRunReport(str(config), compiled.id, bundle.version, spec.database, spec.task_id, started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"), files)
    target = spec.target()

    def phase(name: str, since: float, detail: str = "") -> None:
        report.phases.append(Phase(name, monotonic() - since, detail))

    t = monotonic()
    created = create(executor, spec)
    phase("sandbox created", t, f"{spec.database}, {spec.warehouse}")
    try:
        t = monotonic()
        for statement in control_statements(spec):
            executor.execute_script(statement)
        if cdm_ddl is not None:
            executor.execute_script(render(cdm_ddl.read_text(encoding="utf-8"), target))
        deployed = []
        for extra in extra_bundles:
            deployed.append(deploy(load_bundle(Path(extra), repo), target, executor).bundle)
        deployed.append(deploy(sandbox_bundle(bundle), target, executor).bundle)
        phase("deployed", t, ", ".join(deployed) + (" and the canonical model" if cdm_ddl else ""))

        t = monotonic()
        for statement in stage_statements(spec):
            executor.execute_script(statement)
        for sample in files:
            executor.execute_script(put_statement(spec, sample))
        for statement in load_statements(spec, compiled, files):
            executor.execute_script(statement)
        phase("samples loaded", t, f"{len(files)} file(s), {sum(s.lines for s in files)} lines")

        t = monotonic()
        for statement in refresh_statements(spec, compiled):
            executor.execute_script(statement)
        phase("parsed", t)

        t = monotonic()
        rows = executor.query(process_statement(spec, compiled))
        report.run_id = str(rows[0][0]) if rows and rows[0] else ""
        phase("processed", t, f"run {report.run_id}")

        t = monotonic()
        _read_back(executor, spec, compiled, report)
        report.tests = [{"test": r.test, "passed": r.passed, "failing_rows": r.failing_rows, "sample": [list(map(str, row)) for row in r.sample]} for r in run_tests(bundle, target, executor)]
        phase("reported", t, f"{sum(1 for x in report.tests if x['passed'])} of {len(report.tests)} tests passed")
        if on_before_destroy is not None:
            on_before_destroy(executor, spec, compiled, report)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:500] if str(exc) else ''}"
    finally:
        t = monotonic()
        destroy(executor, spec, reason="task_failed" if report.error else "task_done", detail=f"dry run {compiled.id}"[:1000])
        report.destroyed = True
        phase("sandbox destroyed", t)
    report.seconds = monotonic() - started
    write_report(report, Path(out))
    return report


def _read_back(executor: Executor, spec: SandboxSpec, compiled: CompiledConfig, report: DryRunReport) -> None:
    queries = report_queries(spec, compiled, report.run_id)
    columns = _describe_columns(executor, spec, compiled)
    for key, sql in queries.items():
        rows = executor.query(sql)
        if key.startswith("parsed:"):
            report.parsed[key.split(":", 1)[1]] = int(rows[0][0]) if rows else 0
        elif key == "files":
            report.files = [dict(zip(columns, [None if v is None else str(v) for v in row])) if columns else {"row": [str(v) for v in row]} for row in rows]
        elif key == "parse_problems":
            report.parse_problems = [{"code": r[0], "level": r[1], "rows": int(r[2])} for r in rows]
        elif key == "exceptions":
            report.exceptions = [{"code": r[0], "level": r[1], "stage": r[2], "rows": int(r[3])} for r in rows]
        elif key == "ledger" and rows:
            names = ["files_registered", "files_merged", "files_rejected", "rows_merged", "rows_projected", "rows_rejected", "exceptions_file", "exceptions_record", "exceptions_field"]
            report.ledger = {n: int(v) for n, v in zip(names, rows[0])}
        elif key == "file_status":
            statuses = {r[0]: r[1] for r in rows}
            for f in report.files:
                f["status"] = statuses.get(f.get("FILE_NAME"), "not registered")
        elif key == "silver_rows":
            report.silver_rows = int(rows[0][0]) if rows else 0
        elif key == "canonical_rows":
            report.canonical_rows = int(rows[0][0]) if rows else 0
        elif key.startswith("control_total:"):
            rule = key.split(":", 1)[1]
            for r in rows:
                report.control_totals.append({"rule": rule, "file": r[0], "trailer": None if r[1] is None else str(r[1]), "actual": None if r[2] is None else str(r[2]), "gap": None if r[3] is None else str(r[3])})


# -- the report --------------------------------------------------------------


def render_markdown(report: DryRunReport) -> str:
    out = [f"# Dry run: {report.source}", ""]
    out.append(f"Config `{report.config}`, bundle version `{report.bundle_version}`, sandbox `{report.sandbox}` (destroyed: {'yes' if report.destroyed else 'no'}). Started {report.started_at}; {report.seconds:.1f} s of a {BUDGET_SECONDS} s budget{'' if report.within_budget else ', over budget'}. Status: {report.status}.")
    out.append("")
    if report.error:
        out.append(f"**Stopped:** {report.error}")
        out.append("")
    out.append("| Phase | Seconds | |")
    out.append("|---|---|---|")
    for p in report.phases:
        out.append(f"| {p.name} | {p.seconds:.1f} | {p.detail} |")
    out.append("")
    out.append("## Samples")
    out.append("")
    out.append("| File | Lines | Status | Line count | Excluded rows | Field problems | File problems |")
    out.append("|---|---|---|---|---|---|---|")
    by_name = {f.get("FILE_NAME"): f for f in report.files}
    for s in report.samples:
        f = by_name.get(s.file_name, {})
        out.append(f"| `{s.file_name}` | {s.lines} | {f.get('status', 'not parsed')} | {f.get('LINE_COUNT', '')} | {f.get('EXCLUDED_ROWS', '')} | {f.get('FIELD_PROBLEMS', '')} | {f.get('FILE_PROBLEMS', '')} |")
    out.append("")
    out.append("## Rows parsed")
    out.append("")
    if report.parsed:
        out.append("| Record | Rows |")
        out.append("|---|---|")
        for label, rows in report.parsed.items():
            out.append(f"| {label} | {rows} |")
    else:
        out.append("No record table was read.")
    out.append("")
    if report.silver_rows is not None or report.canonical_rows is not None:
        out.append(f"Silver: {report.silver_rows if report.silver_rows is not None else 'n/a'} active rows; canonical entity: {report.canonical_rows if report.canonical_rows is not None else 'n/a'} rows for the custodian.")
        out.append("")
    out.append("## Rejected by code")
    out.append("")
    out.append(f"Record-level exceptions this run: {report.rejected_rows} rows. Parse problems of the samples (all runs):")
    out.append("")
    if report.parse_problems:
        out.append("| Parse code | Level | Rows |")
        out.append("|---|---|---|")
        for p in report.parse_problems:
            out.append(f"| `{p['code']}` | {p['level']} | {p['rows']} |")
    else:
        out.append("None.")
    out.append("")
    if report.exceptions:
        out.append("| Exception code | Level | Stage | Rows |")
        out.append("|---|---|---|---|")
        for e in report.exceptions:
            out.append(f"| `{e['code']}` | {e['level']} | {e['stage']} | {e['rows']} |")
    else:
        out.append("No exceptions were raised by the run." if report.run_id else "The run did not reach the exception store.")
    out.append("")
    if report.ledger:
        out.append("Run ledger: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in report.ledger.items()) + ".")
        out.append("")
    out.append("## Control totals")
    out.append("")
    if report.control_totals:
        out.append("| Rule | File | Trailer | Actual | Gap |")
        out.append("|---|---|---|---|---|")
        for c in report.control_totals:
            out.append(f"| `{c['rule']}` | `{c['file']}` | {c['trailer'] if c['trailer'] is not None else ''} | {c['actual'] if c['actual'] is not None else ''} | {c['gap'] if c['gap'] is not None else ''} |")
    else:
        out.append("The config has no control-total rule." if report.run_id else "Not measured.")
    out.append("")
    out.append("## DQ results")
    out.append("")
    if report.tests:
        out.append("| Test | Result | Failing rows |")
        out.append("|---|---|---|")
        for t in report.tests:
            out.append(f"| `{t['test']}` | {'pass' if t['passed'] else 'FAIL'} | {t['failing_rows']} |")
    else:
        out.append("The rendered tests were not run.")
    out.append("")
    return "\n".join(out)


def write_report(report: DryRunReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "report.md"
    data = out / "report.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
