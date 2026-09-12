# ADR 0038: Chaos scenarios exercise codes the taxonomy already names; recovery is a resend, never a query

Date: 2026-09-12
Status: Accepted
Story: S4.3.2 Chaos scenarios (E4, F4.3, WBS 2.4.9)

## Context

The domain pack's rejection taxonomy (`domains/custodial/rejections.yaml`) already names a code, a level and a severity for a malformed line (`RECORD_TYPE_UNKNOWN`), a duplicate key within a file (`MERGE_DUPLICATE_KEY`), a truncated file (the source's own `control_total` dq_rule), and lateness already has a full detector, a DAG reason and an alert (ADR 0007, ADR 0024). A QE drill needs to inject each fault from a real day's own sample, prove the platform reaches exactly the state the taxonomy or the dq_rule promises, prove an alert is raised for it, and prove that fixing it is nothing more than resending the file — no row anywhere is deleted or edited by hand.

## Decision

1. **Two of the four codes the story asks about are not implemented yet, so the drill exercises the two that are.** No renderer in the generation plane raises `FILE_DUPLICATE` (an exact resend of an already-loaded file, by content hash) or the taxonomy's own `RECORD_DUPLICATE`; the merge stage's own `MERGE_DUPLICATE_KEY` (two rows of one file sharing a key) is implemented and is a duplicate in every sense the story cares about, so the drill's "duplicate" scenario injects a repeated detail line rather than a repeated file. Likewise `FILE_TRAILER_MISSING` and `FILE_RECORD_COUNT` are documented but not enforced as a file-level, load-blocking check; the source's own `control_total` dq_rule already catches exactly this condition (a trailer count that no longer matches the file), and the drill's "truncated" scenario targets that. Building file-level duplicate and truncation detection is generation-plane work for a later story, not this one; this ADR records the gap rather than quietly working around it.

2. **Faults are injected at the line level, using the spec's own match rule — nothing about the fixed-width layout is hand-coded.** `inject_malformed` corrupts a detail line's own record-type marker (the bytes the compiled spec says identify it) so no record type matches it; `inject_duplicate` repeats that line immediately after itself; `inject_truncated` removes it while leaving the trailer's own count untouched. All three read the position, length and value straight from the compiled config's detail record (`astra_knowledge.registry.MatchRule`), so the same three functions work for any position-matched fixed-width spec, not only Pershing's.

3. **A retry is the untouched, correct sample delivered again under a new name — proof recovery needs no manual data surgery, not an assertion of it.** Nothing in this module ever issues a `DELETE` or an `UPDATE`; the "fix" for malformed, duplicate and truncated is the same file that was always correct, redelivered as a new file the pipe has never seen. Recovery is confirmed by reading the same signal the fault was detected with — parse problems for the retry's own file name, exceptions for the retry's own run id, the control-total gap for the retry's own file name — and finding it clear. Lateness has no content to fix, so it has no retry: the scenario ends once the `custodian_late_arrival` alert is confirmed.

4. **The drill raises its own alerts, in its own sandbox's copy of CONTROL.ALERTS; it does not claim production already does.** `late` reproduces the exact row `CUSTODIAN_GATE` writes to `CONTROL.CUSTODIAN_RUNS` and the exact `custodian_late_arrival` alert it raises at severity info (ADR 0024 point 4) — real, existing production behaviour, reproduced synchronously the way a dry run and a volume test already reproduce what a live task would do. Malformed, duplicate and truncated have no production alert of their own today (only task failures, lateness, staleness and a DQ score breach do, ADR 0007 and S4.2.3); the drill raises a `chaos_scenario` alert at the taxonomy's or the dq_rule's own severity so the acceptance criterion is met by evidence, not by assertion — but this is the drill's own alert, not a standing detector. Wiring every critical exception into a continuous production alert is a separate, larger decision for a later story, weighed against how noisy it would make `CONTROL.ALERTS` for a rejection class that is expected to happen routinely.

5. **`CONTROL.ALERTS` is now copied into every sandbox**, alongside `FILE_LOAD_LOG`, `MERGE_LOG`, `REJECTION_CODES` and `CUSTODIAN_RUNS` (`astra_verification.dryrun.CONTROL_TABLES`) — the one change this story makes to a module built for S4.1.1, additive and used by every sandbox-based command since a dry run and a volume test never write to it.

## Consequences

- Running all four scenarios takes seven load-and-process cycles in one sandbox (one for late, two each for the three content scenarios); a caller wanting only one scenario passes `--scenario` to skip the rest.
- The drill needs exactly one detail record type in the spec, matched by position; a spec with several detail records, or a delimited (non-fixed-width) match rule, is out of scope until a real config needs it.
- `chaos_scenario` and the drill's own `custodian_late_arrival` row live only in the sandbox; nothing this story writes reaches the real environment's `CONTROL.ALERTS` or dispatches to Slack, Jira or email.
