# ADR 0072: "Agent acceptance" is a real approve/reject ratio, not `agent_eval`'s precision/recall

Date: 2026-09-15
Status: Accepted
Story: S6.2.3 Throughput and cost metrics (E6, F6.2, WBS 2.6.6)

## Context

The product spec's own KPI table defines "Agent acceptance rate" as "Share of agent outputs
approved without material edit, by agent and tier" — a real-world human-decision ratio, measured
per custodian and day for this story. That is a different quantity from `astra_verification.
agent_eval`'s own metric: precision/recall against a hand-curated gold set, with no custodian or
day dimension at all. The product spec's own API sketch (`GET /metrics/agents`) conflates the two
in one line, but nothing in this codebase computes "share of outputs approved" today — confirmed
exhaustively via research before writing anything.

The one real, per-custodian, per-day record of an agent output actually being approved or rejected
by a person already exists: `astra_control.audit_log.rule_status_changes_from`'s own
`rule_status_change` records (S6.3.11), each carrying `custodian`, `at` (an ISO timestamp) and a
`subject` ending in `-> confirmed`, `-> rejected`, `-> legacy_defect` or `-> recovered`. `confirmed`
is acceptance; `rejected` and `legacy_defect` are both real review rejections; `recovered` is a
Rule Recovery agent's own automated action, not a human review decision, and is excluded from both
counts.

`astra_control.board.live_per_week` already computes "custodians live per week" exactly as this
story asks for it (S6.1.1) — reused directly, unchanged.

No query-tag mechanism exists anywhere in this repository: no Terraform resource sets one, no
rendered SQL references one, no cost-tracking module of any kind exists. "Cost per custodian
visible from query tags" describes an integration this factory has not built yet.

## Decision

1. **Agent acceptance is computed from real review outcomes, not from `agent_eval`.**
   `acceptance_by_custodian_day` groups `rule_status_changes_from`'s own records by `(custodian,
   business_date)` (the date portion of `at`, not the full timestamp), counting `confirmed` as
   accepted and `rejected`/`legacy_defect` as rejected; `recovered` and any record with no
   custodian are skipped. `acceptance_rate` is `None` when the total is zero, never a fabricated
   0 or 1.

2. **`agent_eval`'s own weekly report is carried through, never merged into the acceptance
   numbers.** `build_report`'s `agent_eval_weekly` parameter accepts a caller-supplied
   `WeeklyReport.to_dict()` verbatim and renders it in its own clearly separate section, labeled
   "a different metric" — the two are shown side by side so a reader sees the product spec's own
   ambiguity resolved, not silently picked one way.

3. **Custodians live per week is `astra_control.board.live_per_week`, called directly, unchanged.**
   No reimplementation.

4. **Cost per custodian per day is caller-supplied only.** `build_report`'s `costs` parameter is a
   plain `{(custodian, business_date): amount}` mapping; with nothing given, `report.costs` is
   `()` and `render_markdown` says plainly that no query-tag mechanism exists in this repository —
   the same "caller-supplied only, honest 'no data' otherwise" precedent `astra_control.
   custodian_page` already established for its own missing fields.

5. **One weekly report, two files, for the client's own cadence.** `write_report` writes
   `weekly.md` and `weekly.json` into a given directory, the same convention `astra_verification.
   agent_eval.write_weekly_report` already uses for its own weekly export.

## Consequences

- `throughput-metrics show|export` are both reads with no write action — "reporting is generated"
  implies no mutation; both actions are in `READ_ACTIONS`, available to every role uniformly.
- A reader who wants `agent_eval`'s own precision/recall alongside this report must generate it
  separately (`astra-verify agent-eval weekly-report` or equivalent) and pass the resulting JSON
  via `--agent-eval-weekly`; this module does not run `agent_eval` itself.
- "Cost per custodian" stays empty until a real query-tag or cost-attribution mechanism is built
  elsewhere in this factory; this story does not invent one to fill the gap.
