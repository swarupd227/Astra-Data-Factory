"""DR drill: the standardized zone destroyed and restored from retained files, proving RTO and RPO (S4.3.3, ADR 0039).

"The standardized zone" is Bronze (raw lines turned into typed,
standardized records) and the Silver it derives (the canonical, merged
records) — the two schemas a source's own pipeline populates. The files
a custodian delivers, and the golden datasets captured from them, are
retained independently of Snowflake's own storage, in S3 (ADR 0003, ADR
0031); a disaster that destroys the database does not destroy what is
durably retained there. This drill proves that claim empirically instead
of asserting it: load a normal day's sample into a sandbox and record
the row counts that result (the baseline), drop the BRONZE and SILVER
schemas outright (the disaster), time how long recreating them and
replaying the same retained files back through the pipe takes (the
restore), and confirm the restored counts match the baseline exactly.

RTO and RPO are the client's own proposed targets — commercial
commitments made in a proposal, not numbers this code invents — so both
are required inputs, never defaulted. RTO is checked against the
measured wall-clock time of the restore. RPO, in this drill, is a binary
outcome rather than a manufactured time figure: because every retained
file is replayed in full, a clean restore achieves zero rows lost — RPO
met by construction — and any row count that does not come back exactly
is reported as a breach with the table and the gap named, not smoothed
into an invented number of minutes.

Like the other NFR drills, the sandbox skips the landing pipe, the Tasks
DAG and the DMF bindings; recreating a live Snowpipe subscription and a
scheduled task graph inside a throwaway sandbox is not what this drill
measures, and the files replayed come from local disk standing in for
the landing zone's own retained copies, not from a live S3 bucket.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from astra_data.bundle import Executor, deploy, load_bundle, render
from astra_data.compiler import CompiledConfig
from astra_data.render.names import record_table, silver_table

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

ZONE_SCHEMAS = ("BRONZE", "SILVER")  # the standardized zone: standardized (Bronze) and canonical (Silver)


class DrError(RuntimeError):
    pass


def drop_zone_statements(spec: SandboxSpec) -> list[str]:
    """The disaster: the standardized zone gone, nothing salvaged from it."""
    return [f'DROP SCHEMA IF EXISTS "{spec.database}"."{schema}"' for schema in ZONE_SCHEMAS]


def recreate_zone_statements(spec: SandboxSpec) -> list[str]:
    """The schemas back, empty — exactly how `sandbox.create` first made them."""
    return [f'CREATE SCHEMA "{spec.database}"."{schema}" WITH MANAGED ACCESS DATA_RETENTION_TIME_IN_DAYS = 0' for schema in ZONE_SCHEMAS]


def _detail_table(compiled: CompiledConfig) -> str:
    detail = next((r for r in compiled.spec.records if r.type == "detail"), None)
    if detail is None:
        raise DrError(f"{compiled.spec.label} has no detail record; the drill has no Bronze table to measure")
    return record_table(compiled, detail.label)


def bronze_count_query(spec: SandboxSpec, compiled: CompiledConfig) -> str:
    return f'SELECT COUNT(*) FROM "{spec.database}"."BRONZE"."{_detail_table(compiled)}"'


def silver_count_query(spec: SandboxSpec, compiled: CompiledConfig) -> str:
    return f'SELECT COUNT(*) FROM "{spec.database}"."SILVER"."{silver_table(compiled)}" WHERE "RETIRED_AT" IS NULL'


def _count(executor: Executor, sql: str) -> int:
    rows = executor.query(sql)
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0


def _load_and_process(spec: SandboxSpec, executor: Executor, compiled: CompiledConfig, samples: list[Sample]) -> str:
    for sample in samples:
        executor.execute_script(put_statement(spec, sample))
    for statement in load_statements(spec, compiled, samples):
        executor.execute_script(statement)
    for statement in refresh_statements(spec, compiled):
        executor.execute_script(statement)
    rows = executor.query(process_statement(spec, compiled))
    return str(rows[0][0]) if rows and rows[0] else ""


# -- the report --------------------------------------------------------------


@dataclass
class DrReport:
    custodian: str
    sandbox: str
    task_id: str
    started_at: str
    rto_minutes: float
    rpo_minutes: float
    baseline: dict[str, int] = field(default_factory=dict)
    restored: dict[str, int] = field(default_factory=dict)
    seconds_to_restore: float | None = None
    phases: list[Phase] = field(default_factory=list)
    destroyed: bool = False
    seconds: float = 0.0
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        return "restored" if self.restored else "incomplete"

    @property
    def rows_lost(self) -> dict[str, int]:
        """Every table whose restored count differs from the baseline, with the gap; empty means a clean restore."""
        return {k: v - self.restored.get(k, 0) for k, v in self.baseline.items() if v != self.restored.get(k, 0)}

    @property
    def within_rto(self) -> bool:
        return self.seconds_to_restore is not None and self.seconds_to_restore <= self.rto_minutes * 60

    @property
    def within_rpo(self) -> bool:
        return self.status == "restored" and not self.rows_lost

    @property
    def proven(self) -> bool:
        return not self.error and self.within_rto and self.within_rpo

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "sandbox": self.sandbox,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "rto_minutes": self.rto_minutes,
            "rpo_minutes": self.rpo_minutes,
            "baseline": self.baseline,
            "restored": self.restored,
            "rows_lost": self.rows_lost,
            "seconds_to_restore": None if self.seconds_to_restore is None else round(self.seconds_to_restore, 1),
            "within_rto": self.within_rto,
            "within_rpo": self.within_rpo,
            "status": self.status,
            "proven": self.proven,
            "phases": [{"phase": p.name, "seconds": round(p.seconds, 1), "detail": p.detail} for p in self.phases],
            "seconds": round(self.seconds, 1),
            "destroyed": self.destroyed,
            "error": self.error,
        }


# -- the drill -----------------------------------------------------------------


def run(
    config: Path,
    samples: list[Path],
    rto_minutes: float,
    rpo_minutes: float,
    spec: SandboxSpec,
    executor: Executor,
    *,
    repo: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    cdm_ddl: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> DrReport:
    """Load a baseline, destroy the standardized zone, restore it from the same retained files, and compare.

    Raises whatever `compile_and_render` raises (astra_verification.dryrun.DryRunError, a
    compile problem) before any sandbox is created; everything after that is recorded in
    the report, not raised.
    """
    started = monotonic()
    started_at = clock()
    work = Path(out) / "bundle"
    compiled, bundle = compile_and_render(Path(config), repo, work)
    custodian = compiled.source["custodian"]
    files = samples_for(compiled, [Path(s) for s in samples])

    report = DrReport(custodian, spec.database, spec.task_id, started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"), rto_minutes, rpo_minutes)
    target = spec.target()

    def phase(name: str, since: float, detail: str = "") -> None:
        report.phases.append(Phase(name, monotonic() - since, detail))

    t = monotonic()
    create(executor, spec)
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
        _load_and_process(spec, executor, compiled, files)
        phase("baseline loaded", t, f"{len(files)} file(s)")

        t = monotonic()
        report.baseline = {"bronze": _count(executor, bronze_count_query(spec, compiled)), "silver": _count(executor, silver_count_query(spec, compiled))}
        phase("baseline measured", t, ", ".join(f"{k} {v}" for k, v in report.baseline.items()))

        t = monotonic()
        for statement in drop_zone_statements(spec):
            executor.execute_script(statement)
        phase("disaster: standardized zone dropped", t, ", ".join(ZONE_SCHEMAS))

        t_restore = monotonic()
        for statement in recreate_zone_statements(spec):
            executor.execute_script(statement)
        if cdm_ddl is not None:
            executor.execute_script(render(cdm_ddl.read_text(encoding="utf-8"), target))
        deploy(sandbox_bundle(bundle), target, executor)
        for statement in stage_statements(spec):
            executor.execute_script(statement)
        _load_and_process(spec, executor, compiled, files)
        report.seconds_to_restore = monotonic() - t_restore
        phase("restored", t_restore, f"{report.seconds_to_restore:.1f} s")

        t = monotonic()
        report.restored = {"bronze": _count(executor, bronze_count_query(spec, compiled)), "silver": _count(executor, silver_count_query(spec, compiled))}
        phase("restored state measured", t, ", ".join(f"{k} {v}" for k, v in report.restored.items()))
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:500] if str(exc) else ''}"
    finally:
        t = monotonic()
        destroy(executor, spec, reason="task_failed" if report.error else "task_done", detail=f"dr drill {custodian}"[:1000])
        report.destroyed = True
        phase("sandbox destroyed", t)
    report.seconds = monotonic() - started
    write_report(report, Path(out))
    return report


# -- the report --------------------------------------------------------------


def render_markdown(report: DrReport) -> str:
    out = [f"# DR drill: {report.custodian}", ""]
    out.append(f"Sandbox `{report.sandbox}` (destroyed: {'yes' if report.destroyed else 'no'}). Started {report.started_at}; status: {report.status}. Proven: {'yes' if report.proven else 'no'}.")
    out.append("")
    if report.error:
        out.append(f"**Stopped:** {report.error}")
        out.append("")
    out.append("## RTO")
    out.append("")
    if report.seconds_to_restore is not None:
        out.append(f"**{report.seconds_to_restore:.1f} s** to restore, against a proposed RTO of {report.rto_minutes:.0f} minute(s) ({report.rto_minutes * 60:.0f} s) — {'within RTO' if report.within_rto else 'RTO BREACHED'}.")
    else:
        out.append("Not measured; the drill did not reach the restore.")
    out.append("")
    out.append("## RPO")
    out.append("")
    out.append(f"Proposed RPO: {report.rpo_minutes:.0f} minute(s). " + ("Every row present before the disaster is present after the restore — RPO met." if report.within_rpo else "RPO BREACHED — rows lost:"))
    if report.rows_lost:
        out.append("")
        out.append("| Table | Baseline | Restored | Lost |")
        out.append("|---|---|---|---|")
        for k, gap in report.rows_lost.items():
            out.append(f"| {k} | {report.baseline.get(k, 0)} | {report.restored.get(k, 0)} | {gap} |")
    out.append("")
    out.append("## Phases")
    out.append("")
    out.append("| Phase | Seconds | |")
    out.append("|---|---|---|")
    for p in report.phases:
        out.append(f"| {p.name} | {p.seconds:.1f} | {p.detail} |")
    out.append("")
    return "\n".join(out)


def write_report(report: DrReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "dr.md"
    data = out / "dr.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
