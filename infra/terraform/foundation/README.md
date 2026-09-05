# Platform foundation

Terraform configuration for everything an Astra Data Factory environment runs on:

- **S1.1.1** Snowflake database, schemas, six functional roles, one warehouse per processing tier, and the grants that connect them.
- **S1.1.2** An S3 bucket for Iceberg data, the IAM roles Snowflake and Open Catalog assume to reach it, a Snowflake external volume, Iceberg-by-default settings on the database, and the catalog integration that syncs table metadata to Snowflake Open Catalog so pg_lake, Spark and DuckDB read the same files.

Every environment (dev, qa, uat, prod) is created from this same configuration. Only `environments/<env>.tfvars` and `environments/backend-<env>.hcl` differ.

## What gets created

Object names are `<PREFIX>_<ENV>_...`; the default prefix is `ASTRA`. For `dev`:

| Object | Name | Notes |
|---|---|---|
| Database | `ASTRA_DEV` | Time Travel retention from `data_retention_days` |
| Schema | `ASTRA_DEV.BRONZE` | Raw lines as landed. Managed access. |
| Schema | `ASTRA_DEV.SILVER` | Canonical data model after merge and resolution |
| Schema | `ASTRA_DEV.GOLD` | Consumer read models and semantic views |
| Schema | `ASTRA_DEV.EXCEPTIONS` | Exception store |
| Schema | `ASTRA_DEV.CONTROL` | Run status, file tracking, watermarks |
| Warehouse | `ASTRA_DEV_WH_SIMPLE` | Size from `warehouse_tiers.simple.size` |
| Warehouse | `ASTRA_DEV_WH_MEDIUM` | Size from `warehouse_tiers.medium.size` |
| Warehouse | `ASTRA_DEV_WH_COMPLEX` | Size from `warehouse_tiers.complex.size` |
| Role | `ASTRA_DEV_ADMIN` | Owns the environment; inherits every role below; granted to `SYSADMIN` |
| Role | `ASTRA_DEV_ENGINEER` | Deploys generated code; creates and writes every object |
| Role | `ASTRA_DEV_PIPELINE` | Service role for Snowpipe, Dynamic Tables and Tasks |
| Role | `ASTRA_DEV_STEWARD` | Reads Silver, Gold, Control; writes the exception store |
| Role | `ASTRA_DEV_CONSUMER` | Reads Gold and Control only (UMP via pg_lake) |
| Role | `ASTRA_DEV_AUDITOR` | Reads everything, changes nothing |
| Resource monitor | `ASTRA_DEV_MONITOR` | Only when `monthly_credit_quota` is set |
| S3 bucket | `astra-dev-iceberg-<aws account id>` | Iceberg data and metadata. Versioned, SSE-S3, all public access blocked, TLS only. |
| IAM role | `ASTRA_DEV_SNOWFLAKE_ICEBERG` | Assumed by Snowflake; read and write on the bucket. Trust policy built from the external volume's reported IAM user and external id. |
| IAM role | `ASTRA_DEV_OPEN_CATALOG` | Assumed by Open Catalog; read only. Created once `open_catalog.iam_user_arn` and `external_id` are set. |
| External volume | `ASTRA_DEV_ICEBERG` | Points at the bucket through the Snowflake role. `USAGE` for ADMIN, ENGINEER, PIPELINE. |
| Catalog integration | `ASTRA_DEV_OPEN_CATALOG` | Only when `open_catalog` is set. The database's `CATALOG_SYNC` parameter points at it. |

The database is configured so that every table created in it is a Snowflake-managed Iceberg table on the external volume (`EXTERNAL_VOLUME`, `CATALOG = 'SNOWFLAKE'`) with `STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'`, which keeps the Parquet files readable by external engines.

All warehouses are created suspended, auto-resume, auto-suspend after 60 seconds by default, and run with a statement timeout so a runaway query cannot hold a warehouse open.

## Access matrix

Access is data, not code. Each schema in `var.schemas` lists its `readers`, `writers` and `creators`; each warehouse tier lists its `users`. The defaults:

