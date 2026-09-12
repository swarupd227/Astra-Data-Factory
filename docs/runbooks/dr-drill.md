# Runbook: prove RTO and RPO with the DR drill

Story S4.3.3 (ADR 0039). The DR drill destroys the standardized zone (Bronze and Silver) in a sandbox and restores it from the same retained files, measuring the restore against the client's own proposed RTO and RPO.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The proposed targets | DevOps engineer | The RTO and RPO the client's proposal commits to are known, in minutes; this drill does not assume or invent them. |
| 2. A normal sample | DevOps engineer | A normal day's sample file for the source is on hand — the same file a dry run, a volume test or a chaos drill already uses. |
| 3. Environment | DevOps | The environment's foundation is deployed, so the sandbox can borrow its external volume, control tables and rejection codes. |

## Running it

```bash
astra-verify dr run \
  --config configs/pershing/pershing_position.yaml \
  --sample GCUS_20260829_POS_001.dat \
  --rto-minutes 30 \
  --rpo-minutes 15 \
  --environment dev
```

The summary line gives the measured restore time against the proposed RTO and whether every row survived against the proposed RPO; the full report is `dr.md` next to `dr.json` under `work/dr/<task id>/`.

Read the report in order:

1. **RTO** — the measured wall-clock time to recreate the schemas, redeploy the canonical model and the source bundle, and replay the same files back through the pipe, against the proposed minutes.
2. **RPO** — met when every table's restored row count matches its baseline exactly; a breach names the table and the row gap, not an invented number of minutes. Because the drill replays every retained file in full, a working restore path achieves zero rows lost by construction — a breach here is a real defect in the restore procedure, not an artifact of partial replay.
3. **Phases** — where the time went: baseline load, the disaster, the restore itself, and measuring the result.

## Deciding

- **Proven**: both RTO and RPO were met; file the report as the evidence for the DR gate.
- **RTO breached**: read the "restored" phase's own seconds against the others; a slow restore usually means the warehouse size needs revisiting for this custodian's data volume, the same conversation the volume test has.
- **RPO breached**: a real recoverability defect — the restore procedure did not fully recreate what was there. Compare the exact table and row gap the report names against what the bundle's own DDL and the config's own mappings should have produced.

## Notes

- "The standardized zone" is Bronze and Silver only; reference data, exception history and control state are not dropped, so resolution during the restore behaves exactly as it would on a normal day.
- The files replayed come from local disk in this drill, standing in for the landing zone's own retained copies; the drill does not separately re-verify that the landing zone's actual S3 retention window covers the outage being simulated — that is a foundation-level operational fact, checked elsewhere.
- The sandbox is destroyed at the end whether the drill succeeds or fails, the same as every other NFR drill; nothing it wrote outlives the run, and nothing it does touches a real environment.
