# Runbook: change and check an agent's autonomy level

Story S5.13.1 (ADR 0053). Guardrails holds the L0–L3 autonomy level of every (agent, task class) pair the factory has, as an append-only log of changes — the same shape `astra_agents.exception_triage`'s decisions log and `astra_agents.gate_evidence_compiler`'s approvals log already use. Deterministic: no model call, no credentials.

## The levels

| Level | Meaning | Typical use |
|---|---|---|
| L0 · Observe | Agent runs, output is logged, nobody sees it in the flow | New agent or new domain pack in shadow mode |
| L1 · Suggest | Output shown to a human who decides | Rule recovery, CDM changes, complex-tier mappings |
| L2 · Prepare | Agent prepares the change and the evidence; human approves with one click | Simple/medium onboarding, DQ rules, docs |
| L3 · Act with audit | Agent applies the change; human notified; reversible | Whitelisted exception classes, drift config deltas in non-prod, cost sizing |

An (agent, task class) with no change ever recorded is L0 — the safe default, not an error.

## Changing a level

```bash
astra-agents guardrails set-level --changes autonomy.yaml \
  --agent rule-recovery --task-class rule_recovery --level L1 \
  --approver architect@example.com --reason "matches the product spec's own table (Section 8)"
```

Every change needs `--approver` and `--reason`; a blank one is refused, nothing is written. A change **to L3** additionally needs measured evidence, or it is refused the same way:

```bash
astra-agents guardrails set-level --changes autonomy.yaml \
  --agent exception-triage --task-class whitelisted_exception_classes --level L3 \
  --approver architect@example.com --reason "Q3 review" \
  --acceptance-rate 0.9 --sample-size 40 --window "trailing 90 days"
```

`--acceptance-rate`, `--sample-size` and `--window` must be given together. The bar: acceptance rate at least 80%, sample size at least 20 — both are checked before anything is written; a change that does not clear it exits 2 and the log is untouched, not written-and-flagged.

## Checking the current state

```bash
astra-agents guardrails status --changes autonomy.yaml                                    # every (agent, task class) the log has ever mentioned
astra-agents guardrails status --changes autonomy.yaml --agent exception-triage --json     # one agent, machine-readable
```

## Where enforcement actually happens

A level recorded here does nothing by itself until an agent's own code consults it. Exception Triage is the one agent retrofitted so far — the one place in this plane with real L3-shaped behavior before this story:

```bash
astra-agents exception-triage run --exceptions exceptions.csv --rejections domains/custodial/rejections.yaml \
  --decisions decisions.yaml --guardrails autonomy.yaml
```

`--guardrails` is optional; omitted, Exception Triage behaves exactly as it always has. Given, it additionally requires `exception-triage`'s own `whitelisted_exception_classes` task class (the defaults for `--agent-id`/`--task-class`, overridable) to be authorized at L3 in the log — a suggestion needs both its own earned per-code confidence *and* this authorization to auto-apply; either one missing forces `auto_apply` off for every suggestion in that run, and the report says so.

## Deciding

- **A change is refused**: the message names exactly what was missing (a blank field, no evidence, evidence below the bar) — supply it and try again. Nothing partial is ever recorded.
- **`status` shows a task class below the level a task needs**: get it recorded, with real evidence for L3 — this is an architect's decision, not something to work around by omitting `--guardrails` on the call site.
- **Promotion to L3 is reviewed monthly (product spec Section 8)**: this CLI does not schedule that review; recording a fresh `set-level` (with fresh evidence, for L3) each review is how that cadence is carried out today.

## Notes

- The Control plane's own real autonomy admin surface (S6.3.12) does not exist yet; this CLI is a real, working stand-in with the same shape a later UI-backed version would likely take — the same relationship the Gate Evidence Compiler's own approval log already has to the Control plane's approvals API.
- `L3_ACCEPTANCE_THRESHOLD` (80%) is the same bar `astra_agents.exception_triage.AUTO_APPLY_CONFIDENCE` already holds one rejection code's own track record to — chosen to agree with it, not imported from it; the two measure different things (a whole task class's evidence vs. one code's own rolling confidence).
