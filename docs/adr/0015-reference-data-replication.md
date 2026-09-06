# ADR 0015: Reference data is replicated on a schedule and resolution joins the replica

Date: 2026-09-06
Status: Accepted
Story: S2.3.3 Reference-data replication patterns (E2, F2.3, WBS 2.2.10)

## Context

Account and security resolution needs the platform's security master (SOS) and account cross-reference (CAS). The legacy Loader resolves record by record against those systems; the factory has a 20-minute processing window and resolution has to be set-based. The product spec lists reference-data patterns as part of a domain pack, and the backlog asks for a nightly replication that records row counts, keeps the delta visible and is what resolution joins.

## Decision

1. **Feeds are declared in the domain pack**, `domains/<name>/reference-data.yaml`: what each feed carries and how it is keyed, how its snapshots arrive (a folder under the landing bucket's reference prefix, CSV with a header row), when it runs and how often a success is expected, which canonical entity it resolves and through which identifiers, and which taxonomy codes resolution against it raises (not found, ambiguous, conflict). The pack loader validates the feeds against the model's entities and the rejection taxonomy.

2. **Every delivery is a full snapshot; the delta is computed.** Source systems export their current state; the replication compares it with the replica and records what was inserted, updated and deleted, with the row before and after, in `<TABLE>_CHANGES` under the run id. The delta since any run is the changes with a later run id. A snapshot key that is blank or repeated is a conflict: those rows are left out, counted, written to `<TABLE>_CONFLICTS` with the feed's conflict code, and the replica keeps what it had. A run that finds no new snapshot files is recorded as skipped and changes nothing.

3. **The pattern library holds the reference implementation** (`patterns/reference_data.py`): a replica with its run history, change log and conflicts, plus set-based resolution by key or by alternate identifiers tried in order, returning found, not found or ambiguous with the feed's rejection code. The rendered SQL is checked against it.

4. **The generation plane renders a release bundle** per pack, `releases/<pack>-reference-data/`: the replica, staging, change and conflict tables in the REFERENCE schema; one procedure per feed that loads the newest snapshot files through the foundation's stage and file format, computes the delta, brings the replica in line and records the run and its counts in CONTROL.REFERENCE_DATA_RUNS; identifier views that unpivot the alternate identifiers so resolution is one join; one serverless task per feed on its cron schedule; and tests that keys are unique, the last run did not fail and the replica is fresh. The bundle is committed, checked in CI and deployed by the same pipeline as every other bundle.

5. **Freshness is monitored by the foundation.** CONTROL.REFERENCE_FEEDS is synced from the feeds file on every deploy; DETECT_STALE_REFERENCE_DATA, called by RUN_ALERTING, raises one `reference_stale` alert per feed and day when no run has succeeded within the expected interval. Task failures are alerted by the existing task-failure detector.

6. **Resolution never calls the source system.** Rendered resolution (S3.2.4, S7.1.3) joins REFERENCE tables and their identifier views; the feeds file is the only place a source system is named. `astra-verify reference status` shows, per feed, the last run, its counts and its delta.

## Consequences

- The REFERENCE schema and the reference prefix of the landing bucket are part of the foundation; the storage integration allows the prefix and a stage reads it with a CSV file format.
- Loading only files that no earlier run has loaded relies on Snowflake's COPY load history; a snapshot delivered under a name already loaded is ignored. Source systems must deliver a new file name per snapshot, which the runbook for onboarding a feed states.
- S7.1.2 wires the feeds into the client's schedules and confirms the nightly counts against the source systems; S7.1.3 renders the resolutions against the replicas.
- A second domain pack declares its own feeds and gets its own bundle without changes to the tooling.
