#!/usr/bin/env python3
"""Live acceptance checks for S1.1.3 (Snowpipe on S3 events).

  1. A file dropped in the landing prefix appears as rows in BRONZE.RAW_LINES
     within 60 seconds.
  2. Each row carries file name, row number and ingest timestamp.
  3. The same file dropped again (same name, same content) is not loaded
     twice and is logged as DUPLICATE in CONTROL.FILE_LOAD_LOG.

Usage:
  python scripts/verify_snowpipe.py --environment dev [--json]

Needs: boto3, snowflake-connector-python. AWS credentials from the usual
chain; Snowflake connection from the environment:

  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
  SNOWFLAKE_ROLE (ASTRA_<ENV>_ENGINEER or above), SNOWFLAKE_WAREHOUSE

The file is written under <landing prefix>/_verify/ and deleted afterwards.
Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass

LINES = ["HDR20260905VERIFY", "DTL000000001ACME", "DTL000000002BETA", "TRL0000000002"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    seconds: float | None = None


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"error: environment variable {name} is not set")
    return value


def connect_snowflake():
    import snowflake.connector

    return snowflake.connector.connect(
        account=env("SNOWFLAKE_ACCOUNT"),
        user=env("SNOWFLAKE_USER"),
        private_key_file=env("SNOWFLAKE_PRIVATE_KEY_PATH"),
        role=env("SNOWFLAKE_ROLE"),
        warehouse=env("SNOWFLAKE_WAREHOUSE"),
    )


def query(con, sql: str, params: tuple = ()) -> list[tuple]:
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def wait_for_rows(con, table: str, file_name: str, expected: int, timeout: float, poll: float = 3.0) -> CheckResult:
    started = time.monotonic()
    while True:
        (count,) = query(con, f"SELECT COUNT(*) FROM {table} WHERE FILE_NAME = %s", (file_name,))[0]
        elapsed = time.monotonic() - started
        if count >= expected:
            return CheckResult("file appears in RAW_LINES within 60 seconds", elapsed <= timeout, f"{count} rows after {elapsed:.0f}s", elapsed)
        if elapsed >= timeout:
            return CheckResult("file appears in RAW_LINES within 60 seconds", False, f"{count} of {expected} rows after {elapsed:.0f}s", elapsed)
        time.sleep(poll)


def check_row_metadata(con, table: str, file_name: str) -> CheckResult:
    rows = query(
        con,
        f"SELECT ROW_NUMBER, LINE, INGESTED_AT FROM {table} WHERE FILE_NAME = %s ORDER BY ROW_NUMBER",
        (file_name,),
    )
    numbers = [r[0] for r in rows]
    lines = [r[1] for r in rows]
    stamped = all(r[2] is not None for r in rows)
    ok = numbers == list(range(1, len(LINES) + 1)) and lines == LINES and stamped
    detail = f"row numbers {numbers}, ingest timestamps {'present' if stamped else 'missing'}, lines {'match' if lines == LINES else 'differ'}"
    return CheckResult("rows carry file name, row number and ingest timestamp", ok, detail)


def wait_for_log(con, log_table: str, file_name: str, status: str, timeout: float, poll: float = 5.0) -> CheckResult:
    started = time.monotonic()
    while True:
        rows = query(con, f"SELECT STATUS, DETAIL FROM {log_table} WHERE FILE_NAME = %s ORDER BY OBSERVED_AT", (file_name,))
        statuses = [r[0] for r in rows]
        elapsed = time.monotonic() - started
        if status in statuses:
            return CheckResult(f"re-dropped file is logged as {status}", True, f"log entries {statuses} after {elapsed:.0f}s", elapsed)
        if elapsed >= timeout:
            return CheckResult(f"re-dropped file is logged as {status}", False, f"log entries {statuses} after {elapsed:.0f}s", elapsed)
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "uat", "prod"])
    parser.add_argument("--prefix", default="ASTRA")
    parser.add_argument("--bucket", help="landing bucket (default: Terraform naming <prefix>-<env>-landing-<account id>)")
    parser.add_argument("--landing-prefix", default="landing")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--reconcile-wait", type=float, default=180.0, help="seconds to wait for the reconciliation task to log the duplicate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    import boto3

    s3 = boto3.client("s3")
    if args.bucket:
        bucket = args.bucket
    else:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        bucket = f"{args.prefix.lower()}-{args.environment}-landing-{account_id}"

    database = f"{args.prefix}_{args.environment.upper()}"
    raw_lines = f'"{database}"."BRONZE"."RAW_LINES"'
    log_table = f'"{database}"."CONTROL"."FILE_LOAD_LOG"'

    relative = f"_verify/verify_{uuid.uuid4().hex[:8]}.dat"
    key = f"{args.landing_prefix}/{relative}"
    body = ("\n".join(LINES) + "\n").encode()

    results: list[CheckResult] = []
    con = connect_snowflake()
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        listed = wait_for_rows(con, raw_lines, relative, len(LINES), args.timeout)
        results.append(listed)
        if listed.passed:
            results.append(check_row_metadata(con, raw_lines, relative))

            # Same name, same content, dropped again.
            s3.put_object(Bucket=bucket, Key=key, Body=body)
            time.sleep(min(args.timeout, 60))
            (count,) = query(con, f"SELECT COUNT(*) FROM {raw_lines} WHERE FILE_NAME = %s", (relative,))[0]
            results.append(CheckResult("duplicate file is not loaded twice", count == len(LINES), f"{count} rows after re-drop, expected {len(LINES)}"))
            results.append(wait_for_log(con, log_table, relative, "DUPLICATE", args.reconcile_wait))
    finally:
        s3.delete_object(Bucket=bucket, Key=key)
        con.close()

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        width = max(len(r.name) for r in results)
        for r in results:
            print(f"{'PASS' if r.passed else 'FAIL'}  {r.name.ljust(width)}  {r.detail}")
    return 0 if results and all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
