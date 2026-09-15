# Runbook: check a custodian's run, or every custodian at once

Story S6.3.8 (ADR 0064). Per custodian and file: expected, arrived, parsed, resolved, published,
timed against a real 20-minute budget, so a late or slow custodian is seen at a glance.

## One custodian's own run

```bash
astra-control run-status show \
  --config configs/examples/pershing_position.yaml \
  --run releases/pershing/2026-09-15/run.yaml
```

`--run` is a small file giving, per expected file pattern, whichever of `arrived_at`/`parsed_at`/
`resolved_at`/`published_at` has actually happened (ISO timestamps), plus the run's own
`business_date` and, if one exists, a `run_log` reference:

```yaml
business_date: "2026-09-15"
run_log: "https://internal/airflow/dags/pershing_gate/runs/2026-09-15"
files:
  "pershing/GCUS_%_POS_%.dat":
    arrived_at: "2026-09-15T10:00:00Z"
    parsed_at: "2026-09-15T10:03:00Z"
    resolved_at: "2026-09-15T10:07:00Z"
    published_at: "2026-09-15T10:12:00Z"
```

Omit `--run` (or point it at a file that does not exist yet) to see every file still at
`expected` — a real, honest "nothing has happened yet" state, not an error. Exit 1 means the
custodian is late as of `--as-of` (default: now); read the printed "Missing" line and cutoff.

## Every custodian together

```bash
astra-control run-status dashboard \
  --config configs/examples/pershing_position.yaml --run releases/pershing/2026-09-15/run.yaml \
  --config configs/examples/another_custodian.yaml --run releases/another/2026-09-15/run.yaml \
  --as-of 2026-09-15T12:00:00Z
```

`--config` and `--run` are repeatable and paired by position — the same count, in the same order,
one pair per custodian. Exit 1 means at least one custodian is late; the printed "Late" line names
which. (This repository has only one config that compiles standalone today,
`configs/examples/pershing_position.yaml` — the second `--config` above names a second real
source's own committed config once one exists.)

## Deciding

- **A file shows `expected` and nothing else**: no run file was given, or the run file has no
  entry for that pattern — check the path, and that the pattern matches the config's own
  `delivery.files` exactly.
- **Timing shows "(in progress)"**: the run has not reached both a real arrival and a real publish
  for at least one file yet — not a failure, just not measurable yet.
- **"OVER BUDGET"**: the elapsed time from the latest file's own arrival to the latest file's own
  publish exceeded 20 minutes (`RUN_WINDOW_BUDGET_MINUTES`, `docs/backlog-v0.2.md`'s own repeated
  requirement) — check which file arrived last and where the time actually went (parsed, resolved
  or published taking longest) from the per-file table.
- **"no run log given"**: nothing to click through to — this repository has no real run-log
  artifact (ADR 0064); add `run_log` to the run file once a real one (an Airflow run URL, a
  Snowflake worksheet) exists.

## Notes

- No real multi-stage run has ever happened in this repository — no environment here has a live
  Snowflake connection to `CONTROL.FILE_LOAD_LOG`, `CONTROL.MERGE_LOG` or `CONTROL.
  GOLD_PUBLISH_LOG`. A run file is always hand-supplied or written by whatever platform integration
  eventually watches those tables — this command never queries them itself.
- `run-status show|dashboard` take the same optional `--role` every command in this plane does;
  both are reads, available to every role (this story adds no write action).
