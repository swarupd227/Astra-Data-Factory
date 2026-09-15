# ADR 0064: Every stage past "expected" is caller-supplied, and the budget is a real backlog number, not invented

Date: 2026-09-15
Status: Accepted
Story: S6.3.8 Run status dashboard (E6, F6.3, WBS 2.6.20)

## Context

AC1's "expected → arrived" pair is already built: `astra_control.custodian_page` (S6.3.3) already
reads a config's own `delivery.files` and `delivery.cutoff_time`, with `arrivals` as an explicit,
documented stand-in for "a live file-load-log query this environment does not have." AC1's own
five stages — expected, arrived, parsed, resolved, published — are grounded in real infrastructure
this repository's own Terraform already names, but not uniformly: `CONTROL.FILE_LOAD_LOG.
OBSERVED_AT` is a real "arrived" column, `CONTROL.GOLD_PUBLISH_LOG.STARTED_AT`/`PUBLISHED_AT` a
real "published" pair, and `astra_data.render.tasks.STEP_ORDER` names `parse.sql`/`resolve.sql` as
real pipeline steps — but **no `PARSED_AT` or `RESOLVED_AT` timestamp column exists anywhere in
this repository's schema** (checked exhaustively). No environment this codebase runs in has a live
Snowflake connection to any of these tables regardless.

AC2's "20-minute budget" is a real, repeated platform requirement — `docs/backlog-v0.2.md` states
it independently across S2.x, S3.2.6, S4.x ("end-to-end ≤ 20 minutes after the last file") and
S7.2.4 — but it is never a schema field, constant or config value anywhere in this repository's
code; every hit is prose in a story's own acceptance criteria.

AC3's "run log" has no real artifact anywhere: not a file, not a schema, not a CLI command in
`astra_data`, `astra_verification`, or `control/` itself. `CONTROL.CUSTODIAN_RUNS` and
`CONTROL.GOLD_PUBLISH_LOG` are real Snowflake tables, but nothing renders them as a browsable log.

`docs/backlog-v0.2.md`'s own S7.2.3 "Status table and watermark" (F7.2, not yet built) names the
exact same five-word stage list this story does, word for word — a different, later story about
the Gold read path's own watermark, not this one.

## Decision

1. **Every stage past "expected" is read from one caller-supplied run file, the same shape
   `astra_control.custodian_page`'s own `arrivals.yaml` already established** — extended from one
   timestamp (arrival) to four (`arrived_at`, `parsed_at`, `resolved_at`, `published_at`) per file
   pattern, plus the run's own `business_date` and, when one exists, a `run_log` reference.
   `load_run` returns an empty `RunInput` for a missing path, exactly the way `custodian_page.
   load_arrivals` already treats a missing arrivals file — never an error, since "no run has
   happened yet" is a real, expected state, not a failure.

2. **`RUN_WINDOW_BUDGET_MINUTES = 20` is this module's own first place the backlog's repeated
   requirement becomes a real number** — the same way `astra_verification.parity.
   MATCH_RATE_TARGET = 0.995` already turned the product spec's own north-star into a constant
   rather than leaving it as prose. Elapsed time is measured from the **latest file's own
   arrival** to the **latest file's own publish** — S4.x's own exact wording, "end-to-end ≤ 20
   minutes after the last file" — the same start point `CONTROL.CUSTODIAN_RUNS.LATEST_ARRIVAL_AT`
   already uses, not an arbitrary choice among the five stage timestamps.

3. **AC1's "late" is "past cutoff, with at least one expected file still missing"** —
   `CustodianRunStatus.is_late(as_of)` needs a real cutoff instant, built from the run's own
   `business_date` combined with the config's own `cutoff_time`/`timezone` via `zoneinfo` (the
   config schema's own real IANA timezone name, `generation/src/astra_data/schemas/config-v0.
   schema.json`) — when no run has been given yet (no `business_date`), there is no real cutoff
   instant to compare against, so the honest answer is "not late," never a guess based on wall
   time alone.

4. **AC3's run log is a caller-given reference, shown when present, named honestly absent when
   not** — the same "a real reference where one exists, an honest note where it does not" shape
   `astra_control.custodian_page`'s own AC2 links already established (ADR 0059), not a link to a
   run-log screen this repository does not have.

5. **The dashboard (many custodians together) is a pure function over already-built statuses,
   `build_dashboard(statuses, as_of=None)`**, the same "aggregate what was already assembled"
   shape `astra_control.queue.for_role`/`counts_by_kind` already use over already-built queue
   items — not a second config-compiling loop duplicated inside the dashboard function itself. The
   CLI pairs `--config` and `--run` positionally (same count, same order, refused outright
   otherwise) rather than inventing a directory-scanning or colon-joined-pair convention not
   grounded in anything else this plane already does.

6. **This story adds no write action** — `run-status.show`/`run-status.dashboard` are both reads,
   available to every role uniformly. Its own actor, "operations user or SRE," names a role ("SRE")
   outside the six closed roles this plane has, the same gap S6.3.7's own "QE engineer" already
   named — moot here for the same reason: nothing to grant differently by role, so nothing is
   silently mapped onto `ops` or any other role.

## Consequences

- No real multi-file, multi-stage run has ever happened in this repository (no live Snowflake
  connection exists in any environment this codebase runs in) — this module's own tests build run
  files by hand, in the real config's own delivery-file pattern shapes (`configs/examples/
  pershing_position.yaml`), rather than reading a real captured run that does not exist.
- No rendered dashboard screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested per-file staging,
  lateness and budget logic a screen would be built on top of.
