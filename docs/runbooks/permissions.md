# Runbook: roles and permissions

Story S6.3.1 (ADR 0057). Six roles — steward, BSA, engineer, ops, PM, auditor — each one's
allowed actions across the factory board, config studio and diff review, enforced at the CLI
boundary. No live identity provider: `permissions resolve` maps already-authenticated claims to
a role through a platform administrator's own group mapping; the SSO redirect and token
validation themselves need a live IdP this tool cannot exercise.

## Seeing what a role can do

```bash
astra-control permissions show                # every role
astra-control permissions show --role auditor  # just one
```

Every action is `read` or `write`. Every role can take every read action. `auditor` shows `no`
for every write — the whole point of AC3, visible directly in the table, not just asserted.

## Configuring the identity provider's group mapping

```yaml
# role-mapping.yaml
groups:
  Astra-Stewards: steward
  Astra-BSA: bsa
  Astra-Engineers: engineer
  Astra-Ops: ops
  Astra-PM: pm
  Astra-Auditors: auditor
```

A platform administrator writes this once per client, naming *their* identity provider's own
group names on the left. Resolve a person's claims against it:

```bash
astra-control permissions resolve --mapping role-mapping.yaml \
  --email steward@example.com --name "A Steward" --group Astra-Stewards
```

Refused (exit 2) when no group matches (nobody falls back to a default role) or when the groups
given map to more than one *different* role — an ambiguous mapping is fixed by the administrator,
never guessed at silently.

## Enforcing a role on a command

Every write command (`board add|move|set-wip-limit`, `config-studio start|advance|
request-promotion`) and every read command (`board show`, `config-studio show-requests`,
`diff-review run`) takes an optional `--role`:

```bash
astra-control board add --board board.yaml --custodian pershing --stream envestnet-custodial --role auditor
# error: auditor is not allowed to board.add
```

```bash
astra-control board add --board board.yaml --custodian pershing --stream envestnet-custodial --role ops
# added: pershing to envestnet-custodial, at profile
```

`--role` is optional everywhere: omit it and every command behaves exactly as it did before this
story — every prior test for board, config studio and diff review is untouched.

## Deciding

- **A command is refused, "is not allowed to..."**: check `permissions show --role <role>` for
  what that role can actually do; either use the right role or route the request to someone who
  has it.
- **`permissions resolve` is refused, "none of [...] maps to a role"**: the identity provider's
  own group for this person is missing from `role-mapping.yaml` — a platform administrator adds
  it, never assumed.
- **`permissions resolve` is refused, "map to more than one role"**: the mapping itself is
  ambiguous for this person (two of their groups point at different roles) — fix the mapping, not
  the person's group membership.

## Notes

- `control/examples/role-mapping.yaml` uses plausible, illustrative group names — no real
  Envestnet identity provider groups are known to this repository.
- Enforcement lives at the CLI boundary (`_check()` in `astra_control.cli`), not inside
  `board.move` or `config_studio.request_promotion` themselves — the same shape config studio's
  own `--guardrails` flag already uses, so this story needed no further changes to either
  module's own functions.
- No rendered sign-in screen or live SSO redirect exists yet; this is the claims-to-role mapping
  and its enforcement, the piece that is genuinely this platform's own to build.
