# ADR 0024: One Tasks DAG per custodian, gated on a complete file set

Date: 2026-09-07
Status: Accepted
Story: S3.2.6 Per-custodian Tasks DAG with completeness (E3, F3.2, WBS 2.3.7)

## Context

Until now each source's process task ran on its own schedule (the target lag) whether or not the custodian had finished delivering. Resolution over a half-delivered day is wasted work that is redone when the rest arrives, and it does not fit the post-arrival window the operations team plans around. The late alert (ADR 0007) already knows what a custodian is expected to deliver each business day, from the config's delivery block, and the file load log knows what arrived. What was missing was a start signal derived from the two, and a rerun when a late file finally comes.

## Decision

1. **One DAG per custodian, rooted in a gate task.** Every source whose config has a delivery block renders its process task as a child of `BRONZE.<CUSTODIAN>_GATE`. The gate is a serverless task on a one-minute schedule that calls `CONTROL.CUSTODIAN_GATE('<custodian>')`; the children run `AFTER` it with `WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE('<CUSTODIAN>_GATE') = 'run'`. The gate is created with `IF NOT EXISTS` and never replaced by a bundle, so deploying one source does not detach the others; the DAG is suspended while a child is attached and every task of it is resumed with `SYSTEM$TASK_DEPENDENTS_ENABLE` at the end.

2. **Complete means every expected pattern has a loaded file for the business date.** The gate reads `CONTROL.CUSTODIAN_FILES` (synced from the same delivery block the late detector uses) and `CONTROL.FILE_LOAD_LOG`. A file's business date is its last-modified date in the custodian's timezone, exactly as the late detector counts it, so "late" and "complete" can never disagree about which files belong to which day. Business dates of the last seven days are considered.

3. **A run starts on the first completion and again on any later arrival.** Each start is a row of `CONTROL.CUSTODIAN_RUNS` carrying the newest file-load-log observation it saw. The gate starts a run for a business date when the set is complete and either no run exists for that date or a file was observed after the last run's observation. A file that completes the set after the cutoff therefore starts the DAG within a minute of appearing in the file load log, which the reconcile task refreshes every minute by default; a re-delivery of a file already processed starts it again. The reason is recorded: `complete`, `late_arrival` or `redelivery`.

4. **A late arrival is told to operations once.** When a run's set was completed by a file last modified at or after the cutoff, the gate raises one `custodian_late_arrival` alert at severity `info`, keyed by run, naming the late files. It follows the `custodian_late` alert the detector raised at the cutoff, so the thread reads: late, then arrived and running.

5. **Nothing in one custodian's DAG refers to another.** Each custodian has its own gate, its own children on its own tier warehouse, and `ALLOW_OVERLAPPING_EXECUTION = FALSE` on its own root only. The two control objects the DAGs share, `CUSTODIAN_RUNS` and `ALERTS`, are only inserted into. A slow or failing run holds back its own custodian and no other; `SUSPEND_TASK_AFTER_NUM_FAILURES` applies per gate.

6. **Every rendered source is in a DAG.** A config without a delivery block cannot be rendered at all, because the pipe needs its patterns (ADR 0019), so there is no scheduled-only path to keep. The target lag now governs only the parse dynamic tables.

## Consequences

- Intake and merge also wait for the complete set, not only resolution: the process procedure runs the stages together (ADR 0021). A file for a date that never completes is not merged until the set completes; the late alert is the signal, and the rows are in Bronze and in the parse tables meanwhile.
- The gate runs every minute per custodian; its queries touch only small control tables. Its cost is a serverless XSMALL minute-scale task per custodian.
- Two sources of the same custodian run in parallel after the gate. A dependency between sources (a transaction file needing the day's positions first) would be a further DAG edge and is not rendered yet.
- Changing the gate's logic is a Terraform change to `CUSTODIAN_GATE`; changing a source's task is a bundle deploy. The task name convention `<CUSTODIAN>_...` from ADR 0007 gives both the custodian's failure severity.
