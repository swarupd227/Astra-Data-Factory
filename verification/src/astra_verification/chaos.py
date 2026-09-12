"""Chaos scenarios: late, malformed, duplicate and truncated files injected, proving recoverability (S4.3.2, ADR 0038).

Four faults a custodian's delivery can arrive with, each already named in
the domain pack's rejection taxonomy or already governed by a dq_rule:

  late        a file that completes the business date's expected set after
              the custodian's cutoff (ADR 0007, ADR 0024)
  malformed   a line no record type of the spec matches — RECORD_TYPE_UNKNOWN
  duplicate   two detail records in the same file carrying the same merge
              key — MERGE_DUPLICATE_KEY
  truncated   the trailer's own record count no longer matches the file's
              detail records — the source's own control_total dq_rule

late is a timing fault: the file's content is fine, only its arrival is
late, so there is nothing to fix and nothing to retry — its scenario ends
once the custodian_late_arrival alert CUSTODIAN_GATE would raise (ADR
0024 point 4) is confirmed. The other three are content faults: each is
injected into a copy of a real sample file, run through the pipeline,
confirmed to reach its documented state (the rejection or the dq gap) and
to raise an alert, then *retried* — the same untouched, correct sample
redelivered under a new file name, nothing deleted or edited anywhere —
and confirmed to leave no trace of the fault. That is what "no manual
data surgery" means: recovery is a resend, not an operator query.

Malformed and duplicate do not yet have a production alert of their own
(only late, task failures, staleness and a DQ breach do, ADR 0007 and
S4.2.3); this drill raises one of its own, `chaos_scenario`, at the
taxonomy's or the dq_rule's own severity, into the sandbox's own copy of
CONTROL.ALERTS — evidence that the drill ran and what it found, not a
claim that production already alerts on every such exception in real
time. Wiring a standing alert for every critical exception is a
separate, larger decision for a later story.

Like a dry run and a volume test, the sandbox skips the landing pipe, the
Tasks DAG and the DMF bindings; there is still no live schedule to wait
on safely in a throwaway sandbox.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from astra_data.bundle import Executor, deploy, load_bundle, render
from astra_data.compiler import CompiledConfig
from astra_data.dq import CompiledDqRule
from astra_data.render.dq import gap_sql
from astra_data.render.names import exceptions_table, file_metadata_table, parse_problems_table

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

SCENARIOS = ("late", "malformed", "duplicate", "truncated")
CONTENT_SCENARIOS = ("malformed", "duplicate", "truncated")  # scenarios a retry applies to


class ChaosError(RuntimeError):
    pass


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# -- injecting the fault, into a copy of a real sample -------------------------


def _detail_record(compiled: CompiledConfig):
    details = [r for r in compiled.spec.records if r.type == "detail"]
    if len(details) != 1:
        raise ChaosError(f"chaos injection needs exactly one detail record type; {compiled.spec.label} has {len(details)}")
    record = details[0]
    if record.match is None or record.match.kind != "position":
        raise ChaosError(f"chaos injection needs a position-matched detail record; {record.label} is matched by {'nothing' if record.match is None else record.match.kind}")
    return record


def _lines(sample: Sample) -> list[str]:
    return sample.path.read_text(encoding="ascii").splitlines()


def _write(out_dir: Path, sample: Sample, tag: str, lines: list[str]) -> Sample:
    out_dir.mkdir(parents=True, exist_ok=True)
    folder, name = sample.file_name.rsplit("/", 1)
    stem, suffix = Path(name).stem, Path(name).suffix
    new_name = f"{stem}-{tag}{suffix}"
    path = out_dir / new_name
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return Sample(path, f"{folder}/{new_name}", len(lines))


def _detail_line_index(compiled: CompiledConfig, lines: list[str], *, from_end: bool = False) -> int:
    record = _detail_record(compiled)
    start, length, value = record.match.start - 1, record.match.length, record.match.value
    order = reversed(list(enumerate(lines))) if from_end else list(enumerate(lines))
    for i, line in order:
        if line[start : start + length] == value:
            return i
    raise ChaosError(f"no {record.label} line found in the sample to inject the scenario into")


def inject_malformed(compiled: CompiledConfig, sample: Sample, out_dir: Path) -> Sample:
    """Corrupt one detail line's own record-type marker so no record type of the spec matches it any more."""
    record = _detail_record(compiled)
    start, length = record.match.start - 1, record.match.length
    lines = _lines(sample)
    i = _detail_line_index(compiled, lines)
    lines[i] = lines[i][:start] + ("?" * length) + lines[i][start + length :]
    return _write(out_dir, sample, "malformed", lines)


