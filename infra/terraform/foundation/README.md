# Platform foundation

Terraform configuration for everything an Astra Data Factory environment runs on:

- **S1.1.1** Snowflake database, schemas, six functional roles, one warehouse per processing tier, and the grants that connect them.
- **S1.1.2** An S3 bucket for Iceberg data, the IAM roles Snowflake and Open Catalog assume to reach it, a Snowflake external volume, Iceberg-by-default settings on the database, and the catalog integration that syncs table metadata to Snowflake Open Catalog so pg_lake, Spark and DuckDB read the same files.
- **S1.1.3** A landing bucket, the storage integration and read-only role Snowpipe uses, an external stage with a directory table, the Bronze raw-lines table, an auto-ingest pipe fed by S3 events, and a file load log that records every arrival, including duplicates Snowpipe silently ignores.
- **S1.2.3** What ephemeral sandboxes need from the environment: the SANDBOX role, cost tags, a sandbox log, and a reaper task that drops expired sandboxes. The runner itself is `verification/astra_verification`.

Every environment (dev, qa, uat, prod) is created from this same configuration. Only `environments/<env>.tfvars` and `environments/backend-<env>.hcl` differ.

## Environments (S1.2.1)

An environment is a name plus two files. Nothing in a `.tf` file may branch on the environment; whatever must differ is a variable with a value in the tfvars file. The rule is enforced, not assumed:

```bash
sh scripts/check-env-parity.sh     # fails on environment-conditional code or mismatched files;
                                   # prints the diff of every tfvars file against dev
make check                         # also plans every tfvars file against the standard inventory
```

To add an environment (for example `perf`):

```bash
sh scripts/new-environment.sh perf qa   # scaffolds perf.tfvars and backend-perf.hcl from qa
```

Then follow [docs/runbooks/new-environment.md](../../../docs/runbooks/new-environment.md), which budgets the whole path at under two hours. The state bucket every environment needs is created once per AWS account by [infra/terraform/bootstrap](../bootstrap/README.md).

