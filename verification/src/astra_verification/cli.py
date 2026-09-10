"""Command line: `astra-verify sandbox ...`, `astra-verify pii check`, `astra-verify reference status`.

Snowflake connection settings come from the environment (see
astra_data.snowflake_connection). Run as the environment's SANDBOX role.
Exit codes: 0 done, 1 a check failed, 2 usage or connection error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from astra_data.bundle import BundleError, DeployError, Target
from astra_data.snowflake_connection import ConnectionConfigError, SnowflakeExecutor, connect

from astra_verification.pii import run_checks as run_pii_checks
from astra_verification.reference import feed_statuses
from astra_verification.dryrun import BUDGET_SECONDS, DryRunError, dry_run
from astra_verification.golden import MAX_DAYS, MIN_DAYS, OdbcLegacyStore, capture_day, check as golden_check, coverage, discover as discover_captures, file_source, load_capture, object_store, record, secrets_from, subprocess_runner as golden_runner, verify as golden_verify
from astra_verification.replay import ReplayError, old_config_from_git, replay
from astra_verification.parity import MATCH_RATE_TARGET, ParityError, check as parity_check, load_parity, run as run_parity
from astra_verification.snowpark import ENGINES, SparkEngine, load_assessment, run_assessment, update_memo
from astra_verification.sandbox import (
    MAX_TTL_MINUTES,
    WAREHOUSE_SIZES,
    SandboxSpec,
    cost_credits,
    create,
    destroy,
    list_sandboxes,
    reap,
)

CREATE_BUDGET_SECONDS = 120


def _executor() -> SnowflakeExecutor:
    try:
        return SnowflakeExecutor(connect())
    except ConnectionConfigError as exc:
        raise SystemExit(f"error: {exc}") from exc


def _spec(args: argparse.Namespace, task_id: str | None = None) -> SandboxSpec:
    return SandboxSpec(
        task_id=task_id or args.task,
        environment=args.environment,
        prefix=args.prefix,
        ttl_minutes=getattr(args, "ttl_minutes", 120),
        warehouse_size=getattr(args, "warehouse_size", "XSMALL"),
    )


def _emit(payload: dict | list, text: str, as_json: bool) -> None:
    print(json.dumps(payload, indent=2) if as_json else text)


# -- commands ----------------------------------------------------------------


def cmd_create(args: argparse.Namespace) -> int:
    spec = _spec(args)
    executor = _executor()
    try:
        created = create(executor, spec, [Path(b) for b in args.bundle])
    finally:
        executor.close()
    _emit(
        created.to_dict(),
        f"created {created.database} with warehouse {created.warehouse} in {created.seconds:.1f}s; expires {created.to_dict()['expires_at']} UTC"
        + (f"; deployed {', '.join(created.deployed)}" if created.deployed else ""),
        args.json,
    )
    return 0


def cmd_destroy(args: argparse.Namespace) -> int:
    spec = _spec(args)
    executor = _executor()
    try:
        destroy(executor, spec, reason=args.reason, detail=args.detail)
    finally:
        executor.close()
    _emit({"database": spec.database, "warehouse": spec.warehouse, "reason": args.reason}, f"destroyed {spec.database} and {spec.warehouse} ({args.reason})", args.json)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    executor = _executor()
    try:
        records = list_sandboxes(executor, args.environment, args.prefix)
    finally:
        executor.close()
    rows = [
        {
            "database": r.database,
            "task_id": r.task_id,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
            "expires_at": r.expires_at.strftime("%Y-%m-%d %H:%M:%S") if r.expires_at else None,
        }
        for r in records
    ]
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no sandboxes")
    else:
        width = max(len(r["database"]) for r in rows)
        for r in rows:
            print(f"{r['database'].ljust(width)}  task={r['task_id'] or '?'}  created={r['created_at'] or '?'}  expires={r['expires_at'] or '?'}")
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    executor = _executor()
    try:
        dropped = reap(executor, args.environment, args.prefix)
    finally:
        executor.close()
    _emit({"dropped": dropped}, f"reaper dropped {dropped} sandbox{'es' if dropped != 1 else ''}", args.json)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    spec = _spec(args)
    executor = _executor()
    try:
        credits = cost_credits(executor, spec, days=args.days)
    finally:
        executor.close()
    _emit(
        {"task_id": spec.task_id, "warehouse": spec.warehouse, "credits": credits, "days": args.days},
        f"{spec.warehouse}: {credits:.4f} credits in the last {args.days} days (metering history lags by minutes to hours)",
        args.json,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Live acceptance for S1.2.3: create with a bundle under two minutes, tagged, then gone."""
    task_id = f"check-{uuid.uuid4().hex[:8]}"
    spec = _spec(args, task_id)
    results: list[tuple[str, bool, str]] = []
    executor = _executor()
    try:
        created = create(executor, spec, [Path(b) for b in args.bundle])
        results.append(("sandbox created with the requested bundles in under two minutes", created.seconds <= CREATE_BUDGET_SECONDS, f"{created.seconds:.1f}s, deployed {', '.join(created.deployed) or 'nothing'}"))

        tag = executor.query(f"SELECT SYSTEM$GET_TAG({_lit(spec.tag('TASK_ID'))}, {_lit(spec.warehouse)}, 'warehouse')")
        value = tag[0][0] if tag and tag[0] else None
        results.append(("sandbox warehouse is tagged with the task id", value == task_id, f"TASK_ID={value!r}"))

        present = [r.database for r in list_sandboxes(executor, args.environment, args.prefix)]
        results.append(("sandbox is listed with its expiry", spec.database in present, f"{len(present)} sandbox(es) listed"))
    finally:
        try:
            destroy(executor, spec, reason="task_done", detail="acceptance check")
            remaining = [r.database for r in list_sandboxes(executor, args.environment, args.prefix)]
            results.append(("sandbox destroyed after the task", spec.database not in remaining, "not listed after destroy"))
        finally:
            executor.close()

    width = max(len(name) for name, _, _ in results)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name.ljust(width)}  {detail}")
    print("note: expiry after the time limit is enforced by CONTROL.REAP_SANDBOXES; run `astra-verify sandbox reap` to exercise it now.")
    return 0 if all(passed for _, passed, _ in results) else 1


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cmd_pii_check(args: argparse.Namespace) -> int:
    """Live acceptance for S1.2.5."""
    target = Target(args.environment, args.prefix)
    privileged = args.privileged_role or f"{target.environment_database}_ENGINEER"
    restricted = args.restricted_role or f"{target.environment_database}_AUDITOR"
    executor = _executor()
    try:
        results = run_pii_checks(executor, target, privileged, restricted)
    finally:
        executor.close()
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        width = max(len(r.name) for r in results)
        for r in results:
            print(f"{'PASS' if r.passed else 'FAIL'}  {r.name.ljust(width)}  {r.detail}")
    return 0 if all(r.passed for r in results) else 1


