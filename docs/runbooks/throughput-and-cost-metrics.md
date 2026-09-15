# Runbook: the weekly throughput and cost report

Story S6.2.3 (ADR 0072). Custodians live per week, agent acceptance per custodian and day, and
cost per custodian per day when given, assembled into one report for the client's own reporting
cadence.

## Showing the report

```bash
astra-control throughput-metrics show \
  --board board.yaml \
  --rules rules --root . \
  --cost pershing:2026-09-10:1234.56 \
  --role pm
```

- `--board` is optional; without it, `custodians_live_per_week` is empty (no board, no live
  custodians).
- `--rules` is optional; without it, `acceptance` is empty (no rule catalog, no review history).
  `--root` sets the repo root used for display paths when loading rules.
- `--cost` is a repeatable `custodian:business_date:amount` triple — no query-tag mechanism exists
  anywhere in this repository, so cost is caller-supplied only; without any `--cost`, the report
  says so plainly rather than showing a blank table.
- `--agent-eval-weekly` takes a path to an `astra_verification.agent_eval` `WeeklyReport.
  to_dict()` JSON file, shown in its own separate section — a different metric than agent
  acceptance (ADR 0072), never merged into it.
- Add `--json` for the machine-readable form; omit it for the Markdown report.

## Exporting for the client cadence

```bash
astra-control throughput-metrics export \
  --board board.yaml --rules rules --root . \
  --cost pershing:2026-09-10:1234.56 \
  --out reports/2026-W37 \
  --role pm
```

Writes `weekly.md` and `weekly.json` into `--out`, the same convention `astra-verify agent-eval
weekly-report` already uses for its own weekly export.

## Deciding

- **`error: '<x>' is not a custodian:business_date:amount triple`**: a `--cost` value is missing a
  colon-separated part, or has an extra one — check it is exactly `custodian:business_date:amount`.
- **`error: could not convert string to float`**: a `--cost` amount is not a number.
- **Acceptance table is empty**: either `--rules` was not given, or the rule catalog has no
  `confirmed`/`rejected`/`legacy_defect` history yet for any custodian in the date range you
  expected — `recovered` entries (an automated Rule Recovery action, not a human review) never
  count toward acceptance.
- **`custodians_live_per_week` is empty**: either `--board` was not given, or no custodian has
  reached `cutover` on that board yet.

## Notes

- Agent acceptance is a real event ratio — confirmed vs. rejected/legacy_defect outcomes from
  `astra_control.audit_log.rule_status_changes_from`'s own rule-review history — deliberately
  distinct from `astra_verification.agent_eval`'s own gold-set precision/recall, per the product
  spec's own KPI table definition ("share of agent outputs approved without material edit"). Pass
  `--agent-eval-weekly` to show that other metric alongside this one, clearly labeled, never
  conflated.
- `throughput-metrics show|export` are both reads with no write action; every role, including
  auditor, may run either.
