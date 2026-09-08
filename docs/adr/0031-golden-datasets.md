# ADR 0031: Golden datasets are the legacy path's outputs, captured per business day, hashed, versioned and read-only

Date: 2026-09-08
Status: Accepted
Story: S4.1.2 Capture golden datasets from the legacy path (E4, F4.1, WBS 2.4.2, 2.4.3)

## Context

Parity is proven with data: the factory's pipelines must produce what the legacy Splitter/Loader produced, and the differences must be explained. That needs an oracle that is the legacy behaviour itself, not a description of it: the rows the Loader loaded and the rejections it raised for the historical files of a business day, captured from a non-production replay, kept where nobody can change them, and named precisely enough that a parity run can say which oracle it used.

## Decision

1. **A capture is a reviewed file per custodian.** `golden/<custodian>/capture.yaml` says where the historical files are (a directory or an S3 prefix), the glob of a business day's files with a date token, the command that replays a day's files through Splitter/Loader in non-production, the environment variable that names the Loader database connection, and the queries that read each output and the rejections back. Every query must `ORDER BY`, so that a capture is reproducible and its hash means something; an unknown placeholder or a missing rejections query fails validation, which CI runs.

2. **One dataset per custodian and business day, one version per change.** `astra-verify golden capture` walks the business days of a range. For each day it lists and hashes the files, runs the replay, reads every output back and writes the dataset: `sources.json` (the file list with sha256 and size), one CSV per output, `replay.log`, and `manifest.json` carrying the day, the version, the replay command, the row counts and the hash of every file. The dataset's hash is the hash of its file names and hashes in name order. A day whose content equals the latest version's is recorded as unchanged and nothing is written; different content becomes the next version, with `supersedes` naming the hash it replaces. A day without files or with a failed replay is reported and not written.

3. **The store never overwrites, and the bucket keeps what was written.** The store interface refuses an existing key: locally by refusing the path and making every written file read-only; on S3 with a conditional put. The foundation gains a golden bucket with object lock and a default retention, so a version cannot be modified or deleted through the ordinary API for that period. `astra-verify golden verify` re-reads every indexed version from the store and fails when a manifest's hash is not the hash of its files, or a file no longer hashes as the manifest says.

4. **The repository keeps the index, not the data.** `golden/<custodian>/datasets.json` lists every version with its date, hash, source files, row counts and store reference; entries are appended, never rewritten, and a pull request checks that the file is well formed and that every date is a business day. The index is what a parity report cites.

5. **Coverage is measured against the replay's needs.** `astra-verify golden status` counts the business days captured per custodian and says whether they fall within the thirty to sixty the golden replay wants.

## Consequences

- The replay command and the queries are the client's; the example capture file carries plausible ones against the test schema of the migration story, to be confirmed on the non-production Loader before the first capture, per the runbook.
- S3 and SQL Server access are optional extras (`golden` for boto3, `legacy` for pyodbc); the capture logic is proven against a local store, a local file source and a fake Loader, and the store and source interfaces are small enough that the S3 implementations are thin.
- Object lock in governance mode can be bypassed by a principal with the bypass permission; compliance mode cannot be bypassed by anyone, and is the choice for production once the retention period is agreed. The mode and the retention are foundation variables.
- Datasets are CSV as the queries return them; a change to a query's column list produces a new version with a different hash, which is the intended behaviour: the oracle changed and the index says so.
