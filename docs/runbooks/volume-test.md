# Runbook: prove the 20-minute window at three times normal volume

Story S4.3.1 (ADR 0037). The volume test inflates a normal day's own sample files, runs a custodian's whole daily set through to the Gold publish in one sandbox, and reports whether it finished within 20 minutes of the last file.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. A normal day | QE engineer | Sample files for a normal business day are on hand for every source of the custodian being tested — the same files a dry run (S4.1.1) or a golden capture (S4.1.2) already uses are enough. |
| 2. The custodian's configs | QE engineer | Every source config of the custodian's daily set is known; they all name the same `source.custodian`. |
| 3. Environment | DevOps | The environment's foundation is deployed, so the sandbox can borrow its external volume, control tables and rejection codes. |

## Running it

```bash
astra-verify volume run \
  --config configs/pershing/pershing_position.yaml \
  --sample GCUS_20260829_POS_001.dat \
  --factor 3 \
  --business-date 2026-08-29 \
  --environment dev \
  --warehouse-size MEDIUM
```

Add a further `--config` per additional source of the custodian's daily set; every `--sample` given is routed to whichever config's own delivery pattern matches it, so one pool of files serves every source without saying which file belongs to which.

The summary line gives the end-to-end time against the 20-minute budget and the warehouse size the run used; the full report is `volume.md` next to `volume.json` under `work/volume/<task id>/`.

Read the report in order:

1. **End-to-end, after the last file** — the headline number, measured from the last inflated file's load to the Gold publish that writes the watermark.
2. **Files and lines per source** — `factor` times a normal day's own files and rows; the Silver and Gold row counts are still a normal day's, since duplicate copies of the same account and security merge into one position, not `factor` of them. That is expected, not a bug.
3. **Phases** — where the time went: deploy, load, process, publish. A source whose files did not match any given `--sample` shows zero files and is skipped, not failed.

## Deciding

- **Within budget**: the warehouse size this run used is the evidence for the peak-day plan; record it.
- **Over budget**: read the phases first — "processed" dominated by warehouse queueing points at undersizing for peak load; "deployed" or "published" taking unusually long is worth its own look before concluding the warehouse is undersized.
- Try a larger `--warehouse-size` and rerun before concluding a source needs re-architecting; the report exists to answer "what size clears 3x", not just "does XSMALL clear it".

## Notes

- The measured end-to-end excludes the roughly one-minute cadence `CONTROL.CUSTODIAN_GATE` actually polls at in production — a known, small gap against a 20-minute budget, not hidden (ADR 0037).
- `--factor` defaults to 3; a different peak multiple for a different custodian's planning is one flag, not a different tool.
- The sandbox is destroyed at the end whether the run succeeds or fails, the same as a dry run; nothing it wrote outlives the run.
