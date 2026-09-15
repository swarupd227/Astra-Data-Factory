# Runbook: search who approved what, when

Story S6.3.11 (ADR 0067). Every approval and change record this platform already writes,
unified, filtered by user, custodian, date and action, exported to CSV.

## Searching

```bash
astra-control audit-log show \
  --promotion-requests control/examples/promotion-requests.yaml \
  --drift-change-requests releases/pershing_gcus/drift-change-requests.yaml \
  --rules rules --specs specs \
  --guardrail-log work/guardrails/changes.yaml \
  --gate-approval-log agents/examples/gate_evidence_compiler/approvals.yaml \
  --board control/examples/board.yaml \
  --custodian pershing --user steward
```

Every source flag is optional and repeatable — give whichever logs you have; a missing or
unreadable one contributes nothing, never an error. `--rules` alone (with `--specs` to resolve
custodians on drift approvals) already gives the rule catalog's own full confirm/reject history.
Filters (`--user`, `--custodian`, `--action`, `--start`, `--end`) are case-insensitive substring
matches on the already-aggregated records, oldest first.

## Exporting to CSV

```bash
astra-control audit-log export \
  --rules rules --gate-approval-log agents/examples/gate_evidence_compiler/approvals.yaml \
  --start 2026-09-01T00:00:00Z --end 2026-09-30T23:59:59Z \
  --out evidence/2026-09-audit.csv
```

Same source and filter flags as `show`; writes the matching records to `--out` as a real CSV
(`kind,action,user,at,custodian,subject,evidence,agent_version,source`), openable in any
spreadsheet tool or handed to an auditor directly.

## Deciding

- **`agent_version` is always blank**: honest — nothing in this platform records an agent's own
  version on an approval today (ADR 0067), not a bug in this export.
- **`evidence` says "not recorded"**: the underlying source recorded no note/reason for that
  decision — most sources allow one but do not require it.
- **A guardrails L3 change shows extra detail in `evidence`**: that is the level change's own
  structured evidence (acceptance rate, sample size, window) — the one source with more than free
  text, appended after its own `reason`.
- **A board move is missing from the results**: a transition with no recorded `by` (the field is
  optional) is skipped on purpose — an audit row nobody can be attributed to is worse than not
  showing it.
- **An agent-suggestion accept/reject (S6.3.6) never appears**: there is genuinely nothing to
  read — its own gold-set schema has no room for who/when (ADR 0067, point 7). This is a real,
  named gap, not a filtering mistake.

## Notes

- `audit-log show|export` take the same optional `--role` every command in this plane does; both
  are reads — searching or exporting never approves anything itself, so every role, including
  auditor, already has full access.
- No real committed guardrails changes log or drift-change-requests log exists anywhere in this
  repository yet — point `--guardrail-log`/`--drift-change-requests` at a real one once one
  exists; both flags are simply omitted until then.