Environment names are two to eight lower-case letters or digits. The standard four are dev, qa, uat and prod.

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
| Schema | `ASTRA_DEV.REFERENCE` | Replicated reference data (security master, account cross-reference) with change logs; stage and CSV file format for their snapshots |
| Schema | `ASTRA_DEV.ARCHIVE` | Archive store: the legacy SQL Server schema converted and loaded by SnowConvert AI (`astra-data migrate`) |
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
| S3 bucket | `astra-dev-golden-<aws account id>` | Golden datasets (S4.1.2): the legacy path's outputs per business day. Same hardening; S3 Object Lock with a default retention (`golden_retention_days`, `golden_retention_mode`) keeps every version read-only. |
| S3 bucket | `astra-dev-landing-<aws account id>` | Custodian deliveries under `landing/`. Same hardening; superseded versions kept 30 days. |
| IAM role | `ASTRA_DEV_SNOWFLAKE_LANDING` | Assumed by Snowflake; read only, limited to the landing prefix. |
| Storage integration | `ASTRA_DEV_LANDING` | Delegates landing-zone access to the role above. `USAGE` for ADMIN, ENGINEER, PIPELINE. |
| File format | `ASTRA_DEV.BRONZE.RAW_LINES` | One column per line; no delimiter, quoting, escaping or NULL substitution. |
| Stage | `ASTRA_DEV.BRONZE.LANDING` | External stage over the landing prefix with an auto-refreshing directory table. |
| Iceberg table | `ASTRA_DEV.BRONZE.RAW_LINES` | `FILE_NAME`, `ROW_NUMBER`, `LINE`, `FILE_CONTENT_KEY`, `FILE_LAST_MODIFIED`, `INGESTED_AT`. Written only by Snowpipe. |
| Pipe | `ASTRA_DEV.BRONZE.RAW_LINES` | Catch-all auto-ingest pipe (`landing_catch_all_pipe`, on by default). Its SQS queue is the target of the landing bucket's event notification; per-source pipes rendered by `astra-data render` share it. |
| Iceberg table | `ASTRA_DEV.CONTROL.FILE_LOAD_LOG` | One entry per file arrival: `LOADED`, `FAILED`, `DUPLICATE` or `CONFLICT`, with the reason. |
| Task | `ASTRA_DEV.CONTROL.RECONCILE_FILE_LOADS` | Serverless, every minute by default. Writes the file load log. |
| Role | `ASTRA_DEV_SANDBOX` | Sandbox runner: creates and drops `ASTRA_DEV_SBX_<TASK>` databases and warehouses; reads Control; nothing else. |
| Tags | `ASTRA_DEV.CONTROL.TASK_ID`, `ASTRA_DEV.CONTROL.PURPOSE` | Applied to every sandbox database and warehouse for cost per task. |
| Iceberg table | `ASTRA_DEV.CONTROL.SANDBOX_LOG` | Every sandbox created and destroyed, with the reason. |
| Procedure | `ASTRA_DEV.CONTROL.REAP_SANDBOXES` | Drops sandboxes past their expiry or older than the hard maximum; logs each drop. |
| Task | `ASTRA_DEV.CONTROL.REAP_SANDBOXES` | Serverless, every 10 minutes by default. Calls the reaper. |
| Iceberg tables | `ASTRA_DEV.CONTROL.ALERTS`, `ALERT_DELIVERIES`, `ALERT_ROUTES` | Every alert raised, every delivery attempt, and which severity goes to which channel. |
| Iceberg tables | `ASTRA_DEV.CONTROL.CUSTODIANS`, `CUSTODIAN_FILES` | Cutoff, timezone, business days, expected files and alert severities per custodian, synced from configs. |
| Procedures | `ASTRA_DEV.CONTROL.DETECT_TASK_FAILURES`, `DETECT_LATE_CUSTODIANS`, `DISPATCH_ALERTS`, `RUN_ALERTING` | Detection and dispatch. |
| Task | `ASTRA_DEV.CONTROL.RAISE_ALERTS` | Serverless, every minute by default. Calls `RUN_ALERTING`. |
| Iceberg table | `ASTRA_DEV.CONTROL.CUSTODIAN_RUNS` | Every start of a custodian's Tasks DAG: business date, reason (complete, late_arrival, redelivery), files seen. |
| Procedure | `ASTRA_DEV.CONTROL.CUSTODIAN_GATE` | Root of each custodian's rendered DAG: answers 'run' when a business date's expected file set is complete and a file arrived since its last run. |
| Integrations | `ASTRA_DEV_ALERT_EMAIL`, `ASTRA_DEV_ALERT_SLACK`, `ASTRA_DEV_ALERT_JIRA` | Each only when its channel is configured. Webhook secrets are Snowflake secrets in CONTROL. |
| Tag | `ASTRA_DEV.CONTROL.PII` | Marks a column as PII; the value is the category. Three masking policies are bound to it. |
| Masking policies | `ASTRA_DEV.CONTROL.PII_STRING`, `PII_NUMBER`, `PII_DATE` | Privileged roles see values; everyone else sees a category-shaped mask. `BRONZE.RAW_LINES.LINE` is tagged from the start. |
| View | `ASTRA_DEV.CONTROL.PII_ACCESS` | Who read which PII column and when, from ACCESS_HISTORY joined to the tag. |
| Iceberg table | `ASTRA_DEV.CONTROL.PII_ACCESS_LOG` | Retained copy of PII column reads, appended hourly by task `RETAIN_PII_ACCESS`. |
| Secrets Manager | `astra/dev/snowflake/private-key`, `open-catalog/client-secret`, `alerts/slack-webhook`, `alerts/jira-token` | Created without values; the deploy role reads them through policy `ASTRA_DEV_READ_SECRETS`. |

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
| CONTROL | ENGINEER | PIPELINE | STEWARD, CONSUMER, AUDITOR, SANDBOX |
| REFERENCE | ENGINEER | PIPELINE | STEWARD, AUDITOR |
| ARCHIVE | ENGINEER | PIPELINE | STEWARD, AUDITOR |

