# Runbook: historical migration with SnowConvert AI

Story S3.3.1 drives SnowConvert AI from the factory (ADR 0028). This runbook is the first live run: the CLI is confirmed, the test schema goes end to end, then the real database. Record the run ids in the evidence pack.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. Tool | Migration engineer | SnowConvert AI is installed on the runner with a license; `snowct --version` prints. The version is what `run.json` will record. |
| 2. Command lines | Migration engineer | The four argument lists in `migrations/<id>.yaml` match `snowct --help` of the installed version, phase by phase. Anything that differs is changed in the file, reviewed and merged. `astra-data migrate validate` passes. |
| 3. Secrets | DevOps | The connection string and the license key are in the client's secret manager and exported as the variables the file names (`connection_env`, `license_env`) on the runner. They are never put in the file. |
| 4. Target | DevOps | The environment's foundation is applied; `ARCHIVE` exists (`terraform apply` after the schema was added). |
| 5. Plan | Migration engineer | `astra-data migrate plan --migration migrations/<id>.yaml --environment dev` prints the four commands with `<VARIABLE>` in place of every secret. |

## The test schema

Apply `migrations/examples/test-schema.sql` to an empty SQL Server test database, point a copy of the migration file at it (`source.database`, the connection variable), and run:

```bash
astra-data migrate run --migration migrations/examples/loader_sqlserver.yaml --environment dev
```

Done when: all four phases report `succeeded`; `releases/loader-sqlserver-migration/ddl/archive_loader_sqlserver.sql` creates the three tables in `ARCHIVE`; `astra-data test --environment dev releases` passes the bundle's test; the row counts in the validate log match the test schema (2 accounts, 2 positions, 1 rejection). Commit the bundle directory: the DDL, the logs, `run.json`, the tool's reports and `PROVENANCE.json` are the evidence.

## The real database

Same command against the Loader database. Expect EWI markers: `run.json` counts them per file and the summary line names the total. Each marker is a construct the tool could not convert on its own; resolving them is S9.1, and the converted DDL is not final until the count is zero or every remaining marker is accepted in review.

A failed phase stops the run; its log under `migration/runs/<run id>/` has the command and the tool's output. Fix the cause and run again; `--phase` reruns one phase (for example `--phase migrate --phase validate` after the tables were corrected by hand) and the run is recorded like any other.

## Notes

- `--no-deploy` runs the phases without a Snowflake connection; the migrate phase then expects the tables to exist already.
- `--tool` replaces the command in the file, for a wrapper script on a runner where the CLI is not on the path.
- Every run adds a directory under `migration/runs/`; the DDL step is always the latest convert. Nothing is deleted.
