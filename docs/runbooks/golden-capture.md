# Runbook: capture the golden datasets of a pilot custodian

Story S4.1.2 (ADR 0031). The golden datasets are the legacy path's outputs for thirty to sixty business days, captured once, kept read-only, and cited by every parity run.

## Before the first capture

| Step | Who | Done when |
|---|---|---|
| 1. Non-production Loader | Data engineer | A non-production Splitter/Loader and its database exist with the same code version as production; the version is noted in the evidence pack. |
| 2. Historical files | Data engineer | The custodian's files for the range are under one prefix (`files.location` in `golden/<custodian>/capture.yaml`), named so that `files.pattern` picks one business day's files. |
| 3. Replay command | Data engineer | `legacy.replay` runs the Loader on the files a business day names and returns non-zero on failure. Tried by hand on one day. |
| 4. Queries | Data engineer | Each `legacy.outputs` query returns one business day's rows with a stable `ORDER BY`; the rejections query returns every rejection the Loader raised for the day's files. `astra-verify golden check golden` passes. |
| 5. Store | DevOps | The environment's foundation is applied with the golden bucket (`terraform output golden_bucket_name`); the engineer's AWS identity can write to it. |
| 6. Secret | DevOps | The Loader connection string is in the secret manager and exported as the variable `legacy.connection_env` names. |

## The capture

```bash
astra-verify golden capture golden/pershing --from 2026-06-01 --to 2026-08-29 --store s3://astra-dev-golden-123456789012
```

Every business day of the range is captured in turn; the summary names each day's status: captured (a new version), unchanged (identical to the latest version), no files, or replay failed with the log path. Then:

```bash
astra-verify golden status golden          # days captured against the 30 to 60 the replay needs
astra-verify golden verify golden/pershing --store s3://astra-dev-golden-123456789012
```

Commit `golden/<custodian>/datasets.json`: it is the index every parity run cites, and a pull request checks it.

## Notes

- A day captured again with the same content writes nothing. A day whose content differs (a Loader fix, a re-delivered file) becomes the next version; the previous version stays and the index lists both, so a parity run that cited v1 still means v1.
- `--store` may be a directory for a trial capture; the files are made read-only, but only the bucket's object lock makes the oracle tamper-evident for others.
- Fewer than thirty days is not a failure of the tool; `status` says so, and the replay (S4.1.3) needs the range.
