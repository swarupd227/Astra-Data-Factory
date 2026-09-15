# Runbook: inspect dual-run gate evidence

Story S6.3.7 (ADR 0063). Match rate by day, break groups by rule and field, and drill-down to a
record pair with the break explanation — reads two already-written reports, computes nothing new.

## Trend: match rate per day

```bash
astra-control parity-viewer trend --parity-report releases/pershing-parity/report.json
```

Reads a real `astra_verification.parity_report.ParityReport` (S4.2.2's own `astra-verify parity
report` output) and shows its own `by_cycle` series: one row per business date, with that day's
match rate, whether it met the ≥99.5% target, and the row counts on each side.

## Break groups: by rule and field, with counts

```bash
astra-control parity-viewer breaks --break-report control/examples/queue/break-explainer/pershing/2026-09-01/report.json
```

Reads a real `astra_agents.break_explainer` `report.json` and groups every explanation by
`(rule id, field, cause)`, busiest group first — which rule's own field is causing the most
breaks, at a glance.

## Record pairs: legacy vs lakehouse, differing fields highlighted

```bash
astra-control parity-viewer records --break-report control/examples/queue/break-explainer/pershing/2026-09-01/report.json
```

Groups the same explanations by record key instead — one section per differing record, every one
of its own mismatched fields together (legacy value, lakehouse value, cause, rule).

## Drill-down: one record pair

```bash
astra-control parity-viewer record --break-report control/examples/queue/break-explainer/pershing/2026-09-01/report.json \
  --key ACC0000002 --key 594918104 --key 2026-09-01
```

`--key` is repeatable, one component per part of the record's own key (account, security id,
business date, for Pershing positions) — the exact key a `RowMismatch`/`Explanation` already
carries. Shows the differing field(s), both values, the cause, the rule (if one applies) and the
human explanation together.

## Deciding

- **`trend` refuses with "not a parity report"**: the file given is not a `ParityReport`'s own
  JSON (no `by_cycle` key) — check the path names a `parity_report.json`, not a single-cycle
  `parity.json` (`astra_verification.parity`'s own single-date report has no cycle series at all).
- **A break group has "(no rule)"**: the field has no rule reference in the compiled config at
  all — `astra_agents.break_explainer`'s own `cause` (`unmapped`/`unexplained`) already says why;
  read the record pair's own description for the specific reasoning.
- **`record` says "no record with key"**: `--key` components must match the explanation's own
  `key` tuple exactly, in order — copy them from `records`' own output rather than retyping.

## Notes

- No real multi-day parity report or real captured dual-run difference is committed anywhere in
  this repository — `golden/` holds only the parity mapping config, and producing either needs a
  real golden bucket no environment here has. This runbook's own examples are illustrative paths;
  the real, already-committed `control/examples/queue/break-explainer/pershing/2026-09-01/
  report.json` is the one real break-report example in this repository.
- `parity-viewer trend|breaks|records|record` take the same optional `--role` every command in
  this plane does; every role already reads every one of them (this story adds no write action).
