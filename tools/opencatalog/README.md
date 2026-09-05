# opencatalog

Provisions and verifies the Snowflake Open Catalog side of an Astra Data Factory environment. Part of story S1.1.2 (backlog F1.1, WBS 2.1.2).

Open Catalog has no Terraform provider, so this tool manages it through the Polaris management REST API. It is idempotent: run it as often as you like, it only creates what is missing and reports the rest as unchanged.

## What it creates per environment

For environment `dev` with the default prefix `astra`:

| Object | Name | Purpose |
|---|---|---|
| Catalog | `astra_dev` | Internal catalog over the environment's Iceberg bucket. Snowflake syncs table metadata into it. |
| Catalog role | `astra_dev_manage` | `CATALOG_MANAGE_CONTENT`. Held by the sync principal. |
| Catalog role | `astra_dev_read` | List and read namespaces, tables and views. Nothing that writes. |
| Principal role | `astra_dev_sync` | Carries `astra_dev_manage` |
| Principal role | `astra_dev_reader` | Carries `astra_dev_read` |
| Principal | `astra_dev_snowflake_sync` | Service connection Snowflake authenticates with (Terraform `open_catalog_client_id`) |
| Principal | `astra_dev_reader` | Service connection for pg_lake, Spark and DuckDB |

## Prerequisites

- An Open Catalog account, created once per organisation from Snowsight (Admin, Accounts, Create Open Catalog account). This cannot be scripted.
- One service connection in that account with the Open Catalog admin principal role, for this tool to authenticate as. Create it once in the Open Catalog UI and store its credentials in the secret manager.
- Python 3.11 or later.

```bash
cd tools/opencatalog
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # add ,verify to run the live checks
```

## Provisioning an environment

Terraform first, then this tool, then Terraform again. The second Terraform run creates the IAM role Open Catalog assumes, because the trust policy needs the IAM user and external id that Open Catalog reports when the catalog is created.

```bash
export OPEN_CATALOG_URL=https://<org>-<open-catalog-account>.snowflakecomputing.com
export OPEN_CATALOG_CLIENT_ID=...        # admin service connection
export OPEN_CATALOG_CLIENT_SECRET=...

# 1. In infra/terraform/foundation: terraform apply, then read the outputs
#    iceberg_base_url and open_catalog_role_arn.

# 2. Create the catalog, roles and principals.
opencatalog provision --environment dev \
  --base-location s3://astra-dev-iceberg-123456789012/ \
  --role-arn arn:aws:iam::123456789012:role/ASTRA_DEV_OPEN_CATALOG
```

The output lists what was created, the `iam_user_arn` and `external_id` to put under `open_catalog` in `environments/dev.tfvars`, and, on first creation only, the credentials of the two principals. Store them in the secret manager immediately; Open Catalog does not show a secret twice. Use `--rotate-credentials` to issue new ones.

```bash
# 3. Set open_catalog and open_catalog_client_id in dev.tfvars, export
#    TF_VAR_open_catalog_client_secret, and apply again. This creates the
#    Open Catalog IAM role, the catalog integration and CATALOG_SYNC.
```

## Verifying the acceptance criteria

```bash
export OPEN_CATALOG_READER_CLIENT_ID=...    # astra_dev_reader
export OPEN_CATALOG_READER_CLIENT_SECRET=...
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=...
export SNOWFLAKE_ROLE=ASTRA_DEV_ENGINEER SNOWFLAKE_WAREHOUSE=ASTRA_DEV_WH_SIMPLE

opencatalog verify --environment dev
```

`verify` creates a one-row Iceberg table in `ASTRA_DEV.SILVER`, then runs the three checks from the story and drops the table whatever happens:

| Check | How |
|---|---|
| Table listed by the Iceberg REST catalog within one minute | Polls the catalog's namespaces every two seconds and records the elapsed time |
| An external engine reads the table via the catalog | DuckDB attaches the catalog with the reader principal and counts the rows |
| Access is denied without the catalog grant | Creates a principal with no roles, confirms a 403 on listing, deletes it |

Exit code is 0 only when all three pass. `--json` prints the results as data for the evidence pack.

## Tests

```bash
pytest
```

The tests run against an in-memory Open Catalog (`tests/conftest.py`) that implements the routes the tool uses, including the privilege check on the read side. They need no account and finish in under a second.

## Notes

- Management calls need the admin service connection; the environment principals cannot manage the catalog, and the test suite proves the reader cannot create principals.
- The tool never changes the storage of an existing catalog. If a catalog exists with a different bucket or role, it stops and says so.
- DuckDB's Iceberg REST support is used as the reference external engine because it needs no cluster. The same catalog serves pg_lake (S7.2.2) and Spark.
