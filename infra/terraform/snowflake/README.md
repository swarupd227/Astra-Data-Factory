# Snowflake foundation

Terraform configuration for the Snowflake objects every Astra Data Factory environment runs on: one database, its schemas, six functional roles, one warehouse per processing tier, and the grants that connect them. Implements story S1.1.1 (backlog F1.1, WBS 2.1.1).

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

State is stored in S3 in the client's account with native lock files. Set the bucket in `environments/backend-<env>.hcl`.

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

Unit tests in `tests/foundation.tftest.hcl` run against a mocked provider and cover object names, sizes, the access matrix, cost control and every validation rule. They run in CI on every change and need no credentials.

## Design notes

- **One root module, one tfvars per environment.** No workspaces, no per-environment copies of the code. Environment parity is structural (S1.2.1).
- **Roles are functional, not personal.** People and service users are granted the six roles; nothing is granted to a user here.
- **Schemas use managed access**, so only the schema owner can grant privileges on objects inside them. That keeps the access matrix in this file the single source of truth.
- **Stages get `USAGE` only** on future grants. External stages (S3 landing zones for Snowpipe) use `USAGE`; internal stages would need `READ`/`WRITE`, which Snowflake rejects on external stages. Add an explicit grant if a pipeline needs an internal stage.
- **Sandboxes are not created here.** Ephemeral schemas and warehouses for dry-runs (S1.2.3) are created and destroyed at run time by the sandbox runner, using the ADMIN role's `CREATE SCHEMA` privilege.