def inject_duplicate(compiled: CompiledConfig, sample: Sample, out_dir: Path) -> Sample:
    """Repeat one detail line immediately after itself — the same merge key twice in one file."""
    lines = _lines(sample)
    i = _detail_line_index(compiled, lines)
    lines.insert(i + 1, lines[i])
    return _write(out_dir, sample, "duplicate", lines)


def inject_truncated(compiled: CompiledConfig, sample: Sample, out_dir: Path) -> Sample:
    """Drop the last detail line but leave the trailer's own count exactly as delivered, so it no longer adds up."""
    lines = _lines(sample)
    i = _detail_line_index(compiled, lines, from_end=True)
    del lines[i]
    return _write(out_dir, sample, "truncated", lines)


def inject_late(sample: Sample, out_dir: Path) -> Sample:
    """Content untouched: lateness is about when the file arrived, not what it says."""
    return _write(out_dir, sample, "late", _lines(sample))


def _retry_sample(base: Sample, scenario: str) -> Sample:
    """The same, correct file, redelivered under a new name — a resend, not a repair of anything already loaded."""
    folder, name = base.file_name.rsplit("/", 1)
    stem, suffix = Path(name).stem, Path(name).suffix
    return Sample(base.path, f"{folder}/{stem}-{scenario}-retry{suffix}", base.lines)


# -- reading the state a scenario reaches --------------------------------------


def parse_problem_count_query(spec: SandboxSpec, compiled: CompiledConfig, code: str, file_name: str) -> str:
    db = f'"{spec.database}"'
    return f'SELECT COUNT(*) FROM {db}."BRONZE"."{parse_problems_table(compiled)}" WHERE "CODE" = {_lit(code)} AND "FILE_NAME" = {_lit(file_name)}'


def exception_count_query(spec: SandboxSpec, compiled: CompiledConfig, run_id: str, code: str) -> str:
    db = f'"{spec.database}"'
    return f'SELECT COUNT(*) FROM {db}."EXCEPTIONS"."{exceptions_table(compiled)}" WHERE "RUN_ID" = {_lit(run_id)} AND "REJECTION_CODE" = {_lit(code)}'


def control_total_gap_query(spec: SandboxSpec, compiled: CompiledConfig, rule: CompiledDqRule, file_name: str) -> str:
    db = f'"{spec.database}"'
    return f'SELECT ABS({gap_sql(rule)}) FROM {db}."BRONZE"."{file_metadata_table(compiled)}" WHERE "FILE_NAME" = {_lit(file_name)}'


# -- the alerts this drill raises -----------------------------------------------


def chaos_alert_statement(spec: SandboxSpec, custodian: str, business_date: date, scenario: str, title: str, body: str, severity: str) -> str:
    """One alert per scenario into the sandbox's own copy of CONTROL.ALERTS — evidence the drill ran and what it found, not a standing production detector."""
    alerts = f'"{spec.database}"."CONTROL"."ALERTS"'
    key = f"chaos:{custodian}:{scenario}:{business_date.isoformat()}"
    return (
        f"INSERT INTO {alerts} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE) "
        f"SELECT UUID_STRING(), SYSDATE(), 'chaos_scenario', {_lit(severity)}, {_lit(custodian)}, {_lit(title)}, {_lit(body)}, {_lit(key)}, {_lit(business_date.isoformat())}::DATE"
    )


