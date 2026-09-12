"""3x volume test: a custodian's whole daily set at N times normal volume, proving the 20-minute window for a peak day (S4.3.1, ADR 0037).

A dry run (S4.1.1) proves one source's pipeline is correct on a sample; it
deliberately skips the landing pipe, the Tasks DAG and the DMF bindings to
stay fast and deterministic. A volume test asks a different question —
not "is it correct" but "does it finish in time at peak load" — for the
custodian's *whole* daily set (every source with a delivery block) and
through to where a consumer can actually read the day: the Gold publish
that writes the watermark last (ADR 0026).

Rather than re-running the pipeline once per business day at real volume
(which needs synthetic data this platform has no generator for yet), the
test inflates a normal day's own sample files N times over: N byte-
identical copies of each file, renamed so the custodian's own delivery
pattern still matches them. Bronze parsing, resolution and merge see N
times the files and N times the rows flow through compute — the thing a
volume test measures — even though a natural-key MERGE means the Silver
result is the same day it always was, since N copies of the same account
and security are one position, not N. The report says so plainly, so a
reviewer never mistakes the Silver or Gold row count for N times a normal
day's distinct data.

The sandbox skips the same three things a dry run does (no live Tasks
schedule to wait on), and stands in for the two the custodian's Tasks DAG
would otherwise do for it, exactly the way S4.1.1 already logs a loaded
file "as the reconcile task would": a CONTROL.CUSTODIAN_RUNS row "as
CUSTODIAN_GATE would" once every source has processed, then
CONTROL.PUBLISH_GOLD called directly. End-to-end is measured from the
last inflated file's load to the watermark write that publish leaves
behind — the same two ends operations would read off CUSTODIAN_RUNS and
GOLD.WATERMARK in production.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from astra_data.bundle import Bundle, Executor, deploy, load_bundle, render
from astra_data.compiler import CompiledConfig
from astra_data.render.names import custodian_folder, delivery_patterns

from astra_verification.dryrun import (
    Phase,
    Sample,
    compile_and_render,
    control_statements,
    load_statements,
    process_statement,
    put_statement,
    refresh_statements,
    sandbox_bundle,
    samples_for,
    stage_statements,
)
from astra_verification.sandbox import SandboxSpec, create, destroy

VOLUME_BUDGET_SECONDS = 1200  # the 20-minute window a peak day must fit inside, after the last file


class VolumeError(RuntimeError):
    pass


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def inflate(samples: list[Sample], factor: int, out_dir: Path) -> list[Sample]:
    """`factor` byte-identical copies of each sample, renamed so the delivery pattern's own wildcard still matches — N times the files and rows, none of them synthesized."""
    if factor < 1:
        raise VolumeError(f"factor must be at least 1; found {factor}")
    out_dir.mkdir(parents=True, exist_ok=True)
    inflated: list[Sample] = []
    for sample in samples:
        data = sample.path.read_bytes()
        folder, name = sample.file_name.rsplit("/", 1)
        stem, suffix = Path(name).stem, Path(name).suffix
        for copy in range(1, factor + 1):
            copy_name = f"{stem}-x{copy}{suffix}"
            copy_path = out_dir / copy_name
            copy_path.write_bytes(data)
            inflated.append(Sample(copy_path, f"{folder}/{copy_name}", sample.lines))
    return inflated


def _matches_source(compiled: CompiledConfig, path: Path) -> bool:
    """Whether a sample file belongs to this source's own delivery patterns, without astra_verification.dryrun.samples_for's all-or-nothing validation — a multi-source volume test routes one pool of files across several sources."""
    folder = (custodian_folder(compiled) or compiled.source["custodian"]).strip("/")
    patterns = [re.compile("^" + re.escape(p).replace("%", ".*").replace("_", ".") + "$") for p in delivery_patterns(compiled)]
    name = f"{folder}/{path.name}"
    return any(p.match(name) for p in patterns)


# -- the custodian DAG's two ends, done the way the DAG would --------------------


def custodian_run_statement(spec: SandboxSpec, custodian: str, business_date: date, run_id: str, files: int, latest_arrival_at: datetime) -> str:
    """A CONTROL.CUSTODIAN_RUNS row as CONTROL.CUSTODIAN_GATE would write once the day's set is complete (ADR 0024) — the DAG's start signal, done by hand because the sandbox has no live Tasks schedule to wait on."""
    db = f'"{spec.database}"'
    arrival = latest_arrival_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f'INSERT INTO {db}."CONTROL"."CUSTODIAN_RUNS" ("RUN_ID", "CUSTODIAN_ID", "BUSINESS_DATE", "STARTED_AT", "REASON", "FILES", "LATEST_ARRIVAL_AT", "AFTER_CUTOFF", "LATE_FILES")\n'
        f"SELECT {_lit(run_id)}, {_lit(custodian)}, {_lit(business_date.isoformat())}::DATE, SYSDATE(), 'complete', {files}, {_lit(arrival)}::TIMESTAMP_NTZ, FALSE, NULL"
    )


