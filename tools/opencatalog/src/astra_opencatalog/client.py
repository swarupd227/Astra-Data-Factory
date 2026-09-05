"""HTTP client for the Snowflake Open Catalog management API and Iceberg REST API.

Open Catalog is Snowflake's hosted Apache Polaris. Both APIs sit under
``https://<account>.snowflakecomputing.com/polaris``:

- ``/api/management/v1`` manages catalogs, principals, roles and grants
- ``/api/catalog`` is the Iceberg REST catalog that external engines read

Authentication is OAuth client credentials against the catalog API's token
endpoint. The client fetches a token lazily and refreshes it before expiry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote

import httpx

MANAGEMENT_PREFIX = "/polaris/api/management/v1"
CATALOG_PREFIX = "/polaris/api/catalog"
TOKEN_PATH = f"{CATALOG_PREFIX}/v1/oauth/tokens"
DEFAULT_SCOPE = "PRINCIPAL_ROLE:ALL"

# Iceberg REST encodes multi-level namespaces with the unit separator.
NAMESPACE_SEPARATOR = "\x1f"


class OpenCatalogError(RuntimeError):
    """A non-success response from Open Catalog."""

    def __init__(self, status_code: int, method: str, path: str, detail: str) -> None:
        self.status_code = status_code
        self.method = method
        self.path = path
        self.detail = detail
        super().__init__(f"{method} {path} returned {status_code}: {detail}")


class AccessDenied(OpenCatalogError):
    """Open Catalog refused the request for lack of privilege (401 or 403)."""


class NotFound(OpenCatalogError):
    """The addressed object does not exist (404)."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or body)
        for key in ("message", "error_description", "detail"):
            if key in body:
                return str(body[key])
    return str(body)


def encode_namespace(namespace: Iterable[str]) -> str:
    return NAMESPACE_SEPARATOR.join(namespace)