def late_arrival_statement(spec: SandboxSpec, custodian: str, business_date: date, run_id: str, late_file: str) -> str:
    """A CONTROL.CUSTODIAN_RUNS row exactly as CUSTODIAN_GATE writes one for a business date completed by a file after the cutoff (ADR 0024)."""
    db = f'"{spec.database}"'
    return (
        f'INSERT INTO {db}."CONTROL"."CUSTODIAN_RUNS" ("RUN_ID", "CUSTODIAN_ID", "BUSINESS_DATE", "STARTED_AT", "REASON", "FILES", "LATEST_ARRIVAL_AT", "AFTER_CUTOFF", "LATE_FILES")\n'
        f"SELECT {_lit(run_id)}, {_lit(custodian)}, {_lit(business_date.isoformat())}::DATE, SYSDATE(), 'late_arrival', 1, SYSDATE(), TRUE, {_lit(late_file)}"
    )


def late_arrival_alert_statement(spec: SandboxSpec, custodian: str, business_date: date, run_id: str, late_file: str) -> str:
    """The custodian_late_arrival alert CUSTODIAN_GATE raises once, at severity info, naming the late file (ADR 0024 point 4)."""
    alerts = f'"{spec.database}"."CONTROL"."ALERTS"'
    key = f"late_arrival:{custodian}:{business_date.isoformat()}:{run_id}"
    title = f"{custodian}: completed by a late file for {business_date.isoformat()}"
    body = f"{custodian}'s expected file set for {business_date.isoformat()} was completed by a file that arrived after the cutoff: {late_file}."
    return (
        f"INSERT INTO {alerts} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE) "
        f"SELECT UUID_STRING(), SYSDATE(), 'custodian_late_arrival', 'info', {_lit(custodian)}, {_lit(title)}, {_lit(body)}, {_lit(key)}, {_lit(business_date.isoformat())}::DATE"
    )


# -- the report --------------------------------------------------------------


@dataclass
class ScenarioResult:
    scenario: str
    detail: str
    code: str | None
    severity: str | None
    fault_count: int
    fault_detected: bool
    alert_raised: bool
    alert_kind: str | None
    retry_applicable: bool
    retried: bool = False
    recovered: bool | None = None  # None when retry_applicable is False

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "detail": self.detail,
            "code": self.code,
            "severity": self.severity,
            "fault_count": self.fault_count,
            "fault_detected": self.fault_detected,
            "alert_raised": self.alert_raised,
            "alert_kind": self.alert_kind,
            "retry_applicable": self.retry_applicable,
            "retried": self.retried,
            "recovered": self.recovered,
        }


@dataclass
class ChaosReport:
    custodian: str
    sandbox: str
    task_id: str
    started_at: str
    business_date: str
    scenarios: list[ScenarioResult] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    destroyed: bool = False
    seconds: float = 0.0
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        return "ran" if self.scenarios else "incomplete"

    @property
    def proven(self) -> bool:
        """Every scenario reached its documented state, raised its alert, and — where a retry applies — recovered with nothing deleted or edited."""
        if self.error or not self.scenarios:
            return False
        return all(s.fault_detected and s.alert_raised and s.recovered is not False for s in self.scenarios)

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "sandbox": self.sandbox,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "business_date": self.business_date,
            "status": self.status,
            "proven": self.proven,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "phases": [{"phase": p.name, "seconds": round(p.seconds, 1), "detail": p.detail} for p in self.phases],
            "seconds": round(self.seconds, 1),
            "destroyed": self.destroyed,
            "error": self.error,
        }


# -- the run -----------------------------------------------------------------


