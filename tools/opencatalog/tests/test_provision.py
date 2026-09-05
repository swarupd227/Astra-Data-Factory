import pytest

from astra_opencatalog.client import AccessDenied, OpenCatalogClient
from astra_opencatalog.provision import MANAGE_PRIVILEGES, READ_PRIVILEGES, EnvironmentSpec, ProvisionError, provision
from tests.conftest import FakeOpenCatalog

SPEC = EnvironmentSpec(prefix="astra", environment="dev", base_location="s3://astra-dev-iceberg-1/", role_arn="arn:aws:iam::1:role/ASTRA_DEV_OPEN_CATALOG")


def test_names_follow_the_terraform_naming_rule():
    assert SPEC.catalog == "astra_dev"
    assert SPEC.manage_catalog_role == "astra_dev_manage"
    assert SPEC.read_catalog_role == "astra_dev_read"
    assert SPEC.sync_principal_role == "astra_dev_sync"
    assert SPEC.reader_principal_role == "astra_dev_reader"
    assert SPEC.sync_principal == "astra_dev_snowflake_sync"
    assert SPEC.reader_principal == "astra_dev_reader"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prefix": "Astra"},
        {"environment": "Prod-1"},
        {"environment": "a"},
        {"base_location": "s3://bucket"},
        {"base_location": "gs://bucket/"},
        {"role_arn": "role"},
    ],
)
def test_spec_rejects_bad_input(kwargs):
    base = dict(prefix="astra", environment="dev", base_location="s3://b/", role_arn="arn:aws:iam::1:role/r")
    with pytest.raises(ValueError):
        EnvironmentSpec(**{**base, **kwargs})


def test_first_run_creates_everything(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    result = provision(admin, SPEC)

    assert result.unchanged == []
    assert fake.catalogs["astra_dev"]["properties"]["default-base-location"] == "s3://astra-dev-iceberg-1/"
    assert fake.catalog_roles[("astra_dev", "astra_dev_manage")] == set(MANAGE_PRIVILEGES)
    assert fake.catalog_roles[("astra_dev", "astra_dev_read")] == set(READ_PRIVILEGES)
    assert fake.principal_roles["astra_dev_sync"] == {"astra_dev": {"astra_dev_manage"}}
    assert fake.principal_roles["astra_dev_reader"] == {"astra_dev": {"astra_dev_read"}}
    assert fake.principals["astra_dev_snowflake_sync"]["roles"] == {"astra_dev_sync"}
    assert fake.principals["astra_dev_reader"]["roles"] == {"astra_dev_reader"}

    assert set(result.credentials) == {"astra_dev_snowflake_sync", "astra_dev_reader"}
    assert result.storage.iam_user_arn.startswith("arn:aws:iam::")
    assert result.storage.external_id


def test_second_run_changes_nothing_and_returns_no_credentials(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    provision(admin, SPEC)
    writes_before = len(fake.calls("POST")) + len(fake.calls("PUT"))

    result = provision(admin, SPEC)

    assert result.created == []
    assert result.credentials == {}
    writes_after = len(fake.calls("POST")) + len(fake.calls("PUT"))
    # Only token requests may have been added, and the token is cached.
    assert writes_after == writes_before
    assert len(result.unchanged) == 2 + len(MANAGE_PRIVILEGES) + len(READ_PRIVILEGES) + 1 + 2 * 2 + 2 * 2


def test_partial_state_is_completed(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    admin.create_catalog("astra_dev", base_location="s3://astra-dev-iceberg-1/", role_arn="arn:aws:iam::1:role/ASTRA_DEV_OPEN_CATALOG")
    admin.create_catalog_role("astra_dev", "astra_dev_read")
    admin.add_catalog_grant("astra_dev", "astra_dev_read", "TABLE_LIST")

    result = provision(admin, SPEC)

    assert "catalog astra_dev" in result.unchanged
    assert "catalog role astra_dev_read" in result.unchanged
    assert "grant TABLE_LIST to astra_dev_read" in result.unchanged
    assert "grant TABLE_READ_DATA to astra_dev_read" in result.created
    assert fake.catalog_roles[("astra_dev", "astra_dev_read")] == set(READ_PRIVILEGES)


def test_existing_catalog_with_different_storage_is_refused(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    admin.create_catalog("astra_dev", base_location="s3://someone-elses-bucket/", role_arn="arn:aws:iam::1:role/ASTRA_DEV_OPEN_CATALOG")
    with pytest.raises(ProvisionError) as excinfo:
        provision(admin, SPEC)
    assert "default-base-location" in str(excinfo.value)
    assert fake.calls("POST", "catalog-roles") == []


def test_rotate_returns_new_credentials_for_existing_principals(fake: FakeOpenCatalog, admin: OpenCatalogClient):
    first = provision(admin, SPEC).credentials
    rotated = provision(admin, SPEC, rotate_credentials=True).credentials
    assert set(rotated) == set(first)
    for name in first:
        assert rotated[name].client_secret != first[name].client_secret
        assert fake.principals[name]["secret"] == rotated[name].client_secret


def test_reader_can_read_and_cannot_manage(fake: FakeOpenCatalog, admin: OpenCatalogClient, make_client):
    result = provision(admin, SPEC)
    fake.add_table("astra_dev", ("ASTRA_DEV", "GOLD"), "POSITION")
    reader = make_client(result.credentials["astra_dev_reader"])

    assert reader.list_tables("astra_dev", ["ASTRA_DEV", "GOLD"]) == ["POSITION"]
    with pytest.raises(AccessDenied):
        reader.create_principal("escalation")


def test_principal_without_roles_is_denied(fake: FakeOpenCatalog, admin: OpenCatalogClient, make_client):
    provision(admin, SPEC)
    stranger = make_client(admin.create_principal("stranger"))
    with pytest.raises(AccessDenied) as excinfo:
        stranger.list_namespaces("astra_dev")
    assert excinfo.value.status_code == 403
