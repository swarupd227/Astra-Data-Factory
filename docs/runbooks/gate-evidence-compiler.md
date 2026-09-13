# Runbook: assemble a gate pack for a release

Story S5.11.1 (ADR 0051). The Gate Evidence Compiler assembles a gate pack from the verification tools this factory has already run — DQ, parity, volume, chaos, DR, agent evaluation — plus a human approval, against seven named criteria. Deterministic: no model call, no credentials.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The reports | Project manager | Whichever of DQ, parity, volume, chaos, DR and agent-eval reports exist for this release are on hand — each tool's own `report.json`, wherever it was written. |
| 2. Approval (once ready) | Project manager | A person has recorded approval for this exact release with `record-approval` below. |

A criterion with no report given, or no approval recorded yet, is not an error — it shows as NOT MET, honestly, the same as if that check had actually failed.

## Running it

```bash
astra-agents gate-evidence-compiler run --release pershing_position-2026-09-13 \
  --dq-report work/dq/.../report.json --parity-report work/parity-report/.../report.json \
  --volume-report work/volume/.../report.json --chaos-report work/chaos/.../report.json \
  --dr-report work/dr/.../report.json --agent-eval-report work/agent-eval/weekly/weekly.json \
  --approvals approvals.yaml
```

Every flag is optional; omit whichever reports do not exist yet for this release. Writes `gate_pack.md` and `gate_pack.json` under `work/gate-evidence-compiler/<release>/`. Exit 0 means every criterion is met; exit 1 means at least one is not.

Read the report:

1. **The criteria table** — all seven, always, each MET or NOT MET with a one-line summary of its evidence.
2. **Not met** — the criteria still needing evidence, with what each one actually checks.

## Recording an approval

```bash
astra-agents gate-evidence-compiler record-approval --approvals approvals.yaml \
  --release pershing_position-2026-09-13 --approver steward@example.com --note "Every other criterion met"
```

Appends one entry; the file is rewritten each time, oldest first — the same append-only shape `astra_agents.exception_triage`'s own decisions log uses. An approval only counts for the exact release it names.

## Deciding

- **All seven MET**: the pack itself is the evidence a gate decision is made on — "gate decisions are made on the pack alone" (the product spec's own guardrail).
- **Any NOT MET**: read which tool has not been run, or has not produced a passing result, for this release, and address that directly — this agent never guesses at evidence it was not given.
- **This agent never runs a verification tool itself.** It only reads reports already produced; running DQ, parity, volume, chaos, DR or agent-eval for this release is a separate step.

## Notes

- The six tool-report fixtures under `agents/examples/gate_evidence_compiler/` are illustrative, hand-authored to match each real tool's own committed output shape — not real run output; volume, chaos and DR all need a live Snowflake sandbox this repository's own agent tooling does not have.
- There is no partial credit: `all_met` is every criterion at once, matching "gate decisions are made on the pack alone."