def cmd_reference_status(args: argparse.Namespace) -> int:
    """Per feed: the last replication run, its row counts, the delta it made, and whether the replica is fresh."""
    target = Target(args.environment, args.prefix)
    executor = _executor()
    try:
        statuses = feed_statuses(executor, target)
    finally:
        executor.close()
    if args.json:
        print(json.dumps([{**s.__dict__, "healthy": s.healthy, "stale": s.stale, "delta": s.delta} for s in statuses], indent=2))
        return 0 if statuses and all(s.healthy for s in statuses) else 1
    if not statuses:
        print(f"no enabled reference feeds in {target.environment_database}.CONTROL.REFERENCE_FEEDS; run astra-data reference sync")
        return 1
    width = max(len(s.feed_id) for s in statuses)
    for s in statuses:
        last = f"last run {s.last_started_at} {s.last_status}" if s.last_run_id else "no run yet"
        since = f"{s.hours_since_success:.1f}h ago" if s.hours_since_success is not None else "never"
        line = f"{'OK  ' if s.healthy else 'FAIL'}  {s.feed_id.ljust(width)}  {last}  delta {s.delta}  replica {s.rows_total if s.rows_total is not None else '-'}  last success {since} (expected within {s.expected_every_hours}h)"
        if s.last_error:
            line += f"  error: {s.last_error}"
        print(line)
        if s.last_run_id:
            print(f"      changes of run {s.last_run_id} are in REFERENCE.{s.table}_CHANGES")
    return 0 if all(s.healthy for s in statuses) else 1