SANDBOX additionally holds `INSERT` on `CONTROL.SANDBOX_LOG`, `APPLY` on the two tags, and the account privileges `CREATE DATABASE` and `CREATE WAREHOUSE` (see `sandbox.tf`).

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
| `landing_bucket_name` | derived | `<prefix>-<env>-landing-<aws account id>` unless set |
| `landing_prefix` | `landing` | Key prefix Snowpipe watches. Custodian folders sit beneath it. |
| `landing_catch_all_pipe` | `true` | Keep the catch-all pipe loading into `RAW_LINES`. Set to `false` once per-source pipes are rendered, so each file is loaded once; the pipe object then watches an empty folder and keeps serving the bucket notification. |
| `landing_unclaimed_minutes` | `10` | A landed file no pipe loaded after this long is logged as `UNCLAIMED` |
| `landing_noncurrent_version_days` | `30` | How long a superseded version of a re-delivered file is kept. Null keeps all. |
| `landing_reconcile_interval_minutes` | `1` | Cadence of the file load log reconciliation task |
| `sandbox_prefix` | `sandbox` | Landing-bucket prefix where sandbox runs stage sample files; must not be inside `landing_prefix` |
| `sandbox_reap_interval_minutes` | `10` | Cadence of the sandbox reaper task |
| `sandbox_max_age_hours` | `24` | Hard limit on any sandbox's life, whatever expiry it declares |
| `alert_email_recipients` | `[]` | Email addresses that receive alerts; empty disables the email channel |
| `slack_webhook_secret` | `null` | Secret path of the Slack incoming webhook, sensitive; null disables Slack. Supply via `TF_VAR_slack_webhook_secret`. |
| `jira` | `null` | `{ site_url, project_key, user_email, issue_type? }`; null disables Jira. Requires `jira_api_token`. |
| `jira_api_token` | `null` | API token of the Jira user, sensitive. Supply via `TF_VAR_jira_api_token`. |
| `alert_interval_minutes` | `1` | Detection and dispatch cadence, at most 5 |
| `alert_lookback_hours` | `2` | How far back task history is scanned each run |
| `alert_delivery_attempts` | `5` | Maximum attempts per alert and channel |
| `pii_unmasked_roles` | `["ADMIN", "ENGINEER", "PIPELINE", "STEWARD"]` | Functional roles that see PII in clear. AUDITOR and CONSUMER cannot be listed. |
| `pii_access_retention_interval_minutes` | `60` | Cadence of the access history retention task |
| `pii_access_catchup_days` | `7` | Look-back window for late-arriving access history rows |

Every variable is validated. A wrong environment name, an unknown warehouse size, a missing tier or an unknown role in an access list fails before any plan is made.

## Prerequisites

Terraform 1.10 or later and the `snowflakedb/snowflake` provider 2.x (pinned in `.terraform.lock.hcl`).

One thing has to exist before Terraform can run: a service user with a key pair and a role that can create databases, warehouses and roles. This is the credential Terraform runs *as*, so it cannot be created by Terraform itself. Create it once per account:

```sql
USE ROLE ACCOUNTADMIN;
CREATE USER TERRAFORM_SVC TYPE = SERVICE RSA_PUBLIC_KEY = '<public key body>';
GRANT ROLE SYSADMIN TO USER TERRAFORM_SVC;
GRANT ROLE SECURITYADMIN TO ROLE SYSADMIN;
GRANT CREATE INTEGRATION ON ACCOUNT TO ROLE SYSADMIN;      -- storage and catalog integrations
GRANT EXECUTE TASK ON ACCOUNT TO ROLE SYSADMIN;            -- owner of the reconciliation task
GRANT EXECUTE MANAGED TASK ON ACCOUNT TO ROLE SYSADMIN;    -- the task is serverless
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE SYSADMIN;  -- ACCESS_HISTORY for the PII access view
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

## Landing zone

Custodians deliver files to `s3://<landing bucket>/landing/<custodian>/...`. Each object-created event goes to the SQS queue Snowflake owns for the account; the `RAW_LINES` pipe loads the file into `BRONZE.RAW_LINES` as one row per line with the file name, the row number and the time Snowpipe started scanning the file. Nothing in the line is interpreted: no delimiters, quoting, escaping, trimming or NULL substitution. Parsing is the generation plane's job (S3.2.2).

