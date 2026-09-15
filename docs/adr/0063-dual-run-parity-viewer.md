# ADR 0063: Two already-written reports, grouped three ways, never recomputed

Date: 2026-09-15
Status: Accepted
Story: S6.3.7 Dual-run and parity viewer (E6, F6.3, WBS 2.6.19)

## Context

Every piece AC1-AC3 ask for already exists as computed data, in two files this repository already
produces — the question this story answers is how to present it, not how to compute it.

- **AC1 ("trend chart of match rate per day")**: `astra_verification.parity_report.ParityReport`
  (S4.2.2) already aggregates a "dual-run cycle window" into exactly this — `cycles: list[Cycle]`,
  each `Cycle(business_date, result: ParityResult)`, and `ParityReport.to_dict()` already includes
  `"by_cycle": [...]`, one entry per day with that day's own `match_rate`. `astra_control.
  custodian_page` (S6.3.3) already reads this same report's JSON but only pulls four top-level
  summary keys (`match_rate`, `target`, `meets_target`, `trend`) and discards `by_cycle` entirely
  — its own `parity_viewer_note` said outright that this viewer was not built yet.
- **AC2 ("break groups by rule and field with counts")** and **AC3 ("record pair... legacy vs
  lakehouse with differing fields highlighted")**: `astra_agents.break_explainer` (S5.10.1) already
  classifies every dual-run difference by field, cause and (when one applies) rule, and its
  `Explanation.to_dict()` already carries both the legacy and lakehouse values for that field, keyed
  by the record's own identity tuple. Two different groupings of the exact same `explanations` list
  answer AC2 (group by `(rule_id, field, cause)`, count) and AC3 (group by `key`, one row per
  record with every one of its own differing fields together) — no second comparison, no new agent
  call.

**No real multi-day parity report is committed anywhere in this repository.** `golden/` holds only
the parity mapping config (`golden/pershing/parity.yaml`) — never a captured dataset or a run
report; producing either needs `astra-verify golden capture` against a real golden bucket, which
no environment here has (`golden/README.md`'s own admission, and `agents/break_explainer/
eval.yaml`'s own: "no real captured dataset with real differences exists in this repository").

## Decision

1. **`astra_control.parity_viewer` reads two already-written report files and recomputes
   neither.** `load_trend` reads a `ParityReport.to_dict()`-shaped JSON and turns its own
   `by_cycle` into `TrendPoint`s (AC1). `break_groups`/`record_pairs`/`record_pair` all read a
   Break Explainer `report.json`'s own `explanations` list, grouped two different ways (AC2, AC3)
   — the same list, not two different reports. This module never imports `astra_agents` — the same
   "no new agent import, read the report file" boundary `astra_control.queue` and `astra_control.
   agent_review` already established, extended here to the Verification plane's own parity report
   too (`astra_verification.parity_report` is imported only by this ADR's own test fixtures, to
   build a report to read — never by the module itself, which only ever reads JSON).

2. **AC3's "record pair" groups by `key`, not by field.** A Break Explainer `Explanation` is one
   row per (record, field) — grouping by `key` alone turns that into one row per record with every
   one of its own differing fields together, which is what "a record pair... with differing fields
   highlighted" (plural fields, one record) actually asks for. `break_groups` groups the identical
   list the other way, by `(rule_id, field, cause)`, for AC2's own "by rule and field" — the two
   functions are two views over one input, not two separate data sources.

3. **This story adds no write action.** Every one of `parity-viewer.trend/breaks/records/record`
   is a read, and every role already reads every read action uniformly (ADR 0057) — so the story's
   own "steward or QE engineer" actor needed no permission grant to resolve. "QE engineer" is named
   plainly in `astra_control.permissions`'s own module docstring as not one of the six closed roles
   (steward, bsa, engineer, ops, pm, auditor) and not present in `docs/ux/personas.md`'s own role
   mapping either — this is moot for this story's own read-only actions, but is named honestly
   rather than silently resolved as if the backlog's own wording had meant `engineer` all along. A
   future write action here (there is none yet) would need to actually resolve it.

4. **No real multi-day parity report exists to point this story's own tests or examples at, so
   they build one with the real engine instead of hand-writing numbers.** `astra_verification.
   parity.compare_rows` and `astra_verification.parity_report.aggregate`, called directly against
   the real, committed `golden/pershing/parity.yaml` mapping (its own real keys and field
   tolerances — Quantity to 5 decimal places, Price to 4, MarketValue to 2), with a small set of
   illustrative rows in the mapping's own real column shapes (legacy and lakehouse column names
   genuinely differ, per the mapping itself) produce a genuine three-cycle `ParityReport` — a real
   comparison, not invented match-rate numbers. The break-report side needs no fixture of its own:
   the real, already-committed `control/examples/queue/break-explainer/pershing/2026-09-01/
   report.json` (built in S6.3.2 by running the real Break Explainer CLI against its own real
   illustrative rows) is reused as-is.

## Consequences

- `astra_control.custodian_page`'s own `parity_viewer_note` is updated: it used to say "S6.3.7 is
  not built yet"; now, given a parity report, it names the real `astra-control parity-viewer
  trend` command to inspect it.
- `parity-viewer trend|breaks|records|record` take the same optional `--role` every command in
  this plane does, though every role is already authorized for every one of them.
- No rendered dual-run screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested trend, break-grouping
  and record-pair logic a screen would be built on top of.