# -- parser ------------------------------------------------------------------


def _capture_dir(args: argparse.Namespace):
    path = Path(args.capture)
    capture_file = path if path.is_file() else path / "capture.yaml"
    capture, problems = load_capture(capture_file, Path(args.root))
    if capture is None:
        for p in problems:
            print(p.format(), file=sys.stderr)
        return None, capture_file.parent.parent
    return capture, capture_file.parent.parent


def cmd_golden_check(args: argparse.Namespace) -> int:
    captures, problems = golden_check(Path(args.golden), Path(args.root))
    if problems:
        for p in problems:
            print(p.format(), file=sys.stderr)
        print(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in golden captures and indexes", file=sys.stderr)
        return 1
    for c in captures:
        cov = coverage(Path(args.golden), c.custodian)
        print(f"{c.custodian}: {len(c.outputs)} outputs ({', '.join(c.outputs)}), files {c.files_location}/{c.files_pattern}; {cov.versions} version(s) over {cov.days} business day(s) indexed")
    print(f"checked {len(captures)} capture file{'s' if len(captures) != 1 else ''}: no problems" if captures else f"no capture files under {args.golden}")
    return 0


def cmd_golden_status(args: argparse.Namespace) -> int:
    rows = [coverage(Path(args.golden), path.parent.name) for path in discover_captures(Path(args.golden))]
    if args.json:
        print(json.dumps([{"custodian": c.custodian, "days": c.days, "versions": c.versions, "first": c.first, "last": c.last, "within_range": c.within_range} for c in rows], indent=2))
        return 0 if all(c.within_range for c in rows) else 1
    for c in rows:
        print(f"{c.custodian}: {c.verdict}" + (f" ({c.first} to {c.last}, {c.versions} version(s))" if c.days else ""))
    if not rows:
        print(f"no capture files under {args.golden}")
    return 0 if all(c.within_range for c in rows) else 1


def cmd_golden_capture(args: argparse.Namespace) -> int:
    from datetime import date

    capture, golden_dir = _capture_dir(args)
    if capture is None:
        return 2
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = capture.business_days_between(start, end)
    if not days:
        print(f"error: no business day of {capture.custodian} between {start} and {end}", file=sys.stderr)
        return 2
    try:
        connection = secrets_from(os.environ, capture)
    except EnvironmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store = object_store(args.store)
    files = file_source(args.files or capture.files_location)
    legacy = OdbcLegacyStore(connection) if args.legacy is None else args.legacy
    results = []
    try:
        for day in days:
            result = capture_day(capture, day, files=files, store=store, legacy=legacy, runner=golden_runner, environ=dict(os.environ), work_dir=Path(args.work))
            results.append(result)
            line = f"{day.isoformat()}  {result.status:14}"
            if result.status in ("captured", "unchanged"):
                line += f" v{result.version} {result.hash[:12]}  {len(result.sources)} file(s), rows " + ", ".join(f"{k} {v}" for k, v in result.rows.items())
            elif result.detail:
                line += f" {result.detail}"
            print(line)
    finally:
        close = getattr(legacy, "close", None)
        if close:
            close()
    added = record(golden_dir, results)
    captured = sum(1 for r in results if r.status == "captured")
    failed = [r for r in results if r.status == "replay_failed"]
    cov = coverage(golden_dir, capture.custodian)
    print(f"{capture.custodian}: {captured} new version(s) captured, {sum(1 for r in results if r.status == 'unchanged')} unchanged, {sum(1 for r in results if r.status == 'no_files')} without files, {len(failed)} replay failure(s); index golden/{capture.custodian}/datasets.json {'updated' if added else 'unchanged'}; {cov.verdict}")
    return 1 if failed else 0


def cmd_golden_verify(args: argparse.Namespace) -> int:
    capture, golden_dir = _capture_dir(args)
    if capture is None:
        return 2
    problems = golden_verify(golden_dir, capture.custodian, object_store(args.store))
    cov = coverage(golden_dir, capture.custodian)
    if problems:
        for p in problems:
            print(p.format(), file=sys.stderr)
        print(f"{capture.custodian}: {len(problems)} of {cov.versions} indexed version(s) do not verify against {args.store}", file=sys.stderr)
        return 1
    print(f"{capture.custodian}: {cov.versions} version(s) over {cov.days} business day(s) verify against {args.store}: every file hashes as its manifest says")
    return 0


def cmd_dryrun(args: argparse.Namespace) -> int:
    """Sample files through a drafted config in a sandbox; the report says what the pipeline made of them; the sandbox is gone."""
    from datetime import datetime, timezone

    task_id = args.task or f"dryrun-{Path(args.config).stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    repo = Path(args.repo)

    def under_repo(path: str) -> Path:
        return Path(path) if Path(path).is_absolute() else repo / path

    bundles = [under_repo(b) for b in (args.bundles or ["releases/custodial-reference-data"])]
    cdm_ddl = under_repo(args.cdm_ddl) if args.cdm_ddl else None
    spec = SandboxSpec(task_id=task_id, environment=args.environment, prefix=args.prefix, ttl_minutes=args.ttl_minutes, warehouse_size=args.warehouse_size)
    out = Path(args.out) / task_id
    executor = _executor()
    try:
        report = dry_run(
            Path(args.config),
            [Path(s) for s in args.samples],
            spec,
            executor,
            repo=repo,
            out=out,
            extra_bundles=bundles,
            cdm_ddl=cdm_ddl,
        )
    except DryRunError as exc:
        for p in exc.problems:
            print(p.format(), file=sys.stderr)
        print(f"error: the dry run did not start; {len(exc.problems)} problem{'s' if len(exc.problems) != 1 else ''} in the inputs", file=sys.stderr)
        return 2
    finally:
        executor.close()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        parsed = ", ".join(f"{label} {rows}" for label, rows in report.parsed.items()) or "nothing"
        rejected = ", ".join(f"{e['code']} {e['rows']}" for e in report.exceptions if e["level"] == "record") or "none"
        failed_tests = [t["test"] for t in report.tests if not t["passed"]]
        gaps = [c for c in report.control_totals if c["gap"] not in (None, "0", "0.0")]
        print(f"dry run of {report.source} in {report.sandbox}: {report.status}, {report.seconds:.1f} s of {BUDGET_SECONDS} s{'' if report.within_budget else ' (over budget)'}, sandbox destroyed: {'yes' if report.destroyed else 'no'}")
        print(f"  parsed: {parsed}")
        print(f"  rejected rows by code: {rejected}")
        print(f"  control-total gaps: {len(gaps)} file(s) with a gap" if report.control_totals else "  control-total gaps: no control-total rule")
        print(f"  DQ: {len(report.tests) - len(failed_tests)} of {len(report.tests)} tests passed" + (f"; failing: {', '.join(failed_tests)}" if failed_tests else ""))
        if report.error:
            print(f"  stopped: {report.error}")
        print(f"  report: {out / 'report.md'}")
    return 1 if report.error or not report.within_budget else 0


def cmd_snowpark_assess(args: argparse.Namespace) -> int:
    """Run the assessment's transformers on an engine, write the result file and rewrite the memo's evidence."""
    try:
        assessment = load_assessment(Path(args.assessment))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        run = run_assessment(assessment, SparkEngine(args.engine), transformers=args.transformers or None)
    except ImportError as exc:
        print(f"error: engine {args.engine} needs a package that is not installed: {exc}. Install astra-verification[spark] for local, [snowpark-connect] for Snowpark Connect.", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    update_memo(assessment)
    if args.json:
        print(json.dumps(run.to_dict(), indent=2))
    else:
        print(f"{args.engine}: session started in {run.session_seconds:g} s")
        for r in run.results:
            parity = "" if r.parity is None else ("parity with expected" if r.parity else f"{r.mismatches} mismatch{'es' if r.mismatches != 1 else ''} against expected")
            print(f"  {r.transformer:22} {r.status:9} {r.rows:4} rows  {r.seconds:7.3f} s  {parity}" + (f"  {r.error}" if r.error else ""))
        failed = [r for r in run.results if r.status != "succeeded"]
        parity_failed = [r for r in run.results if r.parity is False]
        print(f"run {run.run_id} {run.status}; results/{args.engine}-{run.run_id}.json written and the memo's evidence rewritten" + (f"; {len(parity_failed)} transformer(s) without parity" if parity_failed else "") + (f"; {len(failed)} failed" if failed else ""))
    return 0 if run.status == "succeeded" and not any(r.parity is False for r in run.results) else 1


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a config change against N days of already-captured history; the difference report says whether SME review is needed."""
    from datetime import datetime, timezone

    if bool(args.old) == bool(args.old_ref):
        print("error: give exactly one of --old (a file) or --old-ref (a git ref)", file=sys.stderr)
        return 2

    repo = Path(args.repo)

    def under_repo(path: str) -> Path:
        return Path(path) if Path(path).is_absolute() else repo / path

    new_config = Path(args.new)
    task_id = args.task or f"replay-{new_config.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    work = under_repo(args.work)
    out = Path(args.out) / task_id
    try:
        old_config = Path(args.old) if args.old else old_config_from_git(new_config, args.old_ref, repo, work)
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    bundles = [under_repo(b) for b in (args.bundles or ["releases/custodial-reference-data"])]
    cdm_ddl = under_repo(args.cdm_ddl) if args.cdm_ddl else None
    executor = _executor()
    try:
        report = replay(
            old_config,
            new_config,
            args.custodian,
            args.environment,
            executor,
            repo=repo,
            golden_dir=under_repo(args.golden),
            days=args.days,
            prefix=args.prefix,
            work_dir=work,
            out=out,
            extra_bundles=bundles,
            cdm_ddl=cdm_ddl,
            task_id=task_id,
        )
    except (DryRunError, ReplayError) as exc:
        problems = getattr(exc, "problems", None)
        if problems:
            for p in problems:
                print(p.format(), file=sys.stderr)
            print(f"error: the replay did not start; {len(problems)} problem{'s' if len(problems) != 1 else ''} in the inputs", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        executor.close()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"replay of {report.custodian}: {len(report.business_days)} business day(s) ({report.business_days[0]} to {report.business_days[-1]}), {len(report.sample_files)} file(s), {report.seconds:.1f} s")
        print(f"  config: {len(report.config_diff.fields)} field/rule/dq/resolution difference(s)" + (f", {len(report.config_diff.other)} other" if report.config_diff.other else ""))
        if report.both_ran:
            print(f"  data: {report.rows_compared} row(s) compared, {report.rows_added} added, {report.rows_removed} removed, {report.rows_changed} changed")
            print(f"  exceptions: {len(report.exception_delta)} code(s) changed; tests: {len(report.test_delta)} changed")
        else:
            print(f"  old run: {report.old_run.status}" + (f" - {report.old_run.error}" if report.old_run.error else ""))
            print(f"  new run: {report.new_run.status}" + (f" - {report.new_run.error}" if report.new_run.error else ""))
        print(f"  {'eligible for promotion without SME review' if report.auto_promotable else 'SME review needed'}; report: {out / 'replay.md'}")
    return 0 if report.auto_promotable else 1


def cmd_parity_check(args: argparse.Namespace) -> int:
    mappings, problems = parity_check(Path(args.golden), Path(args.root))
    if problems:
        for p in problems:
            print(p.format(), file=sys.stderr)
        print(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in parity mappings", file=sys.stderr)
        return 1
    for m in mappings:
        print(f"{m.custodian}: {m.legacy_output} vs {m.schema}.{m.table}; {len(m.keys)} key(s), {len(m.fields)} field(s)")
    print(f"checked {len(mappings)} parity mapping{'s' if len(mappings) != 1 else ''}: no problems" if mappings else f"no parity mappings under {args.golden}")
    return 0


def cmd_parity_run(args: argparse.Namespace) -> int:
    """Compare a golden dataset's legacy output with the lakehouse for one business date; the report says the match rate."""
    from datetime import date as date_cls

    mapping, problems = load_parity(Path(args.config), Path(args.root))
    if mapping is None:
        for p in problems:
            print(p.format(), file=sys.stderr)
        print(f"error: {len(problems)} problem{'s' if len(problems) != 1 else ''} in the parity mapping", file=sys.stderr)
        return 2
    business_date = date_cls.fromisoformat(args.business_date)
    target = Target(args.environment, args.prefix)
    executor = _executor()
    try:
        result = run_parity(mapping, Path(args.golden), object_store(args.store), executor, target.database, business_date, legacy_version=args.legacy_version, out=Path(args.out) / f"{mapping.custodian}-{args.business_date}")
    except ParityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        executor.close()
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"parity of {result.custodian} {result.business_date}: {result.legacy_output} vs {result.table}")
        print(f"  {result.legacy_rows} legacy row(s), {result.lakehouse_rows} lakehouse row(s), {result.matched} matched")
        print(f"  match rate {result.match_rate:.4%} (target {MATCH_RATE_TARGET:.1%}): {'meets target' if result.meets_target else 'below target'}")
        print(f"  missing {len(result.missing)}, extra {len(result.extra)}, value mismatch {len(result.mismatches)} row(s)" + (f"; by field: {', '.join(f'{k} {v}' for k, v in sorted(result.field_summary.items(), key=lambda kv: -kv[1]))}" if result.field_summary else ""))
        print(f"  report: {Path(args.out) / f'{mapping.custodian}-{args.business_date}' / 'parity.md'}")
    return 0 if result.meets_target else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-verify", description="Astra Data Factory verification plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    sandbox_parser = sub.add_parser("sandbox", help="ephemeral sandboxes")
    ssub = sandbox_parser.add_subparsers(dest="sandbox_command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--environment", required=True, help="dev, qa, uat, prod or another short lower-case name")
    common.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    common.add_argument("--json", action="store_true")

    task = argparse.ArgumentParser(add_help=False)
    task.add_argument("--task", required=True, help="task id the sandbox belongs to")

    c = ssub.add_parser("create", parents=[common, task], help="create a sandbox and deploy bundles into it")
    c.add_argument("--ttl-minutes", type=int, default=120, help=f"time limit after which the reaper drops it (default 120, max {MAX_TTL_MINUTES})")
    c.add_argument("--warehouse-size", default="XSMALL", choices=WAREHOUSE_SIZES)
    c.add_argument("--bundle", action="append", default=[], help="release bundle directory to deploy; repeatable")
    c.set_defaults(func=cmd_create)

    d = ssub.add_parser("destroy", parents=[common, task], help="drop a sandbox now")
    d.add_argument("--reason", default="manual", help="task_done, task_failed or manual (default)")
    d.add_argument("--detail", default="")
    d.set_defaults(func=cmd_destroy)

    ssub.add_parser("list", parents=[common], help="list the environment's sandboxes").set_defaults(func=cmd_list)
    ssub.add_parser("reap", parents=[common], help="run the reaper now").set_defaults(func=cmd_reap)

    co = ssub.add_parser("cost", parents=[common, task], help="credits used by a sandbox warehouse")
    co.add_argument("--days", type=int, default=7)
    co.set_defaults(func=cmd_cost)

    ch = ssub.add_parser("check", parents=[common], help="live acceptance check for S1.2.3")
    ch.add_argument("--bundle", action="append", default=[])
    ch.add_argument("--ttl-minutes", type=int, default=30)
    ch.add_argument("--warehouse-size", default="XSMALL", choices=WAREHOUSE_SIZES)
    ch.set_defaults(func=cmd_check)

    pii_parser = sub.add_parser("pii", help="PII masking and access history")
    psub = pii_parser.add_subparsers(dest="pii_command", required=True)
    pc = psub.add_parser("check", parents=[common], help="live acceptance check for S1.2.5: policies bound, mask seen by the restricted role, access history answers")
    pc.add_argument("--privileged-role", help="role that sees PII in clear (default <PREFIX>_<ENV>_ENGINEER)")
    pc.add_argument("--restricted-role", help="role that must see the mask (default <PREFIX>_<ENV>_AUDITOR)")
    pc.set_defaults(func=cmd_pii_check)

    dr = sub.add_parser("dryrun", help="run sample files through a drafted config in a sandbox and report; the sandbox is destroyed afterwards")
    dr.add_argument("--config", required=True, help="the drafted config file")
    dr.add_argument("--sample", dest="samples", action="append", required=True, help="sample file the custodian would deliver; repeatable. Its name must match a delivery pattern of the config")
    dr.add_argument("--environment", required=True, help="environment whose foundation the sandbox borrows (external volume, control tables, rejection codes)")
    dr.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    dr.add_argument("--task", help="task id of the sandbox (default: dryrun-<config>-<timestamp>)")
    dr.add_argument("--ttl-minutes", type=int, default=30, help="time limit after which the reaper drops the sandbox should the run die (default 30)")
    dr.add_argument("--warehouse-size", default="XSMALL", choices=WAREHOUSE_SIZES)
    dr.add_argument("--repo", default=".", help="repository root: specs, rules and domains are read from it (default: current directory)")
    dr.add_argument("--bundle", dest="bundles", action="append", help="bundle deployed into the sandbox before the source, relative to --repo; repeatable (default: releases/custodial-reference-data)")
    dr.add_argument("--cdm-ddl", default="domains/custodial/cdm/rendered/1.0/ddl.sql", help="rendered canonical model DDL deployed first, relative to --repo; empty to skip")
    dr.add_argument("--out", default=os.environ.get("ASTRA_DRYRUN_OUT", "work/dryrun"), help="reports are written under <out>/<task id>/ (default: work/dryrun)")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=cmd_dryrun)

    rp = sub.add_parser("replay", help="replay a config change against N days of already-captured history; a difference report grouped by rule and field says whether SME review is needed")
    rp.add_argument("--new", required=True, help="the drafted (new) config file")
    rp.add_argument("--old", help="the config as it was before the change; a file path")
    rp.add_argument("--old-ref", help="git ref to read the old config from, at --new's path (alternative to --old)")
    rp.add_argument("--custodian", required=True, help="custodian whose golden datasets (astra-verify golden capture) supply the historical files")
    rp.add_argument("--days", type=int, default=MIN_DAYS, help=f"business days of already-captured history to replay, most recent first (default {MIN_DAYS})")
    rp.add_argument("--environment", required=True, help="environment whose foundation the two sandboxes borrow")
    rp.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    rp.add_argument("--task", help="task id prefix for the two sandboxes (default: replay-<config>-<timestamp>)")
    rp.add_argument("--repo", default=".", help="repository root: specs, rules, domains and golden/ are read from it (default: current directory)")
    rp.add_argument("--golden", default="golden", help="golden datasets directory, relative to --repo (default: golden)")
    rp.add_argument("--bundle", dest="bundles", action="append", help="bundle deployed into each sandbox before the source, relative to --repo; repeatable (default: releases/custodial-reference-data)")
    rp.add_argument("--cdm-ddl", default="domains/custodial/cdm/rendered/1.0/ddl.sql", help="rendered canonical model DDL deployed first, relative to --repo; empty to skip")
    rp.add_argument("--work", default=os.environ.get("ASTRA_REPLAY_WORK", "work/replay"), help="working directory for file copies and the old config, relative to --repo unless absolute (default: work/replay)")
    rp.add_argument("--out", default=os.environ.get("ASTRA_REPLAY_OUT", "work/replay-reports"), help="reports are written under <out>/<task id>/ (default: work/replay-reports)")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_replay)

    parity_parser = sub.add_parser("parity", help="row and field parity between a golden legacy output and the lakehouse, with configurable keys and tolerances")
    pasub = parity_parser.add_subparsers(dest="parity_command", required=True)
    pacommon = argparse.ArgumentParser(add_help=False)
    pacommon.add_argument("--root", default=".", help="repository root used to display paths (default: current directory)")
    pac = pasub.add_parser("check", parents=[pacommon], help="validate every parity mapping under a golden directory")
    pac.add_argument("golden", nargs="?", default="golden")
    pac.set_defaults(func=cmd_parity_check)
    par = pasub.add_parser("run", parents=[pacommon], help="compare one golden dataset's legacy output with the lakehouse for a business date")
    par.add_argument("--config", required=True, help="the custodian's parity.yaml")
    par.add_argument("--business-date", required=True, help="YYYY-MM-DD, a business date already captured for the custodian")
    par.add_argument("--environment", required=True, help="environment whose lakehouse table is compared")
    par.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    par.add_argument("--golden", default="golden", help="golden datasets directory, relative to --root (default: golden)")
    par.add_argument("--store", required=True, help="the golden store: s3://bucket[/prefix], or a directory")
    par.add_argument("--legacy-version", type=int, help="golden dataset version to compare against (default: the latest captured for the date)")
    par.add_argument("--out", default=os.environ.get("ASTRA_PARITY_OUT", "work/parity"), help="reports are written under <out>/<custodian>-<date>/ (default: work/parity)")
    par.add_argument("--json", action="store_true")
    par.set_defaults(func=cmd_parity_run)

    golden_parser = sub.add_parser("golden", help="golden datasets: the legacy path's outputs captured per business day, hashed, versioned, read-only")
    gsub = golden_parser.add_subparsers(dest="golden_command", required=True)
    gcommon = argparse.ArgumentParser(add_help=False)
    gcommon.add_argument("--root", default=".", help="repository root used to display paths (default: current directory)")
    gk = gsub.add_parser("check", parents=[gcommon], help="validate every capture file and index under a golden directory")
    gk.add_argument("golden", nargs="?", default="golden")
    gk.set_defaults(func=cmd_golden_check)
    gs = gsub.add_parser("status", parents=[gcommon], help=f"business days captured per custodian against the {MIN_DAYS} to {MAX_DAYS} the replay needs")
    gs.add_argument("golden", nargs="?", default="golden")
    gs.add_argument("--json", action="store_true")
    gs.set_defaults(func=cmd_golden_status)
    gc = gsub.add_parser("capture", parents=[gcommon], help="replay every business day of a range through the legacy path and store the outputs and rejections")
    gc.add_argument("capture", help="the custodian's directory under golden/, or its capture.yaml")
    gc.add_argument("--from", dest="start", required=True, help="first business date, YYYY-MM-DD")
    gc.add_argument("--to", dest="end", required=True, help="last business date, YYYY-MM-DD")
    gc.add_argument("--store", required=True, help="the golden store: s3://bucket[/prefix], or a directory for a trial")
    gc.add_argument("--files", help="override the capture file's files.location")
    gc.add_argument("--work", default=os.environ.get("ASTRA_GOLDEN_WORK", "work/golden"), help="working directory for file copies and replay logs (default: work/golden)")
    gc.set_defaults(func=cmd_golden_capture, legacy=None)
    gv = gsub.add_parser("verify", parents=[gcommon], help="re-read every indexed version from the store and check that every file still hashes as its manifest says")
    gv.add_argument("capture", help="the custodian's directory under golden/, or its capture.yaml")
    gv.add_argument("--store", required=True)
    gv.set_defaults(func=cmd_golden_verify)

    snowpark_parser = sub.add_parser("snowpark", help="Snowpark Connect assessment of Spark transformers")
    spsub = snowpark_parser.add_subparsers(dest="snowpark_command", required=True)
    sa = spsub.add_parser("assess", help="run the assessment's transformers on an engine, record effort and result, and rewrite the memo's evidence")
    sa.add_argument("assessment", help="assessment directory, for example assessments/normalizer")
    sa.add_argument("--engine", choices=list(ENGINES), default="local", help="local (a local Spark session, the reference) or snowpark-connect (the same code on Snowflake)")
    sa.add_argument("--transformer", dest="transformers", action="append", help="run only this transformer; repeatable (default: all)")
    sa.add_argument("--json", action="store_true")
    sa.set_defaults(func=cmd_snowpark_assess)

    reference_parser = sub.add_parser("reference", help="reference-data replication")
    rsub = reference_parser.add_subparsers(dest="reference_command", required=True)
    rs = rsub.add_parser("status", parents=[common], help="per feed: last run, row counts, delta and freshness; exit 1 when a feed failed or is stale")
    rs.set_defaults(func=cmd_reference_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except BundleError as exc:
        for problem in exc.problems:
            print(problem.format(), file=sys.stderr)
        return 1
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
