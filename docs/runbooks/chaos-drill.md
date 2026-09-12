# Runbook: prove recoverability with the chaos drill

Story S4.3.2 (ADR 0038). The chaos drill injects late, malformed, duplicate and truncated files from one real sample, in one sandbox, and confirms each reaches its documented state, raises an alert, and recovers from nothing more than a resend.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. A normal sample | QE engineer | A normal day's sample file for the source is on hand — the same file a dry run or a volume test already uses is enough. |
| 2. A control_total rule | QE engineer | The source's `dq_rules` include one `control_total` rule; the truncated scenario needs it to catch the gap. |
| 3. Environment | DevOps | The environment's foundation is deployed, so the sandbox can borrow its external volume, control tables and rejection codes. |

## Running it

```bash
astra-verify chaos run \
  --config configs/pershing/pershing_position.yaml \
  --sample GCUS_20260829_POS_001.dat \
  --business-date 2026-08-29 \
  --environment dev
```

`--scenario late|malformed|duplicate|truncated` (repeatable) limits the drill to only the scenarios named; omitted, all four run. The summary line gives each scenario's outcome; the full report is `chaos.md` next to `chaos.json` under `work/chaos/<task id>/`.

Read the report per scenario:

1. **Fault detected** — the count of parse problems, exceptions or the control-total gap the injected file produced. Zero means the platform did not catch what it was supposed to; that is a finding, not a pass.
2. **Alert** — `custodian_late_arrival` for the late scenario (the real alert `CUSTODIAN_GATE` would raise), `chaos_scenario` for the other three (this drill's own evidence that an alert of the right severity was raised; it is not a claim that production alerts on every such exception today — see ADR 0038).
3. **Recovered** — for malformed, duplicate and truncated: the same, correct sample redelivered under a new file name, with the fault's own signal read again and found clear. `n/a` for late, which has no content to fix.

## Deciding

- **Proven**: every scenario detected its fault, raised its alert, and recovered (where a retry applies). File the report as evidence for the recoverability gate.
- **Not proven — a fault was not detected**: check the scenario's injected file under `work/chaos/<task id>/chaos/<scenario>/`; a spec change may have moved the detail record's own match position or value.
- **Not proven — a retry did not recover**: this points at the pipeline, not the drill — the exceptions or the gap should clear from an untouched, correct resend with nothing deleted, so a persistent finding here is a real recoverability defect.

## Notes

- `FILE_DUPLICATE` (an exact resend of an already-loaded file) and file-level truncation checks (`FILE_TRAILER_MISSING`, `FILE_RECORD_COUNT`) are documented in the rejection taxonomy but not yet enforced as file-level, load-blocking checks; the drill's "duplicate" and "truncated" scenarios exercise the record-level and dq_rule checks that are built today (ADR 0038). That gap is a candidate for a future generation-plane story, not something this drill works around silently.
- Nothing this drill writes reaches the real environment's alerting; every alert lands in the sandbox's own copy of `CONTROL.ALERTS`, destroyed with everything else at the end.
- The drill needs exactly one, position-matched detail record type in the spec; a delimited spec or one with several detail records is not yet supported.