def publish_statement(spec: SandboxSpec, custodian: str) -> str:
    return f'CALL "{spec.database}"."CONTROL"."PUBLISH_GOLD"({_lit(custodian)})'


def watermark_query(spec: SandboxSpec, custodian: str, business_date: date) -> str:
    return f'SELECT "ROWS", "PUBLISHED_AT" FROM "{spec.database}"."GOLD"."WATERMARK" WHERE "CUSTODIAN_ID" = {_lit(custodian)} AND "BUSINESS_DATE" = {_lit(business_date.isoformat())}::DATE'


# -- the report --------------------------------------------------------------


@dataclass
class SourceRun:
    config: str
    id: str
    files_loaded: int
    lines_loaded: int
    run_id: str = ""

    def to_dict(self) -> dict:
        return {"config": self.config, "id": self.id, "files_loaded": self.files_loaded, "lines_loaded": self.lines_loaded, "run_id": self.run_id}


@dataclass
class VolumeReport:
    custodian: str
    sandbox: str
    warehouse_size: str
    task_id: str
    started_at: str
    factor: int
    business_date: str
    sources: list[SourceRun] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    last_file_loaded_at: str | None = None
    publish_result: str | None = None
    gold_rows: int | None = None
    watermark_published_at: str | None = None
    seconds_end_to_end: float | None = None
    destroyed: bool = False
    seconds: float = 0.0
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        return "published" if self.publish_result else "incomplete"

    @property
    def within_budget(self) -> bool:
        return self.seconds_end_to_end is not None and self.seconds_end_to_end <= VOLUME_BUDGET_SECONDS

    @property
    def total_files(self) -> int:
        return sum(s.files_loaded for s in self.sources)

    @property
    def total_lines(self) -> int:
        return sum(s.lines_loaded for s in self.sources)

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "sandbox": self.sandbox,
            "warehouse_size": self.warehouse_size,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "factor": self.factor,
            "business_date": self.business_date,
            "status": self.status,
            "sources": [s.to_dict() for s in self.sources],
            "phases": [{"phase": p.name, "seconds": round(p.seconds, 1), "detail": p.detail} for p in self.phases],
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "last_file_loaded_at": self.last_file_loaded_at,
            "publish_result": self.publish_result,
            "gold_rows": self.gold_rows,
            "watermark_published_at": self.watermark_published_at,
            "seconds_end_to_end": None if self.seconds_end_to_end is None else round(self.seconds_end_to_end, 1),
            "budget_seconds": VOLUME_BUDGET_SECONDS,
            "within_budget": self.within_budget,
            "seconds": round(self.seconds, 1),
            "destroyed": self.destroyed,
            "error": self.error,
        }


def single_custodian(compiled: Iterable[CompiledConfig]) -> str:
    """Every given config's own custodian, when they all agree; a custodian's daily set is one custodian by definition."""
    custodians = {c.source["custodian"] for c in compiled}
    if len(custodians) != 1:
        raise VolumeError(f"every --config must belong to the same custodian; found {', '.join(sorted(custodians))}")
    return custodians.pop()


# -- the run -----------------------------------------------------------------


