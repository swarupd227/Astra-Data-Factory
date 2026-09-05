"""Command line entry point: ``opencatalog provision`` and ``opencatalog verify``.

Connection and credentials come from the environment, never from arguments,
so nothing secret lands in shell history or CI logs:

  OPEN_CATALOG_URL            https://<org>-<account>.snowflakecomputing.com
  OPEN_CATALOG_CLIENT_ID      service connection with the Open Catalog admin role
  OPEN_CATALOG_CLIENT_SECRET

``verify`` additionally needs:

  OPEN_CATALOG_READER_CLIENT_ID / _SECRET   the environment's reader principal
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
  SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE       to create the probe table
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass

from astra_opencatalog.client import Credentials, OpenCatalogClient, OpenCatalogError
from astra_opencatalog.provision import EnvironmentSpec, ProvisionError, ProvisionResult, provision
from astra_opencatalog.verify import CheckResult, read_with_duckdb, run_verification


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"error: environment variable {name} is not set")
    return value


def environment_name(value: str) -> str:
    """argparse type: the same rule as the Terraform `environment` variable."""
    import re

    if not re.fullmatch(r"[a-z][a-z0-9]{1,7}", value):
        raise argparse.ArgumentTypeError("must be 2 to 8 lower-case letters or digits, starting with a letter")
    return value


def _admin_client() -> OpenCatalogClient:
    return OpenCatalogClient(
        _env("OPEN_CATALOG_URL"),
        Credentials(_env("OPEN_CATALOG_CLIENT_ID"), _env("OPEN_CATALOG_CLIENT_SECRET")),
    )


# -- provision ----------------------------------------------------------------


def _print_provision(result: ProvisionResult, spec: EnvironmentSpec, as_json: bool) -> None:
    if as_json:
        payload = {
            "catalog": result.catalog,
            "storage": asdict(result.storage),
            "created": result.created,
            "unchanged": result.unchanged,
            "credentials": {name: asdict(c) for name, c in result.credentials.items()},
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"Catalog {result.catalog}")
    for label in result.created:
        print(f"  created    {label}")
    for label in result.unchanged:
        print(f"  unchanged  {label}")

    print()
    print("Terraform: set these in environments/<env>.tfvars under open_catalog")
    print(f"  iam_user_arn = {result.storage.iam_user_arn or '<not reported; read it from the catalog in Open Catalog>'}")
    print(f"  external_id  = {result.storage.external_id or '<not reported; read it from the catalog in Open Catalog>'}")

    if result.credentials:
        print()
        print("Credentials (shown once; store them in the secret manager now)")
        for name, creds in result.credentials.items():
            purpose = "Terraform open_catalog_client_id / TF_VAR_open_catalog_client_secret" if name == spec.sync_principal else "OPEN_CATALOG_READER_CLIENT_ID / _SECRET for external engines"
            print(f"  {name}")
            print(f"    client_id     {creds.client_id}")
            print(f"    client_secret {creds.client_secret}")
            print(f"    use           {purpose}")


def cmd_provision(args: argparse.Namespace) -> int:
    spec = EnvironmentSpec(
        prefix=args.prefix,
        environment=args.environment,
        base_location=args.base_location,
        role_arn=args.role_arn,
    )
    with _admin_client() as client:
        result = provision(client, spec, rotate_credentials=args.rotate_credentials)
    _print_provision(result, spec, args.json)
    return 0


# -- verify -------------------------------------------------------------------


@dataclass
class SnowflakeProbeTable:
    database: str
    schema: str
    name: str

    def _connect(self):
        import snowflake.connector  # imported lazily: only verify needs it

        return snowflake.connector.connect(
            account=_env("SNOWFLAKE_ACCOUNT"),
            user=_env("SNOWFLAKE_USER"),
            private_key_file=_env("SNOWFLAKE_PRIVATE_KEY_PATH"),
            role=_env("SNOWFLAKE_ROLE"),
            warehouse=_env("SNOWFLAKE_WAREHOUSE"),
        )

    @property
    def qualified(self) -> str:
        return f'"{self.database}"."{self.schema}"."{self.name}"'

    def create(self) -> None:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(f"CREATE ICEBERG TABLE {self.qualified} (id INT, note STRING)")
            cur.execute(f"INSERT INTO {self.qualified} VALUES (1, 'open catalog sync probe')")

    def drop(self) -> None:
        with self._connect() as con:
            con.cursor().execute(f"DROP ICEBERG TABLE IF EXISTS {self.qualified}")


def _print_results(results: list[CheckResult], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(r) for r in results], indent=2))
        return
    width = max(len(r.name) for r in results)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{status}  {r.name.ljust(width)}  {r.detail}")


def cmd_verify(args: argparse.Namespace) -> int:
    import uuid

    catalog = f"{args.prefix}_{args.environment}"
    probe = SnowflakeProbeTable(
        database=args.database or f"{args.prefix.upper()}_{args.environment.upper()}",
        schema=args.schema,
        name=f"OC_SYNC_PROBE_{uuid.uuid4().hex[:8].upper()}",
    )

    with _admin_client() as admin:
        reader_id = os.environ.get("OPEN_CATALOG_READER_CLIENT_ID")
        reader_secret = os.environ.get("OPEN_CATALOG_READER_CLIENT_SECRET")
        read_rows = None
        if reader_id and reader_secret and not args.skip_read:
            reader = Credentials(reader_id, reader_secret)

            def read_rows(namespace: list[str], table: str) -> int:
                return read_with_duckdb(
                    catalog_api_url=admin.catalog_api_url,
                    token_url=admin.token_url,
                    catalog=catalog,
                    namespace=namespace,
                    table=table,
                    credentials=reader,
                )

        results = run_verification(
            admin,
            catalog,
            probe,
            make_client=lambda creds: OpenCatalogClient(admin.account_url, creds),
            read_rows=read_rows,
            timeout_seconds=args.timeout,
        )

    _print_results(results, args.json)
    return 0 if all(r.passed for r in results) else 1


# -- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opencatalog", description="Provision and verify Snowflake Open Catalog for an Astra Data Factory environment.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--environment", required=True, type=environment_name, help="dev, qa, uat, prod or another short lower-case name")
    common.add_argument("--prefix", default="astra", help="object name prefix, lower case (default: astra)")
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("provision", parents=[common], help="create the catalog, roles and principals for an environment (idempotent)")
    p.add_argument("--base-location", required=True, help="s3://bucket/ from the Terraform output iceberg_base_url")
    p.add_argument("--role-arn", required=True, help="IAM role ARN from the Terraform output open_catalog_role_arn")
    p.add_argument("--rotate-credentials", action="store_true", help="rotate the credentials of existing principals")
    p.set_defaults(func=cmd_provision)

    v = sub.add_parser("verify", parents=[common], help="run the S1.1.2 acceptance checks against live systems")
    v.add_argument("--database", help="Snowflake database (default: <PREFIX>_<ENV>)")
    v.add_argument("--schema", default="SILVER", help="schema for the probe table (default: SILVER)")
    v.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for the catalog listing (default: 60)")
    v.add_argument("--skip-read", action="store_true", help="skip the DuckDB read check")
    v.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OpenCatalogError, ProvisionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
