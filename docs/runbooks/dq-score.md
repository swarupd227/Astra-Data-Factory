# Runbook: score a source's DQ rules for a business date, and act on a breach

Story S4.2.3 (ADR 0035). The DQ runner reads what a source's dq_rules already measured that day — it does not re-run any check — and folds the results into a severity-weighted score per entity.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. Rules | Governance engineer | The source's config has `dq_rules` with a `severity` each (`configs/<custodian>/<source>.yaml`); `astra-data validate` passes. |
| 2. Deployed | DevOps | The bundle is deployed to the environment: every dq_rule became a data metric function or a system-function association, scheduled `TRIGGER_ON_CHANGES` (S3.2.7, ADR 0025). |
| 3. Measured | — | At least one load has happened for the business date, so at least one DMF has triggered; a day with no changes has nothing to score. |

## Running it

```bash
astra-verify dq run \
  --config configs/pershing/pershing_position.yaml \
  --business-date 2026-08-29 \
  --environment dev
```

The summary line gives each entity's score against the target (98% unless `--target` says otherwise) and how many of its rules had a measurement that day; the full report is `dq.md` next to `dq.json` under `work/dq/<source>-<date>/`.

Read the report in order:

1. **Score and status** — `meets target`, `below target`, or `no data` (the entity's rules had no measurement that day; not the same as a failure — check whether the source actually ran).
2. **Rules scored** — an entity with 1 of 3 rules scored is telling you the same thing as `no data` for the other two: their DMFs have not triggered, most often because that table has not changed since the last measurement.
3. **Breached rules** — each names its severity, how many of the day's measurements failed, and the check in words; an `error`-severity breach costs the score four times what an `info` one does, so a low score with only `info` rules breached is a different conversation than one `error` rule failing.

## Deciding

- **At or above target**: the entity is in governance for the day; nothing further to do.
- **Below target, an alert already fired**: the breach is in `CONTROL.ALERTS` (kind `dq_score_breach`) and was dispatched through the same Slack, Jira and email channels as any other alert (ADR 0007) — treat it the way any other severity-routed alert is treated. The alert's body lists the same breached rules the report does.
- **`no data`**: not a quality breach by itself. Check whether the source ran for the business date at all; if it did and the DMFs still show nothing, the schedule or the association may not have deployed — check `SHOW DATA METRIC FUNCTIONS` and `INFORMATION_SCHEMA.DATA_METRIC_FUNCTION_REFERENCES` for the table.

## Notes

- `--target` overrides the 98% default for one run; a lasting change for a source belongs to the client engagement's own quality target, set deliberately, not invented here.
- `--no-alert` writes the report only, for a trial run — useful right after deploying new dq_rules, before they are trusted to page anyone.
- An alert is deduplicated per entity per business date; re-running `dq run` for a date that already raised one does not raise a second.
- Only the rules named in `dq_rules` are scored — the automatic sanity checks every source also gets (row counts, required-field nulls, a merge key's duplicates) are a safety net, not the reviewed set governance scores against.