Per-source pipes rendered by the generation plane (S3.2.1) reuse the same storage integration, stage, file format and event notification: `astra-data render` writes `BRONZE.<SOURCE>_PIPE`, which watches the custodian's folder under the landing prefix, matches the source's delivery patterns and loads into `BRONZE.<SOURCE>_RAW_LINES`. Snowflake routes each event to every pipe whose stage location matches the object key, so once sources have their own pipes an environment sets `landing_catch_all_pipe = false` and each file is loaded once, by the pipe of the source that claims it. A file no pipe claims is logged as `UNCLAIMED` after `landing_unclaimed_minutes`.

**Duplicates.** Snowpipe never loads a file name it has already loaded within 14 days, whether or not the content changed, and it does so silently. The stage's directory table makes re-deliveries visible: a re-delivered object shows a newer last-modified time. Every minute the `RECONCILE_FILE_LOADS` task calls the procedure of the same name, which gathers the copy history of every raw-lines table (`RAW_LINES` and each `<SOURCE>_RAW_LINES`), compares it with the directory table and writes `CONTROL.FILE_LOAD_LOG`:

| Status | Meaning | What to do |
|---|---|---|
| `LOADED` | A pipe loaded the file; `ROW_COUNT` rows; `DETAIL` names the pipe and table | Nothing |
| `UNCLAIMED` | No pipe loaded the file within `landing_unclaimed_minutes` | Add or fix the source config whose delivery patterns should claim the path |
| `FAILED` | Snowpipe reported a load failure; `DETAIL` carries the first error | Fix the file, deliver it under a new name |
| `DUPLICATE` | Same name, same content delivered again; not loaded | Nothing; the delivery is recorded |
| `CONFLICT` | Same name, different content, or re-delivery after a failed load; not loaded | Deliver the corrected file under a new name |

Two deliveries of the same file within one reconciliation interval are recorded as one. Superseded object versions stay in the bucket for `landing_noncurrent_version_days`, so the content of a `CONFLICT` can still be inspected.

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

For S1.1.3:

| Criterion | Verified by |
|---|---|
| A file dropped in the landing prefix appears as rows in the raw-lines table within 60 seconds | `scripts/verify_snowpipe.py` drops a four-line file and times its appearance |
| Each row carries file name, row number and ingest timestamp | Unit test `raw_lines_table_carries_file_name_row_number_and_ingest_time` (mandatory columns) and the live script (values) |
| A duplicate file (same name, same hash) is not loaded twice and is logged | The live script re-drops the same file, checks the row count is unchanged and waits for a `DUPLICATE` entry in the file load log |

```bash
pip install boto3 snowflake-connector-python
python scripts/verify_snowpipe.py --environment dev
```

For S1.2.1:

| Criterion | Verified by |
|---|---|
| Diff of object definitions across environments is empty except for variables | `scripts/check-env-parity.sh` in CI: no environment-conditional code, no environment-named code files, matching tfvars and backend files; prints the tfvars diff, which is the whole difference. Every tfvars file is also planned against the standard inventory. |
| A new environment can be created in under two hours | `scripts/new-environment.sh` plus the timed runbook `docs/runbooks/new-environment.md` (100 minutes budgeted). Measured when the next environment is stood up. |

For S1.2.3 (the runner is in `verification/`):

| Criterion | Verified by |
|---|---|
| Sandbox created in under two minutes with the requested config deployed | `astra-verify sandbox check --bundle <bundle>` times creation plus bundle deploy and fails above 120 s |
| Destroyed automatically after the task or after a time limit | Unit tests prove the context manager destroys on success and failure; unit test `reaper_drops_expired_sandboxes_on_a_schedule` checks the reaper's logic and schedule; `astra-verify sandbox reap` exercises it live |
| Cost tagged to the task | Unit tests check the `TASK_ID` and `PURPOSE` tags on both objects and the query tag; the live check reads the tag back with `SYSTEM$GET_TAG` |

For S1.2.4 (alerts):

