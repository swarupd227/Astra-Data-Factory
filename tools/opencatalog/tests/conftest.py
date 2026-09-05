"""An in-memory stand-in for Open Catalog, served through httpx.MockTransport.

It implements the subset of the Polaris management API and Iceberg REST API
the tool uses, including the privilege check on the read side, so tests can
prove behaviour end to end without an account.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from astra_opencatalog.client import Credentials, OpenCatalogClient

ADMIN = Credentials("admin-id", "admin-secret")
SEPARATOR = "\x1f"


@dataclass
class FakeOpenCatalog:
    catalogs: dict[str, dict] = field(default_factory=dict)
    catalog_roles: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (catalog, role) -> privileges
    principal_roles: dict[str, dict[str, set[str]]] = field(default_factory=dict)  # role -> catalog -> catalog roles
    principals: dict[str, dict] = field(default_factory=dict)  # name -> {"secret", "roles"}
    tables: dict[str, dict[tuple[str, ...], list[str]]] = field(default_factory=dict)  # catalog -> ns -> tables
    requests: list[tuple[str, str]] = field(default_factory=list)
    secret_counter: int = 0

    # -- test helpers -----------------------------------------------------

    def add_table(self, catalog: str, namespace: tuple[str, ...], table: str) -> None:
        ns = self.tables.setdefault(catalog, {})
        for depth in range(1, len(namespace) + 1):
            ns.setdefault(namespace[:depth], [])
        ns[namespace].append(table)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str | None = None, pattern: str | None = None) -> list[tuple[str, str]]:
        return [
            (m, p)
            for m, p in self.requests
            if (method is None or m == method) and (pattern is None or re.search(pattern, p))
        ]

    # -- privilege model --------------------------------------------------

    def _privileges(self, principal: str, catalog: str) -> set[str]:
        if principal == ADMIN.client_id:
            return {"CATALOG_MANAGE_CONTENT", "NAMESPACE_LIST", "TABLE_LIST"}
        found: set[str] = set()
        for principal_role in self.principals.get(principal, {}).get("roles", set()):
            for catalog_role in self.principal_roles.get(principal_role, {}).get(catalog, set()):
                found |= self.catalog_roles.get((catalog, catalog_role), set())
        return found

    # -- dispatch ---------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        # Record what went on the wire (percent-encoded), not the decoded path.
        self.requests.append((method, request.url.raw_path.decode().split("?")[0]))

        if path == "/polaris/api/catalog/v1/oauth/tokens":
            form = parse_qs(request.content.decode())
            client_id = form.get("client_id", [""])[0]
            secret = form.get("client_secret", [""])[0]
            valid = (client_id == ADMIN.client_id and secret == ADMIN.client_secret) or (
                client_id in self.principals and self.principals[client_id]["secret"] == secret
            )
            if not valid:
                return _json(401, {"error": {"message": "invalid client"}})
            return _json(200, {"access_token": f"tok-{client_id}", "token_type": "bearer", "expires_in": 3600})

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer tok-"):
            return _json(401, {"error": {"message": "missing token"}})
        principal = auth[len("Bearer tok-"):]

        if path.startswith("/polaris/api/management/v1/"):
            if principal != ADMIN.client_id:
                return _json(403, {"error": {"message": "management requires admin"}})
            return self._management(method, path[len("/polaris/api/management/v1/"):], request)
        if path.startswith("/polaris/api/catalog/v1/"):
            return self._catalog_api(method, path[len("/polaris/api/catalog/v1/"):], request, principal)
        return _json(404, {"error": {"message": f"no route {path}"}})

    # -- management API ---------------------------------------------------

    def _management(self, method: str, rest: str, request: httpx.Request) -> httpx.Response:
        parts = rest.split("/")
        body = json.loads(request.content) if request.content else {}

        if parts[0] == "catalogs":
            if len(parts) == 1 and method == "POST":
                cat = body["catalog"]
                self.secret_counter += 1
                storage = dict(cat["storageConfigInfo"])
                storage["userArn"] = f"arn:aws:iam::999999999999:user/polaris-{self.secret_counter}"
                storage["externalId"] = f"ext-{self.secret_counter}"
                self.catalogs[cat["name"]] = {
                    "name": cat["name"],
                    "type": cat["type"],
                    "properties": dict(cat["properties"]),
                    "storageConfigInfo": storage,
                }
                self.tables.setdefault(cat["name"], {})
                return _json(201, {})
            name = parts[1]
            if name not in self.catalogs:
                return _json(404, {"error": {"message": f"catalog {name} not found"}})
            if len(parts) == 2 and method == "GET":
                return _json(200, self.catalogs[name])
            if parts[2] == "catalog-roles":
                if len(parts) == 3 and method == "POST":
                    self.catalog_roles.setdefault((name, body["catalogRole"]["name"]), set())
                    return _json(201, {})
                role = parts[3]
                if (name, role) not in self.catalog_roles:
                    return _json(404, {"error": {"message": f"catalog role {role} not found"}})
                if len(parts) == 4 and method == "GET":
                    return _json(200, {"name": role})
                if parts[4] == "grants":
                    if method == "GET":
                        return _json(200, {"grants": [{"type": "catalog", "privilege": p} for p in sorted(self.catalog_roles[(name, role)])]})
                    if method == "PUT":
                        self.catalog_roles[(name, role)].add(body["grant"]["privilege"])
                        return _json(201, {})

        if parts[0] == "principal-roles":
            if len(parts) == 1 and method == "POST":
                self.principal_roles.setdefault(body["principalRole"]["name"], {})
                return _json(201, {})
            role = parts[1]
            if role not in self.principal_roles:
                return _json(404, {"error": {"message": f"principal role {role} not found"}})
            if len(parts) == 2 and method == "GET":
                return _json(200, {"name": role})
            if parts[2] == "catalog-roles":
                catalog = parts[3]
                if method == "GET":
                    return _json(200, {"roles": [{"name": r} for r in sorted(self.principal_roles[role].get(catalog, set()))]})
                if method == "PUT":
                    self.principal_roles[role].setdefault(catalog, set()).add(body["catalogRole"]["name"])
                    return _json(201, {})

        if parts[0] == "principals":
            if len(parts) == 1 and method == "POST":
                name = body["principal"]["name"]
                self.secret_counter += 1
                secret = f"secret-{self.secret_counter}"
                self.principals[name] = {"secret": secret, "roles": set()}
                return _json(201, {"principal": {"name": name}, "credentials": {"clientId": name, "clientSecret": secret}})
            name = parts[1]
            if name not in self.principals:
                return _json(404, {"error": {"message": f"principal {name} not found"}})
            if len(parts) == 2 and method == "GET":
                return _json(200, {"name": name})
            if len(parts) == 2 and method == "DELETE":
                del self.principals[name]
                return httpx.Response(204)
            if parts[2] == "rotate" and method == "POST":
                self.secret_counter += 1
                secret = f"secret-{self.secret_counter}"
                self.principals[name]["secret"] = secret
                return _json(200, {"principal": {"name": name}, "credentials": {"clientId": name, "clientSecret": secret}})
            if parts[2] == "principal-roles":
                if method == "GET":
                    return _json(200, {"roles": [{"name": r} for r in sorted(self.principals[name]["roles"])]})
                if method == "PUT":
                    self.principals[name]["roles"].add(body["principalRole"]["name"])
                    return _json(201, {})

        return _json(404, {"error": {"message": f"no management route {method} {rest}"}})

    # -- Iceberg REST API -------------------------------------------------

    def _catalog_api(self, method: str, rest: str, request: httpx.Request, principal: str) -> httpx.Response:
        parts = rest.split("/")
        catalog = parts[0]
        if catalog not in self.catalogs:
            return _json(404, {"error": {"message": f"catalog {catalog} not found"}})
        privileges = self._privileges(principal, catalog)
        namespaces = self.tables.get(catalog, {})

        if parts[1] == "namespaces" and len(parts) == 2 and method == "GET":
            if not privileges & {"NAMESPACE_LIST", "CATALOG_MANAGE_CONTENT"}:
                return _json(403, {"error": {"message": "principal lacks NAMESPACE_LIST"}})
            parent = request.url.params.get("parent")
            parent_tuple = tuple(parent.split(SEPARATOR)) if parent else ()
            listed = [list(ns) for ns in namespaces if ns[:-1] == parent_tuple]
            return _json(200, {"namespaces": listed})

        if parts[1] == "namespaces" and len(parts) == 4 and parts[3] == "tables" and method == "GET":
            if not privileges & {"TABLE_LIST", "CATALOG_MANAGE_CONTENT"}:
                return _json(403, {"error": {"message": "principal lacks TABLE_LIST"}})
            ns = tuple(unquote(parts[2]).split(SEPARATOR))
            if ns not in namespaces:
                return _json(404, {"error": {"message": "namespace not found"}})
            return _json(200, {"identifiers": [{"namespace": list(ns), "name": t} for t in namespaces[ns]]})

        return _json(404, {"error": {"message": f"no catalog route {method} {rest}"}})


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


@pytest.fixture
def fake() -> FakeOpenCatalog:
    return FakeOpenCatalog()


@pytest.fixture
def admin(fake: FakeOpenCatalog) -> OpenCatalogClient:
    with OpenCatalogClient("https://org-acct.snowflakecomputing.com", ADMIN, transport=fake.transport()) as client:
        yield client


@pytest.fixture
def make_client(fake: FakeOpenCatalog):
    def factory(credentials: Credentials) -> OpenCatalogClient:
        return OpenCatalogClient("https://org-acct.snowflakecomputing.com", credentials, transport=fake.transport())

    return factory