def _load_and_process(spec: SandboxSpec, executor: Executor, compiled: CompiledConfig, sample: Sample) -> str:
    executor.execute_script(put_statement(spec, sample))
    for statement in load_statements(spec, compiled, [sample]):
        executor.execute_script(statement)
    for statement in refresh_statements(spec, compiled):
        executor.execute_script(statement)
    rows = executor.query(process_statement(spec, compiled))
    return str(rows[0][0]) if rows and rows[0] else ""


def _count(executor: Executor, sql: str) -> int:
    rows = executor.query(sql)
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0


def _run_scenario(
    scenario: str,
    compiled: CompiledConfig,
    base: Sample,
    spec: SandboxSpec,
    executor: Executor,
    control_total_rule: CompiledDqRule | None,
    custodian: str,
    business_date: date,
    out: Path,
) -> ScenarioResult:
    out_dir = Path(out) / "chaos" / scenario

    if scenario == "malformed":
        fault_sample = inject_malformed(compiled, base, out_dir)
        rc = compiled.pack.rejections.code("RECORD_TYPE_UNKNOWN")
        code, severity = rc.code, rc.severity
        title, body = f"malformed file: {rc.name} ({code}) for {custodian}", f"{fault_sample.file_name}: {rc.description} Resolution: {rc.resolution}"
    elif scenario == "duplicate":
        fault_sample = inject_duplicate(compiled, base, out_dir)
        rc = compiled.pack.rejections.code("MERGE_DUPLICATE_KEY")
        code, severity = rc.code, rc.severity
        title, body = f"duplicate file: {rc.name} ({code}) for {custodian}", f"{fault_sample.file_name}: {rc.description} Resolution: {rc.resolution}"
    elif scenario == "truncated":
        if control_total_rule is None:
            raise ChaosError(f"{compiled.id} has no control_total dq_rule; the truncated scenario needs one to detect the gap")
        fault_sample = inject_truncated(compiled, base, out_dir)
        code, severity = control_total_rule.id, control_total_rule.severity
        title, body = f"truncated file: control total gap ({code}) for {custodian}", f"{fault_sample.file_name}: {control_total_rule.check}."
    elif scenario == "late":
        fault_sample = inject_late(base, out_dir)
        code, severity = None, "info"
        title = body = ""  # the late_arrival alert has its own wording
    else:
        raise ChaosError(f"unknown scenario {scenario!r}; must be one of {', '.join(SCENARIOS)}")

    run_id = _load_and_process(spec, executor, compiled, fault_sample)

    if scenario == "malformed":
        fault_count = _count(executor, parse_problem_count_query(spec, compiled, code, fault_sample.file_name))
    elif scenario == "duplicate":
        fault_count = _count(executor, exception_count_query(spec, compiled, run_id, code))
    elif scenario == "truncated":
        fault_count = _count(executor, control_total_gap_query(spec, compiled, control_total_rule, fault_sample.file_name))
    else:  # late
        fault_count = 1
    fault_detected = fault_count > 0

    alert_raised = False
    alert_kind = None
    if scenario == "late":
        chaos_run_id = str(uuid.uuid4())
        executor.execute_script(late_arrival_statement(spec, custodian, business_date, chaos_run_id, fault_sample.file_name))
        executor.execute_script(late_arrival_alert_statement(spec, custodian, business_date, chaos_run_id, fault_sample.file_name))
        alert_raised, alert_kind = True, "custodian_late_arrival"
    elif fault_detected:
        executor.execute_script(chaos_alert_statement(spec, custodian, business_date, scenario, title, body, severity))
        alert_raised, alert_kind = True, "chaos_scenario"

    retry_applicable = scenario in CONTENT_SCENARIOS
    retried = False
    recovered = None
    if retry_applicable:
        retry_sample = _retry_sample(base, scenario)
        retry_run_id = _load_and_process(spec, executor, compiled, retry_sample)
        retried = True
        if scenario == "malformed":
            recovered_count = _count(executor, parse_problem_count_query(spec, compiled, code, retry_sample.file_name))
        elif scenario == "duplicate":
            recovered_count = _count(executor, exception_count_query(spec, compiled, retry_run_id, code))
        else:  # truncated
            recovered_count = _count(executor, control_total_gap_query(spec, compiled, control_total_rule, retry_sample.file_name))
        recovered = recovered_count == 0

    return ScenarioResult(scenario, fault_sample.file_name, code, severity, fault_count, fault_detected, alert_raised, alert_kind, retry_applicable, retried, recovered)


