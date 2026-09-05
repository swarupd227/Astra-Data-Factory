import pytest

from astra_data.snowflake_connection import ConnectionConfigError, connection_parameters


def test_account_from_organisation_and_account_name_like_the_terraform_provider():
    params = connection_parameters({
        "SNOWFLAKE_ORGANIZATION_NAME": "ARTIZENT",
        "SNOWFLAKE_ACCOUNT_NAME": "ASTRA_DEV",
        "SNOWFLAKE_USER": "TERRAFORM_SVC",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/keys/svc.p8",
        "SNOWFLAKE_ROLE": "ASTRA_DEV_ENGINEER",
        "SNOWFLAKE_WAREHOUSE": "ASTRA_DEV_WH_SIMPLE",
    })
    assert params == {
        "account": "ARTIZENT-ASTRA_DEV",
        "user": "TERRAFORM_SVC",
        "private_key_file": "/keys/svc.p8",
        "role": "ASTRA_DEV_ENGINEER",
        "warehouse": "ASTRA_DEV_WH_SIMPLE",
    }


def test_explicit_account_wins_and_passphrase_goes_with_the_key_file():
    params = connection_parameters({
        "SNOWFLAKE_ACCOUNT": "org-acct",
        "SNOWFLAKE_USER": "u",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "k.p8",
        "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "pw",
    })
    assert params["account"] == "org-acct" and params["private_key_file_pwd"] == "pw"


@pytest.mark.parametrize(
    "environ,missing",
    [
        ({"SNOWFLAKE_USER": "u", "SNOWFLAKE_PRIVATE_KEY_PATH": "k"}, "SNOWFLAKE_ACCOUNT"),
        ({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_PRIVATE_KEY_PATH": "k"}, "SNOWFLAKE_USER"),
        ({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u"}, "SNOWFLAKE_PRIVATE_KEY"),
    ],
)
def test_missing_settings_are_named(environ, missing):
    with pytest.raises(ConnectionConfigError) as excinfo:
        connection_parameters(environ)
    assert missing in str(excinfo.value)