| Criterion | Verified by |
|---|---|
| A failed Task raises an alert on all three channels within five minutes | Unit tests check the detection SQL, the routes (critical and error to Slack, Jira and email) and the one-minute serverless schedule. Live: suspend a task's warehouse, let a run fail, and watch `ALERT_DELIVERIES` for three `sent` rows. |
| A custodian past its cutoff raises a 'late' alert with the missing files listed | Unit test `late_custodians_become_alerts_listing_missing_files` checks cutoff, business days, the arrival comparison and the listed patterns. Live: set a cutoff a minute ahead for a custodian with an unmatched pattern. |
| Alert severity is configurable per custodian | `alerts.late` and `alerts.task_failure` in the source config, synced by `astra-data custodians sync` (unit-tested), read by the detection procedures. |

Alerts are raised into `CONTROL.ALERTS` even when no channel is configured, so the audit trail exists before the channels do. To raise a test alert by hand: insert a row into `ALERTS` with a severity that has routes and call `CONTROL.DISPATCH_ALERTS()`.

For S1.2.5 (secrets, access history, masking):

| Criterion | Verified by |
|---|---|
| No secret value appears in Git, logs or config | Gitleaks scans the full history on every pull request and push (`.gitleaks.toml`); every secret is a sensitive Terraform variable or a Secrets Manager reference resolved in CI and masked as it is read; unit test `secrets_exist_in_the_client_secret_manager_without_values`. |
| Access history queries return who read which PII column and when | `CONTROL.PII_ACCESS` and the retained `PII_ACCESS_LOG`; unit test `pii_access_view_answers_who_read_which_column_when`; live: `astra-verify pii check` queries the view. |
| A non-privileged role sees masked values on tagged columns | Unit tests check the policies, the tag binding, the unmasked role list and the tagged raw-lines column; live: `astra-verify pii check` reads one raw line as ENGINEER and as AUDITOR and expects the mask on the second. |

To tag a column in generated DDL: `ALTER TABLE ... ALTER COLUMN <col> SET TAG "<DB>"."CONTROL"."PII" = '<category>'`. Categories: `raw_record`, `name`, `account_number`, `tax_id`, `email`, `phone`, `address`, `date_of_birth`, `financial`.

Unit tests in `tests/*.tftest.hcl` run against mocked providers and cover object names, sizes, the access matrix, cost control, the volume, the Open Catalog wiring, the landing pipeline and every validation rule. The bucket hardening is tested in `modules/private-bucket/tests`. All of it runs in CI on every change and needs no credentials.

## Design notes

- **One root module, one tfvars per environment.** No workspaces, no per-environment copies of the code. Environment parity is structural (S1.2.1).
- **Roles are functional, not personal.** People and service users are granted the six roles; nothing is granted to a user here.
- **Schemas use managed access**, so only the schema owner can grant privileges on objects inside them. That keeps the access matrix in this file the single source of truth.
- **Stages get `USAGE` only** on future grants. External stages (S3 landing zones for Snowpipe) use `USAGE`; internal stages would need `READ`/`WRITE`, which Snowflake rejects on external stages. Add an explicit grant if a pipeline needs an internal stage.
- **Sandboxes are not created here.** Ephemeral schemas and warehouses for dry-runs (S1.2.3) are created and destroyed at run time by the sandbox runner, using the ADMIN role's `CREATE SCHEMA` privilege.
- **The Snowflake IAM role ARN is composed, not read back.** Snowflake needs the role ARN to create the external volume, and the role's trust policy needs the IAM user and external id Snowflake reports once the volume exists. Composing the ARN from the account id and a fixed name breaks the cycle in one apply.
- **Snowflake is the only writer.** The Open Catalog role is read only, and CATALOG_SYNC pushes metadata one way. External engines read; they do not write through the catalog.
- **`CATALOG_SYNC` is set with `snowflake_execute`** because the provider does not expose the parameter on databases yet. The resource carries a matching `UNSET` so destroy is clean.
- **Preview provider resources are enabled explicitly.** Storage integration, file format, external stage, pipe and Iceberg table are preview resources in provider 2.20; `providers.tf` lists them and the version constraint is `~> 2.20` so a provider upgrade is a deliberate change.
- **Landing objects depend on the future grants.** A future grant covers only objects created after it, so every stage, table, pipe and task in `landing.tf` has an explicit `depends_on` to the grant resources.
- **The reconciliation task is serverless.** A one-minute schedule on a warehouse would keep it resumed permanently; Snowflake-managed compute bills only the seconds the small query runs.
