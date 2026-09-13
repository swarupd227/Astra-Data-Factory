# Runbook: triage a batch of exceptions

Story S5.8.1 (ADR 0048). Exception Triage reads a batch of exception rows (the CDM `Exception` entity's own shape) and the domain pack's rejection taxonomy, and proposes a resolution per root cause, with a confidence based on a real, measured acceptance history — never a fabricated number. Deterministic: no model call, no credentials.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The exceptions | Operations user | A CSV of `NEW`-status exception rows is on hand — `EXCEPTION_ID, REJECTION_CODE, LEVEL, ENTITY, CUSTODIAN_ID, FIELD_NAME, RAW_VALUE, MESSAGE, RECORD_KEY, RAISED_AT, STATUS`, the CDM `Exception` entity's own columns. |
| 2. The taxonomy | Operations user | `domains/<pack>/rejections.yaml` is the domain pack this batch belongs to. |
| 3. A decisions log (optional, recommended once one exists) | Operations user | `decisions.yaml`, built up over time by `record-decision` below; without one, every code starts at a neutral 50% confidence. |

## Running it

```bash
astra-agents exception-triage run \
  --exceptions exceptions.csv --rejections domains/custodial/rejections.yaml --decisions decisions.yaml
```

Writes `report.md` and `report.json` under `work/exception-triage/`.

Read the report in order:

1. **Suggestions, by root cause** — one row per group of exceptions sharing a code and the same failing value (or field, when there is no single value). The resolution text is the taxonomy's own `resolution` for that code, never invented.
2. **Codes with no taxonomy entry** — a code this batch names that is not in the taxonomy at all; add it there first, or resolve it by hand.
3. **Acceptance rate, per code** — every code's measured track record, whether or not it has an exception in this batch. This is the same number that decides auto-apply eligibility.

## Recording a decision

After a person accepts or rejects a suggestion (however that happens — a workbench click, a ticket, a conversation), record it so future confidence reflects it:

```bash
astra-agents exception-triage record-decision --decisions decisions.yaml --code SECURITY_NOT_FOUND --decision accepted --by steward@example.com
```

Appends one entry; the file is rewritten each time, oldest first, the same append-only shape `astra_knowledge.rules`'s own history uses.

## Deciding

- **A suggestion, `auto-apply: no`**: a person resolves it — the suggestion is a starting point, not a change made for them.
- **A suggestion, `auto-apply: yes`**: this code is both whitelisted in the taxonomy (`auto_resolve: true`) and has a measured acceptance rate at or above 80% — the product spec's own "measured acceptance rate above a threshold" requirement for L3. Whether to actually wire auto-apply into a pipeline is a separate decision (`S6.3.12`'s own admin surface); this report only says which suggestions have earned it.
- **A code newly marked `auto_resolve: true` in the taxonomy**: it starts at 50% confidence here like any other code with no history — it will not show `auto-apply: yes` until real decisions push it past 80%, on purpose.

## Notes

- `NEW_SECURITY` and `MISSING_TX_CODE` (this story's own backlog shorthand) are `SECURITY_NOT_FOUND` and `TRANSACTION_CODE_UNMAPPED` in the real, committed taxonomy; `ACCOUNT_NOT_FOUND` is named exactly (ADR 0048's own consequences).
- `agents/examples/exception_triage/` is a committed, clearly labeled illustrative fixture — no real exception data exists anywhere in this repository (exceptions are only ever produced at pipeline runtime).
- This agent never applies anything itself; it only reports which suggestions a whitelist and a track record together allow.
