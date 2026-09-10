# Runbook: replay a config change before promotion

Story S4.1.3 (ADR 0032). A steward's self-service change to a promoted config is replayed against the custodian's already-captured history before it is promoted, so a difference is seen and explained, not discovered later.

## Before replaying

| Step | Who | Done when |
|---|---|---|
| 1. History | Steward | The custodian has golden datasets captured (S4.1.2): `astra-verify golden status golden` shows the custodian with at least some business days. Fewer than 30 still replays; the report says how many days it covered. |
| 2. The draft | Steward | The changed config validates on its own: `astra-data validate configs/<custodian>/<source>.yaml`. |
| 3. The old version | Steward | Known: the commit or tag the config was last promoted at (for `--old-ref`), or a saved copy of the file as it was (for `--old`). |
| 4. Environment | DevOps | The environment's foundation is applied; the reference-data and CDM bundles are deployed there (dry runs and replay borrow them, the same as a dry run). |

## Replaying

```bash
astra-verify replay \
  --new configs/pershing/pershing_position.yaml \
  --old-ref origin/main \
  --custodian pershing \
  --environment dev
```

Two sandboxes are created, one per config, against the same business days; both are destroyed when the replay ends, whatever happened. The summary line names the config differences, the rows compared, added, removed and changed, and whether the change is eligible for promotion without SME review. The full report is `replay.md` next to `replay.json` under `work/replay-reports/<task>/`.

Read the report in order:

1. **Config differences** — every mapping, rule, DQ rule and resolution part that differs, from the two configs alone. Often this alone explains what the change does.
2. **Data differences**, grouped by the same rule or field — if a group shows rows affected, that mapping or rule is what produced them; the sample row shows the actual before and after value.
3. **Exceptions** and **DQ tests** — a rejection code appearing, disappearing or changing count, or a test that now fails that passed before (or the reverse).

## Deciding

- **Zero differences** (the summary says "eligible for promotion without SME review", exit code 0): promote the change without further review. This is also what replaying a config against itself proves is possible — the gate has no false positives on a no-op.
- **Differences found** (exit code 1): every group in the report is a claim about what changed and why; confirm each one is intended before promoting. A difference under a `dq:` or `resolution:` group with no matching config-difference line is not expected and is worth a second look — it means the same config produced different data, which should not happen against captured history.
- **The replay did not start or a run failed** (exit code 2, or the report's old/new run status is not `ran`): fix the cause (a compile problem, a missing bundle, a sandbox failure) named in the report and run again.

## Notes

- `--days` overrides how many of the most recent captured business days are replayed (default 30). More days is a stronger check and a longer run.
- `--old` and `--old-ref` are mutually exclusive; `--old-ref` shells out to `git show <ref>:<path of --new>`, so it only works from inside the repository the config lives in.
- Replay costs roughly twice a dry run's time and Snowflake spend (two sandboxes, both against the full N days of files); it is a pre-promotion step for a real change, not a per-commit check.
