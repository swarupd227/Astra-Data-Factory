# Runbook: set an autonomy level, manage the self-healing whitelist

Story S6.3.12 (ADR 0068). Autonomy as a controlled setting: L0-L3 per agent and task class, a
reason required and logged, L3 refused outright unless the measured acceptance rate already
clears the bar; the self-healing whitelist shown and change-requested, never edited directly.

## Setting an autonomy level

```bash
astra-control autonomy-admin set-level \
  --changes work/guardrails/changes.yaml \
  --agent exception_triage --task-class rejection_resolution \
  --level L2 --approver pm@example.com --reason "Whitelisted classes only." \
  --role pm
```

`--changes` is the same log `astra-agents guardrails` itself reads and writes — this command
appends to it in the identical shape, so either tool can read what the other wrote. A blank
`--reason` or `--approver` is refused outright, nothing written.

A change to L3 additionally needs `--acceptance-rate`, `--sample-size` and `--window` together:

```bash
astra-control autonomy-admin set-level \
  --changes work/guardrails/changes.yaml \
  --agent exception_triage --task-class rejection_resolution \
  --level L3 --approver pm@example.com --reason "Cleared threshold." \
  --acceptance-rate 0.97 --sample-size 120 --window "trailing 90 days" \
  --role pm
```

Refused outright — nothing written — if the sample is below 20 decisions or the acceptance rate
is below 80%.

## Showing current levels

```bash
astra-control autonomy-admin show-levels --changes work/guardrails/changes.yaml
```

One row per (agent, task class): its own current level (the most recent entry — none recorded
means L0), who approved it, why, and when.

## The self-healing whitelist

```bash
astra-control autonomy-admin show-whitelist --rejections domains/custodial/rejections.yaml --root .
```

Every code with `auto_resolve: true` in the domain pack's own real rejection taxonomy.

```bash
astra-control autonomy-admin request-whitelist-change \
  --requests work/whitelist-requests.yaml \
  --code FIELD_CODE_UNKNOWN --requested-by pm@example.com \
  --reason "High volume, low risk." \
  --rejections domains/custodial/rejections.yaml --root . \
  --role pm
```

**This never edits `--rejections` itself.** It logs a request; a steward applies it by hand,
adding `auto_resolve: true` to the real taxonomy file directly (`ADR 0048`'s own stated
boundary — this is a human decision, not an automated one). Pass `--remove` to request taking a
code off the whitelist instead. Giving `--rejections` validates the code is real and the change
is not a no-op before logging; omit it and the request is still logged, unchecked.

```bash
astra-control autonomy-admin show-whitelist-requests --requests work/whitelist-requests.yaml
```

## Deciding

- **L3 refused with "at least 20 decisions"/"acceptance rate >= 80%"**: the evidence given does
  not clear the bar — gather more decisions or a stronger track record before trying again; this
  command will not be talked into it.
- **`request-whitelist-change` says "is not a code"**: `--code` does not match any real code in
  the given `--rejections` file — check spelling against `show-whitelist`'s own output.
- **`request-whitelist-change` says "is already whitelisted"**: the request is a no-op against the
  real taxonomy as it stands today — nothing to request.

## Notes

- `autonomy-admin show-levels|show-whitelist|show-whitelist-requests` take the same optional
  `--role` every command in this plane does and are reads, available to every role. `set-level`
  and `request-whitelist-change` are granted to PM alone — the product spec's own persona table
  attributes "agent autonomy levels" to the delivery lead, not a role named "architect" (ADR
  0068).
- No real committed guardrails changes log exists anywhere in this repository yet — point
  `--changes` at a real one once `astra-agents guardrails` (or this command) has written one.
