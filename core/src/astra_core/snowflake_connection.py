"""Snowflake connection from the environment, the same way the Terraform provider reads it.

  SNOWFLAKE_ORGANIZATION_NAME + SNOWFLAKE_ACCOUNT_NAME   or   SNOWFLAKE_ACCOUNT (org-account)
  SNOWFLAKE_USER
  SNOWFLAKE_PRIVATE_KEY (PEM text)   or   SNOWFLAKE_PRIVATE_KEY_PATH
  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE   optional
  SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE   optional

Nothing here is ever printed or logged.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


class ConnectionConfigError(ValueError):
    pass


def connection_parameters(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Keyword arguments for snowflake.connector.connect, from the environment."""
    env = os.environ if environ is None else environ

    account = env.get("SNOWFLAKE_ACCOUNT")
    if not account:
        org, name = env.get("SNOWFLAKE_ORGANIZATION_NAME"), env.get("SNOWFLAKE_ACCOUNT_NAME")
        if org and name:
            account = f"{org}-{name}"
    if not account:
        raise ConnectionConfigError("set SNOWFLAKE_ACCOUNT (org-account) or SNOWFLAKE_ORGANIZATION_NAME and SNOWFLAKE_ACCOUNT_NAME")

    user = env.get("SNOWFLAKE_USER")
    if not user:
        raise ConnectionConfigError("SNOWFLAKE_USER is not set")

    params: dict[str, Any] = {"account": account, "user": user}

    key_path, key_pem = env.get("SNOWFLAKE_PRIVATE_KEY_PATH"), env.get("SNOWFLAKE_PRIVATE_KEY")
    passphrase = env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    if key_path:
        params["private_key_file"] = key_path
        if passphrase:
            params["private_key_file_pwd"] = passphrase
    elif key_pem:
        params["private_key"] = _pem_to_der(key_pem, passphrase)
    else:
        raise ConnectionConfigError("set SNOWFLAKE_PRIVATE_KEY (PEM text) or SNOWFLAKE_PRIVATE_KEY_PATH")

    for name, key in (("SNOWFLAKE_ROLE", "role"), ("SNOWFLAKE_WAREHOUSE", "warehouse")):
        if env.get(name):
            params[key] = env[name]
    return params


def _pem_to_der(pem: str, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization  # dependency of the connector

    key = serialization.load_pem_private_key(pem.encode(), password=passphrase.encode() if passphrase else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(environ: Mapping[str, str] | None = None):
    params = connection_parameters(environ)  # fail on configuration before touching the driver

    import snowflake.connector  # only deploy and test need it

    return snowflake.connector.connect(**params)


class SnowflakeExecutor:
    """Executor over a snowflake-connector connection."""

    def __init__(self, connection) -> None:
        self._con = connection

    def execute_script(self, sql: str) -> None:
        # execute_string runs every statement in the text, in order, and
        # returns the cursors; consuming them surfaces any error.
        for cursor in self._con.execute_string(sql):
            cursor.close()

    def query(self, sql: str) -> list[tuple]:
        cursor = self._con.cursor()
        try:
            cursor.execute(sql)
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "SnowflakeExecutor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
