# Runbook: propose DQ rules from a Source Spec

Story S5.6.1 (ADR 0046). The DQ Generator reads a Source Spec's own structure — a trailer's control total, a sign field's declared codes, a date field, a merge or pairing's key, a pairing's two record types — and proposes DQ rules in the exact `dq_rules` shape a real config uses. Deterministic: no model call, no credentials.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The spec | Data engineer | A Source Spec is in the registry (`specs/<id>/<version>.yaml`). |
| 2. A client's own targets (optional) | Data engineer | If this engagement has its own severity preferences by category, a small targets file is ready (see below); otherwise every rule defaults to `error`, the same fallback `astra_data.dq` already uses. |

## Running it

```bash
astra-agents dq-generator run --spec specs/pershing_gcus/2017-07-25.yaml
```

Writes `report.md`, `report.json` and `dq_rules.yaml` under `work/dq-generator/<spec id>/<spec version>/`. `dq_rules.yaml` is in the exact shape a real config's own `dq_rules:` block takes — reviewed, it can be pasted straight in.

With a client's own severity targets:

```bash
astra-agents dq-generator run --spec specs/pershing_gcus/2017-07-25.yaml --targets client_targets.yaml
```

```yaml
# client_targets.yaml
severity:
  date: warning      # this engagement treats a future-dated record as a warning, not an error
  pairing: info
```

A category not named keeps the platform default (`error`); nothing is ever set to a number or severity this agent invented on its own.

Read the report by category — control_total, sign_field, date, key, pairing — each with the rule's id, kind, level, check and severity. A category with none generated is not a gap to fill by hand automatically: it means the spec's structure did not give this agent enough to propose one confidently (see Notes).

## Deciding

- **A generated rule**: reasonable to accept into a real config as-is; review the `check` text and citation against the layout document before promoting, the same as any other agent's draft here.
- **An empty category**: read why in ADR 0046's own consequences before assuming it is a bug — a control total is skipped on purpose when a spec has more than one detail record type sharing one trailer count, and a pairing category is naturally empty for a spec with no `pairing` block at all.
- **Promoting**: paste `dq_rules.yaml`'s reviewed contents into the real config's own `dq_rules:` block.

## Notes

- The five categories this agent generates (control-total, sign-field, date, key, pairing) are not the whole of `config-v0.schema.json`'s `dq_rules` vocabulary — `range` and general `not_null` checks unrelated to pairing are out of scope, because both would need a number or a field this agent has no structural way to derive without guessing (ADR 0046's own consequences).
- A trailer's control-total field is recognized by its name ending in `count` (`detail_count`, `record_count`); a custodian whose trailer names it differently produces no control-total rule rather than a wrong guess.
