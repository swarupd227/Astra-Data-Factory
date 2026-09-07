# ADR 0028: The factory drives SnowConvert AI for the historical migration and keeps every result with the release

Date: 2026-09-07
Status: Accepted
Story: S3.3.1 SnowConvert AI CLI integration (E3, F3.3, WBS 2.3.15)

## Context

The legacy Loader keeps its history in SQL Server. The product spec says to use the target platform's own migration tooling where it exists and generate only what it does not cover: for SQL Server to Snowflake that is SnowConvert AI, which extracts a schema, converts it, migrates the data and validates the result. What the factory adds is the driving seat: the phases run from one reviewed file, the converted schema lands where the platform says the archive store is, and the results, logs and tool version are kept with the release like every other artifact.

## Decision

1. **A migration is a reviewed file.** `migrations/<id>.yaml` (schema `migration-v0`) names the SQL Server source, the schemas that move, the archive-store schema they land in, the SnowConvert AI command and the argument list of each phase. The command lines are data, not code: the CLI's verbs and flags are confirmed against the licensed install and kept in the file, reviewed like a config. Placeholders (`{connection}`, `{schemas}`, `{database}`, `{target_schema}`, the phase directories) are the only variable parts; an unknown placeholder fails validation.

2. **Secrets are named, never written.** The connection string and the license key are read from the environment variables the file names. The validator rejects a value that looks like a connection string. The command line recorded in each log shows `<VARIABLE>` where the secret was, and the tool's output is redacted of the secret's value before it is written.

3. **The four phases run in order and stop at the first failure.** `astra-data migrate run` records the tool's version, runs extract, convert, migrate and validate in a working directory, and writes one log per phase with the command, exit code and duration. A failed phase is recorded and the rest are skipped, so a rerun starts from a known state; `--phase` runs a subset for the same reason.

4. **The converted DDL is a release bundle whose step lands in the archive store.** After the convert phase, every converted DDL file is rewritten so its objects belong to `{{ DATABASE }}.<ARCHIVE>`: object names the tool left unquoted are upper-cased as Snowflake would resolve them, quoted names keep their case, several source schemas are told apart by a schema prefix, and `USE` and `CREATE SCHEMA` statements are dropped. The result is `releases/<id>-migration/ddl/archive_<id>.sql`, the single step of a manifest like any other bundle's, with a test that every converted table exists in the archive store. With a connection the bundle is deployed before the migrate phase, so the data migration finds its tables; the deploy pipeline redeploys it with every other bundle. EWI markers the tool leaves in the DDL are counted per file and reported; resolving them is S9.1's "differences listed".

5. **Results and logs live with the release.** `releases/<id>-migration/migration/runs/<run id>/` holds the four logs, `run.json` (status, tool version, per-phase exit codes and timings, converted tables, EWI count, whether the DDL was deployed) and the tool's own reports copied from the convert output. Runs accumulate; `PROVENANCE.json` names the latest run, every run, the migration file's digest and every file's digest.

6. **The archive store is a foundation schema.** `ARCHIVE` joins the standard schemas with the standard grants: ENGINEER creates, PIPELINE writes, STEWARD and AUDITOR read. Consumers reach history through Gold or by explicit grant, not by default.

## Consequences

- No SnowConvert AI install is available to CI, so the end-to-end path is proven with a stand-in tool that behaves like the CLI (version, outputs, reports, failures) and, before the first live run, with the test schema in `migrations/examples/test-schema.sql` applied to a SQL Server test database, per the runbook. The argument lists in the example file are written against the documented CLI and must be confirmed on the licensed install; the file is where that confirmation is recorded.
- The rewrite handles tables and views and references to the moved schemas. Procedures and functions the tool converts pass through with their qualified names rewritten but are not listed as tables.
- The converted tables are standard Snowflake tables as the tool emits them; making the archive store Iceberg, when the type mapping allows it, is a decision for S9.1 with the differences listed.
- Migration bundles are deployed by the same pipeline and pass the same checks (`bundles check`, `bundles lint`) as rendered bundles; they are produced by a run, not by `astra-data render`, so there is no `--check` for their currency.
