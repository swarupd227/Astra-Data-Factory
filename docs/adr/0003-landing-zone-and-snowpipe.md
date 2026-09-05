# ADR 0003: Landing zone and Snowpipe

Date: 2026-09-05
Status: Accepted
Story: S1.1.3 Snowpipe on S3 events (E1, F1.1, WBS 2.1.3)

## Context

Custodian files must become raw-line rows in Bronze on arrival, not on a 6 AM schedule. Each row needs the file name, the row number and the ingest timestamp. A file delivered twice with the same name and content must not load twice, and the second delivery must be logged. Snowpipe provides event-driven loading and its own 14-day file deduplication, but it deduplicates silently and by file name alone.

## Decision

1. **A dedicated landing bucket per environment**, hardened like the Iceberg bucket through a shared module, with versioning so a re-delivered file's previous version survives for a configurable period.

2. **Snowpipe auto-ingest on S3 events, one shared foundation pipe.** A storage integration and a read-only IAM role limited to the landing prefix, an external stage over the prefix, and a pipe that loads every file beneath it into `BRONZE.RAW_LINES`. The bucket notification targets the account's Snowflake SQS queue for the whole prefix, so per-custodian pipes rendered by the generation plane (S3.2.1) need no further AWS change.

3. **Raw lines are literal.** The file format has no delimiter, no quoting, no escaping, no trimming and no NULL substitution. One column per line, written with `METADATA$FILENAME`, `METADATA$FILE_ROW_NUMBER`, `METADATA$FILE_CONTENT_KEY`, `METADATA$FILE_LAST_MODIFIED` and `METADATA$START_SCAN_TIME`. The raw-lines table is Iceberg on the environment volume like every other table.

4. **Duplicates are detected by comparing the stage directory table with Snowpipe's copy history.** Snowpipe ignores a re-delivered name; the auto-refreshing directory table shows its new last-modified time and MD5. A serverless task runs every minute and appends to `CONTROL.FILE_LOAD_LOG` one entry per arrival: `LOADED`, `FAILED`, `DUPLICATE` (same content) or `CONFLICT` (different content, or re-delivery after a failure), each with a plain-language reason and, for conflicts, what to do.

5. **The log is append-only and keyed by object version** (file name plus last-modified time), so the history of a file name is complete and auditable.

6. **Preview provider resources are enabled explicitly** and the provider constraint is tightened to `~> 2.20`. Storage integration, file format, external stage, pipe and Iceberg table are preview resources in this provider version; the lock file pins what was tested.

7. **Acceptance is measured live by `scripts/verify_snowpipe.py`**: drop a file, time its appearance, check the row metadata, re-drop it, confirm the row count is unchanged and a `DUPLICATE` entry appears.

## Consequences

- The foundation ships one raw-lines table for the whole landing prefix. When the generation plane renders per-custodian tables and pipes, the shared pipe can be scoped or retired; the storage integration, stage and event wiring stay.
- Two deliveries of the same file within one reconciliation interval are recorded as one entry. The interval is a variable; one minute is the default.
- The reconciliation joins the directory table's relative path to copy history's file name. Both are relative to the stage location; the live script confirms the join on first run.
- Snowpipe's 14-day memory means a corrected file must be delivered under a new name. The `CONFLICT` entry says so.
- The Terraform role needs `CREATE INTEGRATION`, `EXECUTE TASK` and `EXECUTE MANAGED TASK` on the account, added to the bootstrap statements.
