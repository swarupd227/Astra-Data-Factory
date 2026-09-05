import httpx
import pytest

from astra_opencatalog.client import AccessDenied, Credentials, NotFound, OpenCatalogClient, OpenCatalogError, encode_namespace
from tests.conftest import ADMIN, FakeOpenCatalog


def test_fetches_a_token_once_and_reuses_it(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    admin.get_catalog("missing")
    admin.get_catalog("missing")
    assert len(fake.calls("POST", "oauth/tokens")) == 1


def test_refreshes_the_token_before_it_expires(fake: FakeOpenCatalog):
    now = [0.0]
    client = OpenCatalogClient("https://x.snowflakecomputing.com", ADMIN, transport=fake.transport(), clock=lambda: now[0])
    client.get_catalog("missing")
    now[0] = 3600.0  # past expires_in minus the safety margin
    client.get_catalog("missing")
    assert len(fake.calls("POST", "oauth/tokens")) == 2


def test_bad_credentials_raise_access_denied(fake: FakeOpenCatalog):
    client = OpenCatalogClient("https://x.snowflakecomputing.com", Credentials("nobody", "wrong"), transport=fake.transport())
    with pytest.raises(AccessDenied) as excinfo:
        client.get_catalog("anything")
    assert excinfo.value.status_code == 401


def test_get_catalog_returns_none_when_absent_and_dict_when_present(admin: OpenCatalogClient):
    assert admin.get_catalog("astra_dev") is None
    created = admin.create_catalog("astra_dev", base_location="s3://bucket/", role_arn="arn:aws:iam::1:role/r")
    assert created["properties"]["default-base-location"] == "s3://bucket/"
    assert created["storageConfigInfo"]["roleArn"] == "arn:aws:iam::1:role/r"
    assert created["storageConfigInfo"]["userArn"].startswith("arn:aws:iam::")
    assert admin.get_catalog("astra_dev") == created


def test_create_catalog_sends_internal_s3_catalog(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    admin.create_catalog("astra_dev", base_location="s3://bucket/", role_arn="arn:aws:iam::1:role/r")
    stored = fake.catalogs["astra_dev"]
    assert stored["type"] == "INTERNAL"
    assert stored["storageConfigInfo"]["storageType"] == "S3"
    assert stored["storageConfigInfo"]["allowedLocations"] == ["s3://bucket/"]


def test_unexpected_status_raises_with_detail(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    with pytest.raises(NotFound) as excinfo:
        admin.grants_of("astra_dev", "nope")
    assert "catalog astra_dev not found" in str(excinfo.value)
    assert isinstance(excinfo.value, OpenCatalogError)


def test_principal_lifecycle(admin: OpenCatalogClient):
    creds = admin.create_principal("svc")
    assert creds.client_id == "svc" and creds.client_secret
    assert admin.get_principal("svc") == {"name": "svc"}
    rotated = admin.rotate_credentials("svc")
    assert rotated.client_secret != creds.client_secret
    admin.delete_principal("svc")
    assert admin.get_principal("svc") is None


def test_namespace_encoding_uses_unit_separator():
    assert encode_namespace(["ASTRA_DEV", "SILVER"]) == "ASTRA_DEV\x1fSILVER"


def test_lists_nested_namespaces_and_tables(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    admin.create_catalog("astra_dev", base_location="s3://bucket/", role_arn="arn:aws:iam::1:role/r")
    fake.add_table("astra_dev", ("ASTRA_DEV", "SILVER"), "POSITION")
    fake.add_table("astra_dev", ("ASTRA_DEV", "GOLD"), "POSITION_BY_FIRM")

    assert admin.list_namespaces("astra_dev") == [["ASTRA_DEV"]]
    assert admin.list_namespaces("astra_dev", ["ASTRA_DEV"]) == [["ASTRA_DEV", "SILVER"], ["ASTRA_DEV", "GOLD"]]
    assert admin.list_all_namespaces("astra_dev") == [["ASTRA_DEV"], ["ASTRA_DEV", "SILVER"], ["ASTRA_DEV", "GOLD"]]
    assert admin.list_tables("astra_dev", ["ASTRA_DEV", "SILVER"]) == ["POSITION"]

    (_, path), = fake.calls("GET", "/tables$")
    assert path.endswith("/namespaces/ASTRA_DEV%1FSILVER/tables")


def test_urls_are_derived_from_the_account_url():
    client = OpenCatalogClient("https://org-acct.snowflakecomputing.com/", ADMIN, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert client.catalog_api_url == "https://org-acct.snowflakecomputing.com/polaris/api/catalog"
    assert client.token_url == "https://org-acct.snowflakecomputing.com/polaris/api/catalog/v1/oauth/tokens"
