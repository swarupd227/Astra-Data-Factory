"""Ephemeral sandboxes: an isolated database and warehouse for one task.

A sandbox is `<PREFIX>_<ENV>_SBX_<TASK>`, a database with the standard
schemas, Iceberg by default on the environment's volume, and a warehouse of
its own. Both carry the TASK_ID and PURPOSE tags so their cost is attributed
to the task, and the database comment records when the sandbox expires.

The runner destroys the sandbox when the task ends (`sandbox()` does so even
when the task raises). If the runner dies, the REAP_SANDBOXES task in the
environment's CONTROL schema drops the sandbox once the expiry in the
comment passes, or the hard maximum age is reached.

Every statement is built here as text so the sequence can be tested without
an account; execution goes through the astra_data Executor protocol.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence

from astra_data.bundle import Bundle, Executor, Target, deploy, load_bundle

STANDARD_SCHEMAS: tuple[str, ...] = ("BRONZE", "SILVER", "GOLD", "EXCEPTIONS", "CONTROL")
WAREHOUSE_SIZES = ("XSMALL", "SMALL", "MEDIUM", "LARGE", "XLARGE")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
TOKEN_MAX_LENGTH = 40
MAX_TTL_MINUTES = 24 * 60
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def sandbox_token(task_id: str) -> str:
    """The part of the sandbox name derived from the task id: upper case, identifier-safe, bounded."""
    token = re.sub(r"[^A-Za-z0-9]", "_", task_id).upper().strip("_")
    return token[:TOKEN_MAX_LENGTH].rstrip("_")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _format(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def _parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class SandboxSpec:
    """What to create. Names are derived; nothing else about a sandbox is chosen by hand."""

    task_id: str
    environment: str
    prefix: str = "ASTRA"
    ttl_minutes: int = 120
    warehouse_size: str = "XSMALL"
    statement_timeout_seconds: int = 1800
    schemas: tuple[str, ...] = STANDARD_SCHEMAS

    def __post_init__(self) -> None:
        if not TASK_ID_PATTERN.match(self.task_id):
            raise ValueError("task_id must be 1 to 64 characters: letters, digits, dot, underscore, colon, slash or hyphen")
        if not sandbox_token(self.task_id):
            raise ValueError("task_id must contain at least one letter or digit")
        if not 1 <= self.ttl_minutes <= MAX_TTL_MINUTES:
            raise ValueError(f"ttl_minutes must be between 1 and {MAX_TTL_MINUTES}")
        if self.warehouse_size not in WAREHOUSE_SIZES:
            raise ValueError(f"warehouse_size must be one of {', '.join(WAREHOUSE_SIZES)}")
        if self.statement_timeout_seconds <= 0:
            raise ValueError("statement_timeout_seconds must be positive")
        if not self.schemas:
            raise ValueError("at least one schema is required")
        Target(self.environment, self.prefix)  # validates environment and prefix

    @property
    def environment_database(self) -> str:
        return f"{self.prefix}_{self.environment.upper()}"

    @property
    def database(self) -> str:
        return f"{self.environment_database}_SBX_{sandbox_token(self.task_id)}"

    @property
    def warehouse(self) -> str:
        return f"{self.database}_WH"

    @property
    def external_volume(self) -> str:
        return f"{self.environment_database}_ICEBERG"

    @property
    def log_table(self) -> str:
        return f'"{self.environment_database}"."CONTROL"."SANDBOX_LOG"'

    def tag(self, name: str) -> str:
        return f'"{self.environment_database}"."CONTROL"."{name}"'

    def target(self) -> Target:
        return Target(self.environment, self.prefix, database=self.database, warehouse=self.warehouse)


@dataclass(frozen=True)
class Sandbox:
    """A sandbox that exists."""

    task_id: str
    database: str
    warehouse: str
    created_at: datetime
    expires_at: datetime
    deployed: tuple[str, ...] = ()
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "database": self.database,
            "warehouse": self.warehouse,
            "created_at": _format(self.created_at),
            "expires_at": _format(self.expires_at),
            "deployed": list(self.deployed),
            "seconds": round(self.seconds, 1),
        }


@dataclass(frozen=True)
class SandboxRecord:
    """A sandbox as listed from Snowflake."""

    database: str
    task_id: str | None
    created_at: datetime | None
    expires_at: datetime | None

    def is_expired(self, now: datetime, max_age_hours: int = 24) -> bool:
        if self.expires_at is not None and self.expires_at <= now:
            return True
        return self.created_at is not None and self.created_at <= now - timedelta(hours=max_age_hours)


# -- statements ---------------------------------------------------------------


def metadata(spec: SandboxSpec, created_at: datetime, expires_at: datetime) -> str:
    return json.dumps(
        {
            "task": spec.task_id,
            "purpose": "sandbox",
            "environment": spec.environment,
            "created_at": _format(created_at),
            "expires_at": _format(expires_at),
            "ttl_minutes": spec.ttl_minutes,
        },
        separators=(",", ":"),
    )


def query_tag(spec: SandboxSpec) -> str:
    return json.dumps({"astra": {"task": spec.task_id, "sandbox": spec.database, "purpose": "sandbox"}}, separators=(",", ":"))


def tags_clause(spec: SandboxSpec) -> str:
    return f"TAG ({spec.tag('TASK_ID')} = {_literal(spec.task_id)}, {spec.tag('PURPOSE')} = 'sandbox')"


def create_statements(spec: SandboxSpec, created_at: datetime, expires_at: datetime) -> list[str]:
    comment = _literal(metadata(spec, created_at, expires_at))
    statements = [
        f"ALTER SESSION SET QUERY_TAG = {_literal(query_tag(spec))}",
        (
            f'CREATE DATABASE "{spec.database}" '
            f"DATA_RETENTION_TIME_IN_DAYS = 0 "
            f'EXTERNAL_VOLUME = "{spec.external_volume}" '
            f"CATALOG = 'SNOWFLAKE' "
            f"STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE' "
            f"COMMENT = {comment} "
            f"WITH {tags_clause(spec)}"
        ),
    ]
    statements.extend(f'CREATE SCHEMA "{spec.database}"."{schema}" WITH MANAGED ACCESS DATA_RETENTION_TIME_IN_DAYS = 0' for schema in spec.schemas)
    statements.append(
        f'CREATE WAREHOUSE "{spec.warehouse}" '
        f"WAREHOUSE_SIZE = '{spec.warehouse_size}' "
        f"AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = FALSE "
        f"STATEMENT_TIMEOUT_IN_SECONDS = {spec.statement_timeout_seconds} "
        f"COMMENT = {comment} "
        f"WITH {tags_clause(spec)}"
    )
    statements.append(f'USE WAREHOUSE "{spec.warehouse}"')
    statements.append(
        f"INSERT INTO {spec.log_table} (TASK_ID, SANDBOX, EVENT, REASON, DETAIL, OCCURRED_AT) "
        f"VALUES ({_literal(spec.task_id)}, {_literal(spec.database)}, 'created', NULL, "
        f"{_literal('expires_at=' + _format(expires_at) + ', warehouse=' + spec.warehouse_size)}, SYSDATE())"
    )
    return statements


def destroy_statements(spec: SandboxSpec, reason: str, detail: str = "") -> list[str]:
    return [
        f'DROP WAREHOUSE IF EXISTS "{spec.warehouse}"',
        f'DROP DATABASE IF EXISTS "{spec.database}"',
        (
            f"INSERT INTO {spec.log_table} (TASK_ID, SANDBOX, EVENT, REASON, DETAIL, OCCURRED_AT) "
            f"VALUES ({_literal(spec.task_id)}, {_literal(spec.database)}, 'destroyed', {_literal(reason)}, {_literal(detail)}, SYSDATE())"
        ),
    ]


# -- operations ---------------------------------------------------------------


def create(
    executor: Executor,
    spec: SandboxSpec,
    bundles: Sequence[Path | Bundle] = (),
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> Sandbox:
    """Create the sandbox and deploy the given bundles into it. Returns what exists."""
    started = monotonic()
    created_at = clock()
    expires_at = created_at + timedelta(minutes=spec.ttl_minutes)

    loaded = [b if isinstance(b, Bundle) else load_bundle(Path(b)) for b in bundles]
    for statement in create_statements(spec, created_at, expires_at):
        executor.execute_script(statement)

    target = spec.target()
    deployed = tuple(f"{result.bundle} {result.version}" for result in (deploy(bundle, target, executor) for bundle in loaded))

    return Sandbox(spec.task_id, spec.database, spec.warehouse, created_at, expires_at, deployed, monotonic() - started)


def destroy(executor: Executor, spec: SandboxSpec, reason: str = "task_done", detail: str = "") -> None:
    for statement in destroy_statements(spec, reason, detail):
        executor.execute_script(statement)


@contextmanager
def sandbox(executor: Executor, spec: SandboxSpec, bundles: Sequence[Path | Bundle] = ()) -> Iterator[Sandbox]:
    """A sandbox for the duration of a task; destroyed on exit, whatever happened."""
    created = create(executor, spec, bundles)
    try:
        yield created
    except BaseException as exc:
        destroy(executor, spec, reason="task_failed", detail=f"{type(exc).__name__}: {exc}"[:1000])
        raise
    else:
        destroy(executor, spec, reason="task_done")


def list_sandboxes(executor: Executor, environment: str, prefix: str = "ASTRA") -> list[SandboxRecord]:
    """Every sandbox database of the environment, with what its comment says."""
    environment_database = Target(environment, prefix).environment_database
    executor.execute_script(f"SHOW DATABASES LIKE '{environment_database}_SBX_%'")
    rows = executor.query('SELECT "name", "comment", CONVERT_TIMEZONE(\'UTC\', "created_on")::TIMESTAMP_NTZ FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))')
    records = []
    for name, comment, created_on in rows:
        meta = _try_json(comment)
        created_at = _parse(meta.get("created_at")) or (created_on.replace(tzinfo=timezone.utc) if isinstance(created_on, datetime) else None)
        records.append(SandboxRecord(str(name), meta.get("task"), created_at, _parse(meta.get("expires_at"))))
    return records


def reap(executor: Executor, environment: str, prefix: str = "ASTRA") -> int:
    """Run the environment's reaper now. Returns the number of sandboxes dropped."""
    environment_database = Target(environment, prefix).environment_database
    rows = executor.query(f'CALL "{environment_database}"."CONTROL"."REAP_SANDBOXES"()')
    return int(rows[0][0]) if rows and rows[0] else 0


def cost_credits(executor: Executor, spec: SandboxSpec, days: int = 7) -> float:
    """Credits the sandbox warehouse used, from warehouse metering history (minutes to hours of latency)."""
    rows = executor.query(
        f"SELECT COALESCE(SUM(CREDITS_USED), 0) FROM TABLE(\"{spec.environment_database}\".INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY("
        f"DATE_RANGE_START => DATEADD('day', -{int(days)}, CURRENT_DATE()), WAREHOUSE_NAME => {_literal(spec.warehouse)}))"
    )
    return float(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0.0


def _try_json(text: object) -> dict:
    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "MAX_TTL_MINUTES",
    "STANDARD_SCHEMAS",
    "Sandbox",
    "SandboxRecord",
    "SandboxSpec",
    "cost_credits",
    "create",
    "create_statements",
    "destroy",
    "destroy_statements",
    "list_sandboxes",
    "reap",
    "sandbox",
    "sandbox_token",
]
