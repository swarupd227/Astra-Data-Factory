"""Idempotent provisioning of one environment in Snowflake Open Catalog.

For an environment the catalog side needs:

- one internal catalog over the environment's Iceberg bucket
- a catalog role that may manage content (used by Snowflake to sync metadata)
- a catalog role that may only read (used by pg_lake, Spark, DuckDB)
- a principal role for each, and a principal (service connection) for each

Running ``provision`` twice is safe: existing objects are left alone and
reported as unchanged. Credentials are only returned when a principal is
created or explicitly rotated, because Open Catalog shows a secret exactly
once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astra_opencatalog.client import Credentials, OpenCatalogClient

# Privileges of the role Snowflake syncs through. CATALOG_MANAGE_CONTENT
# covers creating namespaces and tables and writing their metadata.
MANAGE_PRIVILEGES: tuple[str, ...] = ("CATALOG_MANAGE_CONTENT",)

# Privileges of the read-only role handed to external engines. Enough to
# discover namespaces and tables and read table data; nothing that writes.
READ_PRIVILEGES: tuple[str, ...] = (
    "CATALOG_READ_PROPERTIES",
    "NAMESPACE_LIST",
    "NAMESPACE_READ_PROPERTIES",
    "TABLE_LIST",
    "TABLE_READ_PROPERTIES",
    "TABLE_READ_DATA",
    "VIEW_LIST",
    "VIEW_READ_PROPERTIES",
)


class ProvisionError(RuntimeError):
    """The catalog exists but does not match what Terraform created."""


@dataclass(frozen=True)
class EnvironmentSpec:
    """Names and storage for one environment. Mirrors the Terraform naming rule."""

    prefix: str
    environment: str
    base_location: str
    role_arn: str

    def __post_init__(self) -> None:
        if not self.prefix.isidentifier() or self.prefix != self.prefix.lower():
            raise ValueError("prefix must be a lower-case identifier, e.g. astra")
        if self.environment not in ("dev", "qa", "uat", "prod"):
            raise ValueError("environment must be one of dev, qa, uat, prod")
        if not self.base_location.startswith("s3://") or not self.base_location.endswith("/"):
            raise ValueError("base_location must look like s3://bucket/ or s3://bucket/prefix/")
        if not self.role_arn.startswith("arn:aws:iam::"):
            raise ValueError("role_arn must be an IAM role ARN")

    @property
    def catalog(self) -> str:
        return f"{self.prefix}_{self.environment}"

    @property
    def manage_catalog_role(self) -> str:
        return f"{self.catalog}_manage"

    @property
    def read_catalog_role(self) -> str:
        return f"{self.catalog}_read"

    @property
    def sync_principal_role(self) -> str:
        return f"{self.catalog}_sync"

    @property
    def reader_principal_role(self) -> str:
        return f"{self.catalog}_reader"

    @property
    def sync_principal(self) -> str:
        return f"{self.catalog}_snowflake_sync"

    @property
    def reader_principal(self) -> str:
        return f"{self.catalog}_reader"


@dataclass(frozen=True)
class StorageIdentity:
    """What Open Catalog will present when assuming the bucket role."""

    iam_user_arn: str | None
    external_id: str | None


@dataclass
class ProvisionResult:
    catalog: str
    storage: StorageIdentity
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    credentials: dict[str, Credentials] = field(default_factory=dict)

    def record(self, label: str, created: bool) -> None:
        (self.created if created else self.unchanged).append(label)


def provision(client: OpenCatalogClient, spec: EnvironmentSpec, *, rotate_credentials: bool = False) -> ProvisionResult:
    catalog, created = _ensure_catalog(client, spec)
    result = ProvisionResult(catalog=spec.catalog, storage=_storage_identity(catalog))
    result.record(f"catalog {spec.catalog}", created)

    for role, privileges in (
        (spec.manage_catalog_role, MANAGE_PRIVILEGES),
        (spec.read_catalog_role, READ_PRIVILEGES),
    ):
        result.record(f"catalog role {role}", _ensure_catalog_role(client, spec.catalog, role))
        for privilege in privileges:
            result.record(f"grant {privilege} to {role}", _ensure_grant(client, spec.catalog, role, privilege))

    for principal_role, catalog_role in (
        (spec.sync_principal_role, spec.manage_catalog_role),
        (spec.reader_principal_role, spec.read_catalog_role),
    ):
        result.record(f"principal role {principal_role}", _ensure_principal_role(client, principal_role))
        result.record(
            f"principal role {principal_role} -> catalog role {catalog_role}",
            _ensure_catalog_role_assignment(client, principal_role, spec.catalog, catalog_role),
        )

    for principal, principal_role in (
        (spec.sync_principal, spec.sync_principal_role),
        (spec.reader_principal, spec.reader_principal_role),
    ):
        created, credentials = _ensure_principal(client, principal, rotate=rotate_credentials)
        result.record(f"principal {principal}", created)
        if credentials is not None:
            result.credentials[principal] = credentials
        result.record(
            f"principal {principal} -> principal role {principal_role}",
            _ensure_principal_role_assignment(client, principal, principal_role),
        )

    return result


# -- ensure helpers: each returns True when it created something -----------


def _ensure_catalog(client: OpenCatalogClient, spec: EnvironmentSpec) -> tuple[dict, bool]:
    existing = client.get_catalog(spec.catalog)
    if existing is None:
        return client.create_catalog(spec.catalog, base_location=spec.base_location, role_arn=spec.role_arn), True

    properties = existing.get("properties") or {}
    storage = existing.get("storageConfigInfo") or {}
    mismatches = []
    if properties.get("default-base-location") != spec.base_location:
        mismatches.append(f"default-base-location is {properties.get('default-base-location')!r}, expected {spec.base_location!r}")
    if storage.get("roleArn") != spec.role_arn:
        mismatches.append(f"roleArn is {storage.get('roleArn')!r}, expected {spec.role_arn!r}")
    if mismatches:
        raise ProvisionError(
            f"catalog {spec.catalog} exists with different storage: " + "; ".join(mismatches) + ". Fix Terraform or the catalog; this tool does not change storage of an existing catalog."
        )
    return existing, False


def _storage_identity(catalog: dict) -> StorageIdentity:
    storage = catalog.get("storageConfigInfo") or {}
    return StorageIdentity(iam_user_arn=storage.get("userArn"), external_id=storage.get("externalId"))


def _ensure_catalog_role(client: OpenCatalogClient, catalog: str, role: str) -> bool:
    if client.get_catalog_role(catalog, role) is not None:
        return False
    client.create_catalog_role(catalog, role)
    return True


def _ensure_grant(client: OpenCatalogClient, catalog: str, role: str, privilege: str) -> bool:
    for grant in client.grants_of(catalog, role):
        if grant.get("type") == "catalog" and grant.get("privilege") == privilege:
            return False
    client.add_catalog_grant(catalog, role, privilege)
    return True


def _ensure_principal_role(client: OpenCatalogClient, role: str) -> bool:
    if client.get_principal_role(role) is not None:
        return False
    client.create_principal_role(role)
    return True


def _ensure_catalog_role_assignment(client: OpenCatalogClient, principal_role: str, catalog: str, catalog_role: str) -> bool:
    if catalog_role in client.catalog_roles_of(principal_role, catalog):
        return False
    client.assign_catalog_role(principal_role, catalog, catalog_role)
    return True


def _ensure_principal(client: OpenCatalogClient, principal: str, *, rotate: bool) -> tuple[bool, Credentials | None]:
    if client.get_principal(principal) is None:
        return True, client.create_principal(principal)
    if rotate:
        return False, client.rotate_credentials(principal)
    return False, None


def _ensure_principal_role_assignment(client: OpenCatalogClient, principal: str, principal_role: str) -> bool:
    if principal_role in client.principal_roles_of(principal):
        return False
    client.assign_principal_role(principal, principal_role)
    return True