class OpenCatalogClient:
    """Thin, explicit wrapper over the two Open Catalog APIs.

    Every method maps to one HTTP call and returns plain dicts or lists so the
    provisioning logic stays readable and testable with a fake transport.
    """

    def __init__(
        self,
        account_url: str,
        credentials: Credentials,
        *,
        scope: str = DEFAULT_SCOPE,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        clock=time.monotonic,
    ) -> None:
        self.account_url = account_url.rstrip("/")
        self._credentials = credentials
        self._scope = scope
        self._clock = clock
        self._http = httpx.Client(base_url=self.account_url, timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # -- plumbing ---------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OpenCatalogClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def catalog_api_url(self) -> str:
        return f"{self.account_url}{CATALOG_PREFIX}"

    @property
    def token_url(self) -> str:
        return f"{self.account_url}{TOKEN_PATH}"

    def _access_token(self) -> str:
        if self._token and self._clock() < self._token_expires_at:
            return self._token
        response = self._http.post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "scope": self._scope,
            },
        )
        if response.status_code != 200:
            raise _classify(response, "POST", TOKEN_PATH)
        body = response.json()
        self._token = str(body["access_token"])
        # Refresh a minute early so a token never expires mid-request.
        self._token_expires_at = self._clock() + float(body.get("expires_in", 3600)) - 60
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> httpx.Response:
        response = self._http.request(
            method,
            path,
            json=json,
            params=params,
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        if response.status_code not in ok:
            raise _classify(response, method, path)
        return response

    def _get_or_none(self, path: str) -> dict | None:
        try:
            return self._request("GET", path).json()
        except NotFound:
            return None

    # -- catalogs ---------------------------------------------------------

    def get_catalog(self, name: str) -> dict | None:
        return self._get_or_none(f"{MANAGEMENT_PREFIX}/catalogs/{name}")

    def create_catalog(
        self,
        name: str,
        *,
        base_location: str,
        role_arn: str,
        allowed_locations: list[str] | None = None,
    ) -> dict:
        body = {
            "catalog": {
                "name": name,
                "type": "INTERNAL",
                "properties": {"default-base-location": base_location},
                "storageConfigInfo": {
                    "storageType": "S3",
                    "roleArn": role_arn,
                    "allowedLocations": allowed_locations or [base_location],
                },
            }
        }
        self._request("POST", f"{MANAGEMENT_PREFIX}/catalogs", json=body)
        created = self.get_catalog(name)
        if created is None:
            raise OpenCatalogError(404, "GET", f"{MANAGEMENT_PREFIX}/catalogs/{name}", "catalog vanished after creation")
        return created

    # -- catalog roles and grants -----------------------------------------

    def get_catalog_role(self, catalog: str, name: str) -> dict | None:
        return self._get_or_none(f"{MANAGEMENT_PREFIX}/catalogs/{catalog}/catalog-roles/{name}")

    def create_catalog_role(self, catalog: str, name: str) -> None:
        self._request(
            "POST",
            f"{MANAGEMENT_PREFIX}/catalogs/{catalog}/catalog-roles",
            json={"catalogRole": {"name": name}},
        )

    def grants_of(self, catalog: str, catalog_role: str) -> list[dict]:
        body = self._request("GET", f"{MANAGEMENT_PREFIX}/catalogs/{catalog}/catalog-roles/{catalog_role}/grants").json()
        return list(body.get("grants", []))

    def add_catalog_grant(self, catalog: str, catalog_role: str, privilege: str) -> None:
        self._request(
            "PUT",
            f"{MANAGEMENT_PREFIX}/catalogs/{catalog}/catalog-roles/{catalog_role}/grants",
            json={"grant": {"type": "catalog", "privilege": privilege}},
        )

    # -- principal roles --------------------------------------------------

    def get_principal_role(self, name: str) -> dict | None:
        return self._get_or_none(f"{MANAGEMENT_PREFIX}/principal-roles/{name}")

    def create_principal_role(self, name: str) -> None:
        self._request("POST", f"{MANAGEMENT_PREFIX}/principal-roles", json={"principalRole": {"name": name}})

    def catalog_roles_of(self, principal_role: str, catalog: str) -> list[str]:
        body = self._request("GET", f"{MANAGEMENT_PREFIX}/principal-roles/{principal_role}/catalog-roles/{catalog}").json()
        return [role["name"] for role in body.get("roles", [])]

    def assign_catalog_role(self, principal_role: str, catalog: str, catalog_role: str) -> None:
        self._request(
            "PUT",
            f"{MANAGEMENT_PREFIX}/principal-roles/{principal_role}/catalog-roles/{catalog}",
            json={"catalogRole": {"name": catalog_role}},
        )

    # -- principals -------------------------------------------------------

    def get_principal(self, name: str) -> dict | None:
        return self._get_or_none(f"{MANAGEMENT_PREFIX}/principals/{name}")

    def create_principal(self, name: str) -> Credentials:
        body = self._request(
            "POST",
            f"{MANAGEMENT_PREFIX}/principals",
            json={"principal": {"name": name}, "credentialRotationRequired": False},
        ).json()
        return _credentials_from(body)

    def rotate_credentials(self, name: str) -> Credentials:
        body = self._request("POST", f"{MANAGEMENT_PREFIX}/principals/{name}/rotate").json()
        return _credentials_from(body)

    def delete_principal(self, name: str) -> None:
        self._request("DELETE", f"{MANAGEMENT_PREFIX}/principals/{name}")

    def principal_roles_of(self, principal: str) -> list[str]:
        body = self._request("GET", f"{MANAGEMENT_PREFIX}/principals/{principal}/principal-roles").json()
        return [role["name"] for role in body.get("roles", [])]

    def assign_principal_role(self, principal: str, principal_role: str) -> None:
        self._request(
            "PUT",
            f"{MANAGEMENT_PREFIX}/principals/{principal}/principal-roles",
            json={"principalRole": {"name": principal_role}},
        )

    # -- Iceberg REST (read side) -----------------------------------------

    def list_namespaces(self, catalog: str, parent: list[str] | None = None) -> list[list[str]]:
        params = {"parent": encode_namespace(parent)} if parent else None
        body = self._request("GET", f"{CATALOG_PREFIX}/v1/{catalog}/namespaces", params=params).json()
        return [list(ns) for ns in body.get("namespaces", [])]

    def list_all_namespaces(self, catalog: str) -> list[list[str]]:
        """Every namespace at every depth, parents before children."""
        found: list[list[str]] = []
        pending: list[list[str] | None] = [None]
        while pending:
            parent = pending.pop(0)
            for namespace in self.list_namespaces(catalog, parent):
                found.append(namespace)
                pending.append(namespace)
        return found

    def list_tables(self, catalog: str, namespace: list[str]) -> list[str]:
        path = f"{CATALOG_PREFIX}/v1/{catalog}/namespaces/{quote(encode_namespace(namespace), safe='')}/tables"
        body = self._request("GET", path).json()
        return [ident["name"] for ident in body.get("identifiers", [])]


def _credentials_from(body: dict) -> Credentials:
    creds = body.get("credentials") or {}
    try:
        return Credentials(client_id=str(creds["clientId"]), client_secret=str(creds["clientSecret"]))
    except KeyError as exc:
        raise OpenCatalogError(200, "POST", "principals", f"response carried no credentials: missing {exc}") from exc


def _classify(response: httpx.Response, method: str, path: str) -> OpenCatalogError:
    detail = _error_detail(response)
    if response.status_code in (401, 403):
        return AccessDenied(response.status_code, method, path, detail)
    if response.status_code == 404:
        return NotFound(response.status_code, method, path, detail)
    return OpenCatalogError(response.status_code, method, path, detail)