| Schema | Creators | Writers | Readers |
|---|---|---|---|
| BRONZE | ENGINEER | PIPELINE | AUDITOR |
| SILVER | ENGINEER | PIPELINE | STEWARD, AUDITOR |
| GOLD | ENGINEER | PIPELINE | STEWARD, CONSUMER, AUDITOR |
| EXCEPTIONS | ENGINEER | PIPELINE, STEWARD | AUDITOR |
| CONTROL | ENGINEER | PIPELINE | STEWARD, CONSUMER, AUDITOR |

Creators are also writers; writers are also readers. Readers get `SELECT` on future tables, Iceberg tables, views, dynamic tables, materialized views and streams. Writers add `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` on tables, `OPERATE` on dynamic tables, tasks and pipes, and `USAGE` on stages, file formats, functions, procedures, sequences and data metric functions. Creators get every `CREATE <object>` privilege the generation plane needs.

Grants are on **future** objects only. The foundation creates no tables, so a grant on existing objects would have nothing to attach to and would drift as soon as the generation plane deploys. Future grants cover every object created later in these schemas.

| Warehouse | USAGE | OPERATE | MONITOR | MODIFY |
|---|---|---|---|---|
| simple | ENGINEER, PIPELINE, STEWARD, CONSUMER, AUDITOR | PIPELINE, ADMIN | AUDITOR, ADMIN | ADMIN |
| medium, complex | ENGINEER, PIPELINE | PIPELINE, ADMIN | AUDITOR, ADMIN | ADMIN |

ENGINEER and PIPELINE also hold the account-level `EXECUTE TASK` and `EXECUTE MANAGED TASK` privileges.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `environment` | required | `dev`, `qa`, `uat` or `prod`. Part of every object name. |
| `prefix` | `ASTRA` | Object name prefix |
| `terraform_role` | `SYSADMIN` | Role Terraform acts as |
| `parent_role` | `SYSADMIN` | Role the environment ADMIN is granted to |
| `data_retention_days` | `1` | Time Travel retention for database and schemas |
| `schemas` | five schemas above | Schemas and their access lists |
| `warehouse_tiers` | XSMALL / SMALL / MEDIUM | Size, auto-suspend, cluster count, statement timeout and users per tier. `simple`, `medium` and `complex` are required; more tiers may be added. |
| `monthly_credit_quota` | `null` | Creates a resource monitor and attaches it to every warehouse |
| `aws_region` | `us-east-1` | Region of the Iceberg bucket; should match the Snowflake account's region |
| `iceberg_bucket_name` | derived | `<prefix>-<env>-iceberg-<aws account id>` unless set |
| `open_catalog` | `null` | `{ account_url, catalog_name, iam_user_arn?, external_id? }`. Enables the catalog integration and `CATALOG_SYNC`; the two optional fields, reported by `tools/opencatalog provision`, enable the Open Catalog IAM role. |
| `open_catalog_client_id` | `null` | OAuth client id of the sync service connection. Required with `open_catalog`. |
| `open_catalog_client_secret` | `null` | Its secret, sensitive. Supply via `TF_VAR_open_catalog_client_secret`. |

Every variable is validated. A wrong environment name, an unknown warehouse size, a missing tier or an unknown role in an access list fails before any plan is made.

## Prerequisites

Terraform 1.10 or later and the `snowflakedb/snowflake` provider 2.x (pinned in `.terraform.lock.hcl`).

One thing has to exist before Terraform can run: a service user with a key pair and a role that can create databases, warehouses and roles. This is the credential Terraform runs *as*, so it cannot be created by Terraform itself. Create it once per account:

```sql
USE ROLE ACCOUNTADMIN;
CREATE USER TERRAFORM_SVC TYPE = SERVICE RSA_PUBLIC_KEY = '<public key body>';
GRANT ROLE SYSADMIN TO USER TERRAFORM_SVC;
GRANT ROLE SECURITYADMIN TO ROLE SYSADMIN;
```

`SECURITYADMIN` under `SYSADMIN` lets one role create both objects and roles. If your account policy forbids that, run with `terraform_role = "ACCOUNTADMIN"` for the bootstrap and switch back afterwards. The optional resource monitor always needs `ACCOUNTADMIN`.

Connection settings come from the environment; nothing is read from files:

```bash
export SNOWFLAKE_ORGANIZATION_NAME=...
export SNOWFLAKE_ACCOUNT_NAME=...
export SNOWFLAKE_USER=TERRAFORM_SVC
export SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT
export SNOWFLAKE_PRIVATE_KEY="$(cat /path/outside/repo/terraform_svc.p8)"
```

