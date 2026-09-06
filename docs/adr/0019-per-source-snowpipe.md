# ADR 0019: One pipe and one raw-lines table per source; the foundation's catch-all pipe is a bootstrap

Date: 2026-09-06
Status: Accepted
Story: S3.2.1 Bronze DDL and Snowpipe (E3, F3.2, WBS 2.3.2)

## Context

The foundation (ADR 0003) lands every file under the landing prefix into one table, `BRONZE.RAW_LINES`, through one catch-all pipe, and reconciles arrivals into `CONTROL.FILE_LOAD_LOG`. That is enough to prove the landing zone works before any source exists. A rendered source needs its own landing: a pipe that watches its custodian's folder and matches its delivery patterns, and a raw-lines table its stages read, generated from the config so landing to Bronze needs no hand-written SQL. Two pipes that both match a file would both load it; Snowflake routes an S3 event to every pipe whose stage location matches.

## Decision

1. **A source owns its raw lines.** `astra-data render` writes `BRONZE.<SOURCE>_RAW_LINES` (the shape of `RAW_LINES`, `LINE` tagged `raw_record` from the start) and `BRONZE.<SOURCE>_PIPE`, an auto-ingest pipe whose stage path is the custodian's folder under the landing prefix and whose `PATTERN` is the source's delivery patterns turned into one regular expression. The pipe reuses the foundation's storage integration, stage, file format and S3 event notification; nothing is created per source in AWS. The lines view every stage reads now reads the source's own table.

2. **The custodian folder comes from the delivery patterns.** Every pattern must sit under one folder (`pershing/GCUS_%_POS_%.dat`); patterns that sit under different folders or none fail rendering, because the pipe's path is what makes "points at the custodian's landing prefix" true and keeps the pipe from reading other custodians' files.

3. **The pipe is created once, not replaced.** A pipe's COPY statement cannot be altered, and recreating a pipe loses the events that arrive in between and its load history, which is what stops a file being loaded twice. The bundle uses `CREATE PIPE IF NOT EXISTS`; changing a source's folder or patterns is an operational change: drop the pipe in a quiet window and redeploy. The rendered SQL says so.

4. **The catch-all pipe becomes a bootstrap.** `landing_catch_all_pipe` keeps the foundation's pipe loading (on by default, so a fresh environment behaves as ADR 0003 describes and the S1.1.3 live check still holds). Once an environment's sources have rendered pipes, it sets the variable to false: the pipe object stays, because the bucket notification targets its SQS queue and every auto-ingest pipe in the account shares that queue, but it watches a folder nothing is delivered to, so each file is loaded once, by the pipe of the source that claims it. `RAW_LINES` and its PII tag remain.

5. **Reconciliation covers every raw-lines table.** `RECONCILE_FILE_LOADS` is now a procedure that gathers the copy history of `RAW_LINES` and every `<SOURCE>_RAW_LINES` (Snowflake's copy history is per table), then classifies arrivals as before, naming the pipe and table in `DETAIL`. A landed file that no pipe has loaded after `landing_unclaimed_minutes` is logged as `UNCLAIMED`: it landed but no source config claims its path, which is the operational signal a per-source design needs. Late-custodian detection and intake keep reading the log unchanged.

## Consequences

- The bundle manifest gains the pipe as its second step; deploying a bundle to an environment whose catch-all pipe is on double-loads that source's files into two tables until the variable is turned off. The environment runbook states the order: deploy the bundles, then set `landing_catch_all_pipe = false` and apply.
- S3.2.2 fills the typed Bronze tables from the source's lines view; the problems and file registry are unchanged.
- The S1.1.3 live check (`verify_snowpipe.py`) exercises the catch-all pipe and RAW_LINES; a per-source live check belongs to the source's own acceptance in E4.
