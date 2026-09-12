# ADR 0037: The volume test inflates a real day's own files and does by hand what the Tasks DAG's two ends would do

Date: 2026-09-11
Status: Accepted
Story: S4.3.1 3x volume test (E4, F4.3, WBS 2.4.8)

## Context

A dry run (S4.1.1, ADR 0030) proves one source's pipeline is correct on a sample, in under ten minutes, by deliberately skipping the landing pipe, the per-custodian Tasks DAG and the DMF bindings — a sandbox has no live Snowpipe subscription or minute-cadence task schedule to exercise safely and fast. A volume test asks a different question for a different reason: not whether the pipeline is correct, but whether the *whole custodian's* daily set — every source, through to the Gold publish a consumer actually reads (ADR 0026) — finishes within the 20-minute window at three times a normal day's volume. There is no synthetic data generator in this platform yet (that is the Test Generator agent's future job), and there is no live Snowflake account to run a real 20-minute Task-scheduled drill against in this repository's environment.

## Decision

1. **Volume is inflated from a real day's own sample files, not synthesized.** `astra-verify volume run` takes `factor` byte-identical copies of each given sample, renamed so the custodian's own delivery pattern still matches them (`GCUS_20260829_POS_001.dat` becomes `..._001-x1.dat`, `..._001-x2.dat`, `..._001-x3.dat`). Bronze parsing, resolution and the Silver merge compute see `factor` times the files and rows — the thing a volume test measures — even though a natural-key merge leaves Silver the same day it always was, since `factor` copies of the same account and security are one position, not `factor` of them. The report says this plainly so nobody mistakes the Silver or Gold row count for `factor` times a normal day's distinct data.

2. **The sandbox does synchronously, once, what the custodian's Tasks DAG would do asynchronously, every minute.** Like a dry run, the volume test skips `pipe.sql`, `tasks.sql` and the DMF bindings — there is still no live schedule to wait on safely in a throwaway sandbox — but it goes one step further than a dry run: after every source has processed, it writes a `CONTROL.CUSTODIAN_RUNS` row exactly the shape `CONTROL.CUSTODIAN_GATE` would write once a business date's file set is complete (ADR 0024), then calls `CONTROL.PUBLISH_GOLD` directly (ADR 0026) — the same procedure the custodian's `<CUSTODIAN>_PUBLISH` task calls, deployed into the sandbox as the `releases/custodial-gold` bundle, exactly like `releases/custodial-reference-data` is already deployed as an extra bundle for a dry run. This mirrors a technique S4.1.1 already established: `load_statements` logs a loaded file into `CONTROL.FILE_LOAD_LOG` "as the reconcile task would" rather than waiting for a task to do it.

3. **End-to-end is the same two ends operations would read in production.** The clock starts at the last inflated file's load and stops when `CONTROL.PUBLISH_GOLD` returns, read back against `GOLD.WATERMARK` for evidence of what was actually published (row count, publish time) — not merely that the call returned. Warehouse size is not measured after the fact; it is chosen up front by `--warehouse-size` (default `MEDIUM`, larger than a dry run's `XSMALL` default, since 3x volume needs more compute) and reported as exactly what it was, because that is what the sandbox's warehouse was created at.

4. **A custodian's daily set is a list of source configs, not just one.** `--config` is repeatable; every one must belong to the same custodian, checked before any sandbox is created (`single_custodian`, tested as a pure function since this repository has only one committed source config to compile against). Each given `--sample` is routed to whichever config's delivery pattern matches it, the same matching a dry run already uses, so one pool of files serves a multi-source custodian without the caller having to say which sample belongs to which source.

## Consequences

- The measured end-to-end excludes the roughly one-minute cadence at which `CONTROL.CUSTODIAN_GATE` actually polls in production; against a 20-minute budget this is a small, known gap between the harness and the real DAG, not hidden — it is the price of a test that finishes in minutes instead of waiting on a live task schedule.
- `factor` inflates files and rows through compute, not distinct data; a client engagement that needs genuinely distinct 3x data (to stress a resolution cache or reference-data join at scale, for instance) needs the future synthetic data capability, not this story.
- `CONTROL.CUSTODIAN_RUNS` is already copied into every sandbox by S4.1.1's `control_statements` (its `CONTROL_TABLES` list already names it); `releases/custodial-gold` is deployed the same way `releases/custodial-reference-data` already is. Nothing about the sandbox's own machinery needed to change for this story.
- A source among the given configs whose delivery pattern matches none of the given samples is recorded with zero files and skipped for processing, not treated as a failure — a caller testing a subset of a custodian's sources is not forced to supply files for all of them.
