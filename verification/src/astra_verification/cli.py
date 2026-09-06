"""Command line: `astra-verify sandbox create | destroy | list | reap | cost | check`.

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


# -- parser ------------------------------------------------------------------


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