AWS credentials come from the usual provider chain (environment variables, a profile, or OIDC in CI). The identity needs to create S3 buckets and IAM roles in the client account.

State is stored in S3 in the client's account with native lock files. Set the bucket in `environments/backend-<env>.hcl`.

## Enabling Open Catalog

Open Catalog is optional until its account exists. The sequence is Terraform, then `tools/opencatalog provision`, then Terraform again, because the IAM trust policy for Open Catalog needs the identity the catalog reports at creation:

1. `make apply ENV=dev`. Note the outputs `iceberg_base_url` and `open_catalog_role_arn`.
2. `opencatalog provision --environment dev --base-location <iceberg_base_url> --role-arn <open_catalog_role_arn>`. It prints `iam_user_arn`, `external_id` and, on first run, the credentials of the sync and reader principals.
3. Set `open_catalog` (all four fields) and `open_catalog_client_id` in `environments/dev.tfvars`, export `TF_VAR_open_catalog_client_secret`, and `make apply ENV=dev` again. This creates the Open Catalog IAM role, the catalog integration and sets `CATALOG_SYNC` on the database.

From then on every Iceberg table in the database appears in the Open Catalog catalog and can be read by any engine that speaks Iceberg REST.

## Commands

```bash
make check            # fmt, validate and unit tests; no account needed
make plan ENV=dev
make apply ENV=dev
make verify ENV=dev   # apply, then prove a second plan is empty
```

## How the acceptance criteria are verified

| Criterion | Verified by |
|---|---|
| `terraform apply` on an empty account creates all objects with no manual step | `make verify ENV=<env>` runs apply from scratch. The only prerequisite is the service credential above, which is what Terraform authenticates with. |
| Warehouse sizes per tier are variables | `warehouse_tiers` variable; unit test `warehouse_sizes_come_from_tier_variables`. |
| Re-running apply on an unchanged config produces no diff | `make verify` runs a second plan with `-detailed-exitcode` and fails on exit code 2. |

For S1.1.2:

| Criterion | Verified by |
|---|---|
| A table created in Snowflake is listed by the Iceberg REST catalog within one minute | `opencatalog verify` creates a probe table and times the catalog listing |
| An external Spark or DuckDB client can read the table via the catalog | `opencatalog verify` reads the probe table with DuckDB through the reader principal |
| Access is denied for roles without the catalog grant | `opencatalog verify` confirms a principal with no roles gets 403. On the Snowflake side, unit test `table_creating_roles_can_use_the_volume` proves read-only roles hold no privilege on the volume. |

Unit tests in `tests/*.tftest.hcl` run against mocked providers and cover object names, sizes, the access matrix, cost control, the bucket's security settings, the volume, the Open Catalog wiring and every validation rule. They run in CI on every change and need no credentials.

## Design notes

- **One root module, one tfvars per environment.** No workspaces, no per-environment copies of the code. Environment parity is structural (S1.2.1).
- **Roles are functional, not personal.** People and service users are granted the six roles; nothing is granted to a user here.
- **Schemas use managed access**, so only the schema owner can grant privileges on objects inside them. That keeps the access matrix in this file the single source of truth.
- **Stages get `USAGE` only** on future grants. External stages (S3 landing zones for Snowpipe) use `USAGE`; internal stages would need `READ`/`WRITE`, which Snowflake rejects on external stages. Add an explicit grant if a pipeline needs an internal stage.
- **Sandboxes are not created here.** Ephemeral schemas and warehouses for dry-runs (S1.2.3) are created and destroyed at run time by the sandbox runner, using the ADMIN role's `CREATE SCHEMA` privilege.
- **The Snowflake IAM role ARN is composed, not read back.** Snowflake needs the role ARN to create the external volume, and the role's trust policy needs the IAM user and external id Snowflake reports once the volume exists. Composing the ARN from the account id and a fixed name breaks the cycle in one apply.
- **Snowflake is the only writer.** The Open Catalog role is read only, and CATALOG_SYNC pushes metadata one way. External engines read; they do not write through the catalog.
- **`CATALOG_SYNC` is set with `snowflake_execute`** because the provider does not expose the parameter on databases yet. The resource carries a matching `UNSET` so destroy is clean.