def run(
    configs: list[Path],
    samples: list[Path],
    factor: int,
    business_date: date,
    spec: SandboxSpec,
    executor: Executor,
    *,
    repo: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    gold_bundle: Path = Path("releases/custodial-gold"),
    cdm_ddl: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> VolumeReport:
    """Every source of a custodian's daily set, at `factor` times its sample files, in one sandbox destroyed at the end.

    Raises whatever `compile_and_render` raises (astra_verification.dryrun.DryRunError,
    a compile problem) or VolumeError (the configs are not all one custodian) before any
    sandbox is created; everything after that is recorded in the report, not raised.
    """
    started = monotonic()
    started_at = clock()
    work = Path(out) / "bundle"
    compiled_bundles: list[tuple[CompiledConfig, Bundle]] = [compile_and_render(Path(c), repo, work / Path(c).stem) for c in configs]
    custodian = single_custodian(compiled for compiled, _bundle in compiled_bundles)

    report = VolumeReport(custodian, spec.database, spec.warehouse_size, spec.task_id, started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"), factor, business_date.isoformat())
    target = spec.target()

    def phase(name: str, since: float, detail: str = "") -> None:
        report.phases.append(Phase(name, monotonic() - since, detail))

    t = monotonic()
    create(executor, spec)
    phase("sandbox created", t, f"{spec.database}, {spec.warehouse} ({spec.warehouse_size})")
    try:
        t = monotonic()
        for statement in control_statements(spec):
            executor.execute_script(statement)
        if cdm_ddl is not None:
            executor.execute_script(render(cdm_ddl.read_text(encoding="utf-8"), target))
        deployed = []
        for extra in list(extra_bundles) + [gold_bundle]:
            deployed.append(deploy(load_bundle(Path(extra), repo), target, executor).bundle)
        for compiled, bundle in compiled_bundles:
            deployed.append(deploy(sandbox_bundle(bundle), target, executor).bundle)
        phase("deployed", t, ", ".join(deployed) + (" and the canonical model" if cdm_ddl else ""))

        t = monotonic()
        for statement in stage_statements(spec):
            executor.execute_script(statement)
        last_loaded_at = started_at
        for compiled, _bundle in compiled_bundles:
            matched = [p for p in samples if _matches_source(compiled, Path(p))]
            if not matched:
                report.sources.append(SourceRun(str(compiled.path), compiled.id, 0, 0))
                continue
            files = samples_for(compiled, matched)
            inflated = inflate(files, factor, Path(out) / "inflated" / compiled.id)
            for sample in inflated:
                executor.execute_script(put_statement(spec, sample))
            for statement in load_statements(spec, compiled, inflated):
                executor.execute_script(statement)
            report.sources.append(SourceRun(str(compiled.path), compiled.id, len(inflated), sum(s.lines for s in inflated)))
            last_loaded_at = clock()
        report.last_file_loaded_at = last_loaded_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        phase("samples loaded", t, f"{report.total_files} file(s) ({factor}x normal), {report.total_lines} line(s)")

        t = monotonic()
        by_id = {compiled.id: compiled for compiled, _ in compiled_bundles}
        for compiled, _bundle in compiled_bundles:
            if not any(s.id == compiled.id and s.files_loaded for s in report.sources):
                continue
            for statement in refresh_statements(spec, compiled):
                executor.execute_script(statement)
            rows = executor.query(process_statement(spec, compiled))
            run_id = str(rows[0][0]) if rows and rows[0] else ""
            for s in report.sources:
                if s.id == compiled.id:
                    s.run_id = run_id
        phase("processed", t, ", ".join(s.id for s in report.sources if s.run_id))

        t = monotonic()
        custodian_run_id = str(uuid.uuid4())
        executor.execute_script(custodian_run_statement(spec, custodian, business_date, custodian_run_id, report.total_files, last_loaded_at))
        rows = executor.query(publish_statement(spec, custodian))
        report.publish_result = str(rows[0][0]) if rows and rows[0] else None
        published_at = clock()
        wm = executor.query(watermark_query(spec, custodian, business_date))
        if wm:
            report.gold_rows = None if wm[0][0] is None else int(wm[0][0])
            report.watermark_published_at = None if wm[0][1] is None else str(wm[0][1])
        phase("published", t, report.publish_result or "")

        report.seconds_end_to_end = (published_at - last_loaded_at).total_seconds()
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:500] if str(exc) else ''}"
    finally:
        t = monotonic()
        destroy(executor, spec, reason="task_failed" if report.error else "task_done", detail=f"volume test {custodian} {factor}x"[:1000])
        report.destroyed = True
        phase("sandbox destroyed", t)
    report.seconds = monotonic() - started
    write_report(report, Path(out))
    return report


# -- the report --------------------------------------------------------------


def render_markdown(report: VolumeReport) -> str:
    out = [f"# {report.factor}x volume test: {report.custodian} {report.business_date}", ""]
    out.append(
        f"Sandbox `{report.sandbox}` on warehouse size **{report.warehouse_size}** (destroyed: {'yes' if report.destroyed else 'no'}). "
        f"Started {report.started_at}; status: {report.status}."
    )
    out.append("")
    if report.error:
        out.append(f"**Stopped:** {report.error}")
        out.append("")
    out.append(f"{report.total_files} file(s) loaded ({report.factor}x a normal day's own sample files, byte-identical copies — Bronze and resolution see {report.factor}x the rows; a natural-key merge still leaves Silver one day's distinct positions, not {report.factor}x them), {report.total_lines} line(s), across {len(report.sources)} source(s).")
    out.append("")
    out.append("| Source | Files | Lines | Run id |")
    out.append("|---|---|---|---|")
    for s in report.sources:
        out.append(f"| `{s.id}` | {s.files_loaded} | {s.lines_loaded} | {s.run_id or '-'} |")
    out.append("")
    out.append("## End-to-end, after the last file")
    out.append("")
    if report.seconds_end_to_end is not None:
        out.append(f"**{report.seconds_end_to_end:.1f} s** of a {VOLUME_BUDGET_SECONDS} s (20-minute) budget — {'within budget' if report.within_budget else 'OVER BUDGET'}.")
    else:
        out.append("Not measured; the run did not reach publish.")
    out.append("")
    out.append(f"Last file loaded: {report.last_file_loaded_at or 'n/a'}. Gold publish: {report.publish_result or 'not reached'}.")
    if report.gold_rows is not None:
        out.append(f"Gold rows published for the day: {report.gold_rows}, watermark written at {report.watermark_published_at}.")
    out.append("")
    out.append("## Phases")
    out.append("")
    out.append("| Phase | Seconds | |")
    out.append("|---|---|---|")
    for p in report.phases:
        out.append(f"| {p.name} | {p.seconds:.1f} | {p.detail} |")
    out.append("")
    return "\n".join(out)


def write_report(report: VolumeReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "volume.md"
    data = out / "volume.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