def run(
    config: Path,
    sample: Path,
    business_date: date,
    spec: SandboxSpec,
    executor: Executor,
    *,
    repo: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    cdm_ddl: Path | None = None,
    scenarios: tuple[str, ...] = SCENARIOS,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> ChaosReport:
    """Every scenario in `scenarios`, injected from one real sample, in one sandbox destroyed at the end.

    Raises whatever `compile_and_render` raises (astra_verification.dryrun.DryRunError, a
    compile problem) or ChaosError (a scenario's own precondition is not met) before any
    sandbox is created; everything after that is recorded in the report, not raised.
    """
    started = monotonic()
    started_at = clock()
    work = Path(out) / "bundle"
    compiled, bundle = compile_and_render(Path(config), repo, work)
    custodian = compiled.source["custodian"]
    control_total_rule = next((r for r in compiled.dq_rules if r.kind == "control_total"), None)
    if "truncated" in scenarios and control_total_rule is None:
        raise ChaosError(f"{compiled.id} has no control_total dq_rule; the truncated scenario needs one to detect the gap")
    base = samples_for(compiled, [Path(sample)])[0]

    report = ChaosReport(custodian, spec.database, spec.task_id, started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"), business_date.isoformat())
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

        for statement in stage_statements(spec):
            executor.execute_script(statement)

        for scenario in scenarios:
            t = monotonic()
            result = _run_scenario(scenario, compiled, base, spec, executor, control_total_rule, custodian, business_date, out)
            report.scenarios.append(result)
            phase(f"scenario: {scenario}", t, "recovered" if result.recovered else ("detected" if result.fault_detected else "not detected"))
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:500] if str(exc) else ''}"
    finally:
        t = monotonic()
        destroy(executor, spec, reason="task_failed" if report.error else "task_done", detail=f"chaos test {custodian}"[:1000])
        report.destroyed = True
        phase("sandbox destroyed", t)
    report.seconds = monotonic() - started
    write_report(report, Path(out))
    return report


# -- the report --------------------------------------------------------------


def render_markdown(report: ChaosReport) -> str:
    out = [f"# Chaos scenarios: {report.custodian} {report.business_date}", ""]
    out.append(f"Sandbox `{report.sandbox}` (destroyed: {'yes' if report.destroyed else 'no'}). Started {report.started_at}; status: {report.status}. Proven: {'yes' if report.proven else 'no'}.")
    out.append("")
    if report.error:
        out.append(f"**Stopped:** {report.error}")
        out.append("")
    out.append("| Scenario | Code | Severity | Fault detected | Alert | Retried | Recovered |")
    out.append("|---|---|---|---|---|---|---|")
    for s in report.scenarios:
        recovered = "-" if s.recovered is None else ("yes" if s.recovered else "no")
        out.append(f"| {s.scenario} | `{s.code or '-'}` | {s.severity or '-'} | {'yes' if s.fault_detected else 'NO'} ({s.fault_count}) | {s.alert_kind or 'none'} | {'yes' if s.retried else 'n/a'} | {recovered} |")
    out.append("")
    out.append("## Phases")
    out.append("")
    out.append("| Phase | Seconds | |")
    out.append("|---|---|---|")
    for p in report.phases:
        out.append(f"| {p.name} | {p.seconds:.1f} | {p.detail} |")
    out.append("")
    return "\n".join(out)


def write_report(report: ChaosReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "chaos.md"
    data = out / "chaos.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
