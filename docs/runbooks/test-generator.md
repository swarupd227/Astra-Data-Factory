# Runbook: generate tests and synthetic edge files from a config

Story S5.7.1 (ADR 0047). The Test Generator reads a config's own `dq_rules` and, for each one it knows how to synthesize a branch for, writes a SQL assertion (`tests/unit/*.sql`) and a complete, minimal, entirely synthetic sample file built to trip exactly that rule (`tests/edge/*.dat`). Deterministic: no model call, no credentials, and no code path that reads anything but the spec and config it was given.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The config | QE engineer | A config with `dq_rules` is on hand — hand-written, or the DQ Generator's own `dq_rules.yaml` pasted into one. |
| 2. The spec | QE engineer | The Source Spec the config references is in the registry. |

## Running it

```bash
astra-agents test-generator run --config configs/examples/pershing_position.yaml --spec specs/pershing_gcus/2017-07-25.yaml
```

Writes `tests/unit/<rule id>_<branch>.sql`, `tests/edge/<rule id>_<branch>.dat`, `report.md` and `report.json` under `work/test-generator/<config id>/`.

Read the report in order:

1. **The table** — every rule, which branch was synthesized, and whether an edge file exists for it (some branches — a range boundary that cannot exist in an unsigned raw field — get a SQL assertion only, and the report says so).
2. **Not covered** — a rule this agent has no way to synthesize a branch for: a `condition` whose SQL is not the one recognized shape (`FIELD <= CURRENT_DATE()`), or a kind this story does not cover (`resolution`, a rule catalog entry's own prose text). Branch coverage below 100% here is a real signal, not a bug — write that test by hand.

## Deciding

- **A generated `.sql` file**: fill in `{{ DATABASE }}`, `{{ SCHEMA }}` and `{{ TABLE }}` (or `{{ DETAIL_TABLE }}`/`{{ TRAILER_TABLE }}` for a control total) with the real rendered table names once known; the WHERE/HAVING condition itself is already correct.
- **A generated `.dat` file**: safe to load into a sandbox or test pipeline as-is — every value in it, not only the one field under test, comes from this agent's own fixed synthetic vocabulary, never a real sample or reference data.
- **A rule marked "not covered"**: write that test and edge case by hand; this is the honest boundary of what this agent can synthesize without guessing.

## Notes

- `resolution` (account/security found/not-found/closed/ambiguous/inactive) and rule catalog entries (prose `text`, not `dq_rules`) are out of scope — read ADR 0047's own consequences for why.
- `agents/test_generator/` is this agent's real gold set — not a stand-in — scoring branch coverage against the real, already-committed `configs/examples/pershing_position.yaml`.
