# Runbook: confirming SOS and CAS replication is healthy

Story S7.1.2 (ADR 0076). Security master (SOS) and account cross-reference (CAS) already
replicate nightly, log row counts, keep deltas visible and alert on failure — built by S2.3.3
(ADR 0015) and S1.2.4 (ADR 0007). This runbook is how to check that in a real environment, and
what is genuinely left to do for a real client engagement.

## Checking feed health

```bash
astra-verify reference status --environment qa
```

Prints one line per feed: `OK`/`FAIL`, the last run's status, its delta (`+inserted ~updated
-deleted`, with conflicts called out), the replica's own row count, and hours since the last
success against the feed's own expected interval. Exit 1 if any feed is unhealthy (failed last
run, or stale past `expected_every_hours`). Add `--json` for the machine-readable form.

```bash
astra-verify reference status --environment qa --changes security_master --run <run_id>
```

Shows the real `SECURITY_MASTER_CHANGES` rows for one run — every inserted, updated or deleted
key, before and after, as JSON.

## Where the data actually lives

- `CONTROL.REFERENCE_DATA_RUNS` — one row per replication run (succeeded, skipped, or failed with
  `ERROR`), with every row count.
- `REFERENCE.SECURITY_MASTER_CHANGES` / `REFERENCE.ACCOUNT_XREF_CHANGES` — the delta of each run.
- `CONTROL.ALERTS` / `CONTROL.ALERT_DELIVERIES` — every `task_failed` or `reference_stale` alert
  raised, and every Slack/Jira/email delivery attempt made for it.
- `CONTROL.REFERENCE_FEEDS` — the schedule and expected-interval metadata, kept in sync from
  `domains/custodial/reference-data.yaml` by `astra-data reference sync` on every deploy.

## Deciding

- **A feed shows `FAIL`, last status `failed`**: read `last_error` in the status output — it is
  the real Snowflake error from the procedure's own `EXCEPTION WHEN OTHER` handler (a malformed
  snapshot column, a duplicate key, a permissions problem). A `task_failed` alert should already be
  in `CONTROL.ALERTS` for it.
- **A feed shows `FAIL`, stale, no error**: no snapshot file has landed under the feed's own
  landing-bucket folder recently enough — check the custodian's own delivery, not this platform's
  code. A `reference_stale` alert fires once per feed per day until a run succeeds.
- **A feed's real delivery schedule differs from what is deployed**: update `schedule.cron` (and
  `timezone`/`expected_every_hours` if those differ too) in `domains/custodial/reference-data.yaml`,
  run `astra-data reference render`, and commit the result — the next deploy picks it up. Nothing
  else needs to change; the procedure, alerting and status tooling are schedule-agnostic.

## What is left for a real client engagement

Both are real-world facts this repository cannot supply on its own, named plainly rather than
guessed at:

1. **Confirm the deployed cron (`0 2` SOS, `30 2` CAS, America/New_York) actually matches
   Envestnet's own SOS/CAS delivery windows.** If they differ, update the domain pack as above.
2. **Watch the first several real nightly runs** through `astra-verify reference status` and
   `CONTROL.ALERTS` once a live environment is receiving real deliveries, to confirm the counts
   the platform logs actually reconcile with what the source systems reported — the concrete
   meaning of "confirms the nightly counts against the source systems" (ADR 0015).
