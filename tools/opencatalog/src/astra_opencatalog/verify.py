"""Acceptance checks for S1.1.2 against live systems.

1. A table created in Snowflake is listed by the Iceberg REST catalog within
   one minute.
2. An external engine (DuckDB) reads the table through the catalog.
3. A principal without the catalog grant is denied.

The orchestration (``run_verification``) takes callables for the live pieces
so the timing and decision logic can be tested without accounts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from astra_opencatalog.client import AccessDenied, Credentials, OpenCatalogClient


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    seconds: float | None = None


class ProbeTable(Protocol):
    """A table the verification creates in Snowflake and drops afterwards."""

    database: str
    schema: str
    name: str

    def create(self) -> None: ...

    def drop(self) -> None: ...


def find_table(client: OpenCatalogClient, catalog: str, table: str) -> list[str] | None:
    """Return the namespace that lists ``table``, searching every namespace."""
    for namespace in client.list_all_namespaces(catalog):
        if table in client.list_tables(catalog, namespace):
            return namespace
    return None


def wait_until_listed(
    client: OpenCatalogClient,
    catalog: str,
    table: str,
    *,
    timeout_seconds: float = 60.0,
    poll_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> CheckResult:
    started = clock()
    deadline = started + timeout_seconds
    attempts = 0
    while True:
        attempts += 1
        namespace = find_table(client, catalog, table)
        elapsed = clock() - started
        if namespace is not None:
            return CheckResult(
                name="table listed by the Iceberg REST catalog",
                passed=elapsed <= timeout_seconds,
                detail=f"{'.'.join(namespace)}.{table} listed after {elapsed:.1f}s ({attempts} polls)",
                seconds=elapsed,
            )
        if clock() >= deadline:
            return CheckResult(
                name="table listed by the Iceberg REST catalog",
                passed=False,
                detail=f"{table} not listed within {timeout_seconds:.0f}s ({attempts} polls)",
                seconds=elapsed,
            )
        sleep(poll_seconds)


def check_access_denied(
    admin: OpenCatalogClient,
    catalog: str,
    *,
    make_client: Callable[[Credentials], OpenCatalogClient],
    probe_principal: str | None = None,
) -> CheckResult:
    """Create a principal with no roles, confirm it cannot list the catalog, delete it."""
    name = probe_principal or f"astra_probe_{uuid.uuid4().hex[:8]}"
    credentials = admin.create_principal(name)
    try:
        probe = make_client(credentials)
        try:
            probe.list_namespaces(catalog)
        except AccessDenied as denied:
            return CheckResult(
                name="access denied without the catalog grant",
                passed=True,
                detail=f"principal {name} without roles got {denied.status_code} listing {catalog}",
            )
        finally:
            probe.close()
        return CheckResult(
            name="access denied without the catalog grant",
            passed=False,
            detail=f"principal {name} without roles could list namespaces in {catalog}",
        )
    finally:
        admin.delete_principal(name)


def read_with_duckdb(
    *,
    catalog_api_url: str,
    token_url: str,
    catalog: str,
    namespace: list[str],
    table: str,
    credentials: Credentials,
) -> int:
    """Count the rows of a table through the Iceberg REST catalog with DuckDB.

    Returns the row count. Requires the ``verify`` extra (duckdb).
    """
    import duckdb  # imported lazily: only the verify command needs it

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    con = duckdb.connect()
    try:
        con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
        con.execute(
            "CREATE SECRET open_catalog ("
            "TYPE ICEBERG, "
            f"CLIENT_ID {literal(credentials.client_id)}, "
            f"CLIENT_SECRET {literal(credentials.client_secret)}, "
            f"OAUTH2_SERVER_URI {literal(token_url)}, "
            "OAUTH2_SCOPE 'PRINCIPAL_ROLE:ALL')"
        )
        con.execute(f"ATTACH {literal(catalog)} AS oc (TYPE ICEBERG, SECRET open_catalog, ENDPOINT {literal(catalog_api_url)})")
        schema = ".".join(namespace).replace('"', '""')
        ident = table.replace('"', '""')
        row = con.execute(f'SELECT count(*) FROM oc."{schema}"."{ident}"').fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def run_verification(
    admin: OpenCatalogClient,
    catalog: str,
    probe: ProbeTable,
    *,
    make_client: Callable[[Credentials], OpenCatalogClient],
    read_rows: Callable[[list[str], str], int] | None,
    expected_rows: int = 1,
    timeout_seconds: float = 60.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[CheckResult]:
    """Run the three acceptance checks and always drop the probe table."""
    results: list[CheckResult] = []
    probe.create()
    try:
        listed = wait_until_listed(admin, catalog, probe.name, timeout_seconds=timeout_seconds, clock=clock, sleep=sleep)
        results.append(listed)

        if read_rows is None:
            results.append(CheckResult("external engine reads the table", False, "skipped: no reader configured"))
        elif not listed.passed:
            results.append(CheckResult("external engine reads the table", False, "skipped: table was not listed"))
        else:
            namespace = find_table(admin, catalog, probe.name) or []
            try:
                count = read_rows(namespace, probe.name)
                results.append(
                    CheckResult(
                        "external engine reads the table",
                        count == expected_rows,
                        f"read {count} row(s), expected {expected_rows}",
                    )
                )
            except Exception as exc:  # the engine's own errors are the finding
                results.append(CheckResult("external engine reads the table", False, f"{type(exc).__name__}: {exc}"))

        results.append(check_access_denied(admin, catalog, make_client=make_client))
    finally:
        probe.drop()
    return results
