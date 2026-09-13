# Runbook: explain a dual-run's differences

Story S5.10.1 (ADR 0050). Break Explainer takes a parity comparison's own differences (`astra_verification.parity.compare_rows`) and explains each one: which field, what caused it, and the catalog rule that governs the mapping when there is one. Deterministic: no model call, no credentials.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The config | Steward | The source config this custodian's dual-run used is on hand. |
| 2. The parity mapping | Steward | `golden/<custodian>/parity.yaml` — the same mapping the parity report itself uses. |
| 3. Two row sets | Steward | Legacy rows and lakehouse rows for the business date being explained, each in the parity mapping's own column names. |

## Running it

```bash
astra-agents break-explainer run \
  --config configs/examples/pershing_position.yaml --parity golden/pershing/parity.yaml \
  --legacy legacy_rows.csv --lakehouse lakehouse_rows.csv --business-date 2026-09-01
```

Writes `report.md` and `report.json` under `work/break-explainer/<custodian>/<business date>/`. Exit 0 means every difference was explained; exit 1 means at least one was not.

Read the report in order:

1. **The difference table** — key, field, both values, cause, the rule id (when the mapping has one — never fabricated when it does not), and a plain-words description.
2. **By cause** — how many differences fall into each of the four causes (`transform`, `resolution`, `unmapped`, `unexplained`).
3. **Unexplained — needs a person** — nothing in the config accounts for these; a steward investigates directly, the same as any exception this factory cannot resolve on its own.

## Deciding

- **`transform`**: a rounding or scale difference between the two pipelines' own implementations of the same transform is plausible; confirm against the transform's own semantics before assuming it is fine.
- **`resolution`**: the difference may be reference-data timing between the two runs, not a code bug; check when each pipeline's reference data was last refreshed.
- **`unmapped`**: this field is computed downstream of the config this agent read; trace it by hand.
- **`unexplained`**: a real anomaly by this agent's own definition — a direct, untransformed copy should not differ. Investigate before assuming anything about it.
- **A cited rule**: read the rule's own text and citation before trusting the explanation; the explanation only says which rule governs the field, not that the rule is the actual cause.

## Notes

- This agent explains; it does not decide whether a difference is a factory bug, a legacy defect worth preserving, or something else — "Reports only" (ADR 0050's own consequences).
- No captured golden dataset with real recorded differences exists in this repository yet (`astra-verify golden capture` has never been run here); `agents/examples/break_explainer/` is a committed, clearly labeled illustrative row fixture standing in for a real dual-run, scored against the real config, parity mapping and rule catalog.
- There is no built-in numeric threshold for "≥[T]%" — that number is a client-supplied target a person or a CI wrapper checks the reported `explained_rate` against.
