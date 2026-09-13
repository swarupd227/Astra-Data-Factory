# Runbook: check a new file for drift against its spec

Story S5.9.1 (ADR 0049). Drift Watcher compares a delivered sample file against its Source Spec and proposes a delta — a record-length change, a new code a field never declared — before the file reaches Silver. It never writes to the spec; a person reviews the delta and edits the spec themselves.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The spec | Data engineer | The Source Spec this delivery should match is already in the registry. |
| 2. The sample | Data engineer | The delivered file (or a representative excerpt of it) is on hand. |

No credentials, no deploy: this reads two files already on disk.

## Running it

```bash
astra-agents drift-watcher run --spec specs/pershing_gcus/2017-07-25.yaml --sample GCUS_20260915.dat
```

Writes `report.md` and `report.json` under `work/drift-watcher/<spec id>/<spec version>/`. Exit 0 means nothing found, safe to proceed; exit 1 means drift was detected — a real signal to hold this file before Silver until a person looks.

Read the report's "Proposed delta" table: the spec path that would change, the current and proposed values, and the evidence (how many lines, how many occurrences) behind the proposal.

## Deciding

- **A record-length finding**: almost always means the custodian added (or removed) a field. Confirm with the custodian or the layout document before editing `file.record_length` — a majority of lines agreeing on a new length is strong evidence, but this agent does not know *what* the new bytes mean, only that they are there.
- **A new-code finding**: add the code to the field's `codes` list once its meaning is confirmed — with the custodian, the layout document, or a support ticket. Do not add a code this agent found with no idea what it means; the report only proves the value now appears more than once, not what it is for.
- **This agent never edits the spec itself.** Promoting a delta is the same "agent proposes, human approves" shape as every other agent here — open the spec file and make the change by hand once you have confirmed it.

## Notes

- Detection assumes drift is additive at the end of a line (a new trailing field) — the common real-world case. A field inserted in the middle of a record, shifting every position after it, is not detected by this first version (ADR 0049's own consequences).
- A candidate new length or code needs to repeat across the sample (a majority of lines, or at least two occurrences) before it is proposed — one truncated or corrupted row is not treated as real drift.
- `agents/examples/drift_watcher/` is a committed, clearly labeled example: the real `pershing_gcus` spec against a clean baseline sample and a sample with both kinds of drift deliberately injected, so the detection behavior can be seen without a real delivery.
