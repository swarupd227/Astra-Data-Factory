# ADR 0076: All four ACs already exist — S2.3.3 and S1.2.4 already close this story

Date: 2026-09-17
Status: Accepted
Story: S7.1.2 SOS and CAS replication (E7, F7.1, WBS 2.7.2, 2.7.3)

## Context

S7.1.2's own four bullets — nightly replication, row counts logged, deltas visible, job alerts on
failure — describe exactly what S2.3.3 "Reference-data replication patterns" (ADR 0015) and S1.2.4
"Alerts to Slack, Jira and email" (ADR 0007) already built, well before this epic started. Before
writing any code, this story's own scope was verified fact by fact against real, committed
evidence rather than assumed:

1. **Nightly schedule.** `domains/custodial/reference-data.yaml` declares both feeds — `security_
   master` (system `SOS`) at `0 2 * * * America/New_York`, `account_xref` (system `CAS`) at
   `30 2 * * * America/New_York`, staggered so CAS runs after SOS. `releases/custodial-reference-
   data/pipeline/tasks.sql` renders a real `CREATE TASK ... SCHEDULE = 'USING CRON ...'` for each,
   both `RESUME`d — not suspended. This bundle is one of the two bundles `astra-data deploy`
   already executes for real on every merge to `main` (dev, then qa).
2. **Row counts logged.** The rendered `REPLICATE_SECURITY_MASTER`/`REPLICATE_ACCOUNT_XREF`
   procedures write a row to `CONTROL.REFERENCE_DATA_RUNS` — `ROWS_SOURCE`, `ROWS_INSERTED`,
   `ROWS_UPDATED`, `ROWS_DELETED`, `ROWS_CONFLICT`, `ROWS_TOTAL` — on every run: succeeded, skipped
   (no new snapshot), or failed (the `EXCEPTION WHEN OTHER` handler records `STATUS='failed'` with
   `ERROR := SQLERRM` before re-raising).
3. **Deltas visible.** Every feed gets a `<TABLE>_CHANGES` table, one row per inserted/updated/
   deleted key with `BEFORE`/`AFTER` as JSON, keyed by run id (ADR 0015: "the delta since any run
   is the changes with a later run id"). `astra_verification.reference.feed_statuses` (S2.3.3,
   real code, real tests with a `FakeExecutor`) turns this into a per-feed `delta` string; `astra-
   verify reference status` prints it and exits 1 when any feed is unhealthy.
4. **Job alerts on failure.** `infra/terraform/foundation/alerting.tf`'s `RUN_ALERTING` calls both
   `DETECT_TASK_FAILURES` (any failed Task in the database, `security_master`/`account_xref`'s own
   replication Tasks included) and `DETECT_STALE_REFERENCE_DATA` (`reference.tf`, feed-specific:
   one `reference_stale` alert per feed per day when no run has succeeded within
   `expected_every_hours`). Both are dispatched by the same `RAISE_ALERTS` serverless Task, on the
   real Slack/Jira/email infrastructure S1.2.4 already built — confirmed by reading the Terraform
   resources directly, not merely their names.

All four are backed by real, already-committed code, already covered by tests that pass today
(`knowledge/tests/test_reference_data.py` — the pattern library, 21 tests; `generation/tests/
test_reference_data.py` — the bundle renderer, 11 tests; `verification/tests/test_reference.py` —
`reference status`, 4 tests, all exercised through the same `FakeExecutor` shape `astra_data.bundle`
already established, no live Snowflake needed to verify the logic itself).

## Decision

**No new code.** Writing a second replication mechanism, a second row-count log, a second delta
table or a second alert path to "satisfy" this story would duplicate S2.3.3 and S1.2.4 rather than
extend them — the same reasoning that kept S6.2.5 from building a second per-exception store ahead
of its own dedicated backlog story. This ADR exists to record, plainly, that the verification was
done and found nothing missing, not to manufacture a deliverable.

What genuinely remains is exactly what ADR 0015 itself named as this story's own job, and both
pieces are real-world facts, not code:

1. **"Wires the feeds into the client's schedules."** The committed cron values (`0 2` / `30 2`
   America/New_York) are concrete and considered — not placeholders — but nothing in this
   repository states Envestnet's own actual SOS/CAS delivery windows to confirm them against.
   Changing a schedule to match a specific client fact this repository does not contain would be
   exactly the kind of fabrication this project has refused throughout (an invented cost figure,
   an invented agent version, an invented query-tag mechanism) — so the values are left as they
   are, and the gap is named rather than papered over.
2. **"Confirms the nightly counts against the source systems."** This needs a live production
   Snowflake environment receiving real SOS/CAS deliveries — the same "no live Snowflake sandbox
   in any environment this codebase runs in" gap named repeatedly elsewhere in this project (dry
   runs, chaos, DR, golden capture). `astra-verify reference status` is the real, tested tool that
   performs this confirmation the moment such an environment exists; it cannot be run against one
   here.

## Consequences

- This story closes with a runbook (`docs/runbooks/sos-and-cas-replication.md`) rather than a new
  module: how to check the two feeds are healthy today, and the two concrete steps left for a real
  client engagement (confirm the schedule against Envestnet's own delivery windows; watch the
  first few real nightly runs through `astra-verify reference status` and `CONTROL.ALERTS`).
- No files under `releases/`, `domains/`, or any plane's `src/` changed for this story — there is
  nothing to render, deploy or test that is not already rendered, deployed and tested.
- If a real Envestnet SOS/CAS delivery schedule becomes known, updating `domains/custodial/
  reference-data.yaml`'s `schedule.cron` and re-running `astra-data reference render` is the whole
  change — the renderer, deploy path and alerting already require nothing else.
