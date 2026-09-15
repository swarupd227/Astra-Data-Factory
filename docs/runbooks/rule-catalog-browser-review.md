# Runbook: browse and confirm the rule catalog

Story S6.3.5 (ADR 0061). Every recovered rule beside its class and citation, filtered by status,
custodian or rejection code, confirmed, rejected or marked a legacy defect with a comment. Every
status change goes through the same `astra_knowledge.rules.set_status` a rule catalog file already
uses — this is a screen over it, not a second way to change a rule.

## Browsing

```bash
astra-control rule-review show --rules rules
```

One row per rule: id, status, class, text, its citation and an openable reference
(`specs/<id>/<version>.yaml#page=N` for a spec citation, `<file>:<line>` for a legacy code
citation), and the custodians it applies to. Filter with `--status`, `--custodian` and
`--rejection-code`; combine as many as needed.

```bash
astra-control rule-review show --rules rules --status recovered
astra-control rule-review show --rules rules --custodian pershing
astra-control rule-review show --rules rules --rejection-code R017
```

`--rejection-code` matches a rule's own `tags` entry shaped `rejection-<code>` — the only place a
rejection code lives in a rule today (module docstring). No committed rule carries one yet, so
this filter finds nothing until Rule Recovery or a steward tags one; that is a true "no matches,"
not a broken filter.

## Confirming, rejecting, marking a legacy defect

```bash
astra-control rule-review set-status \
  --rules rules --id pershing_gcus.refresh_mode \
  --status confirmed --by steward@example.com \
  --note "Matches the sample file's header record."
```

`--status` is one of `confirmed`, `rejected`, `legacy_defect` — AC2's own three words; reverting a
rule to `recovered` is not something this command does. Who, when and the comment are recorded on
the rule's own file, the same history a rule already carries — nothing new to look at, no second
log. Refused outright (exit 2, nothing written) if the rule is already at that status, `--by` is
blank, or the rule id is not in the catalog.

## Bulk confirm

```bash
astra-control rule-review bulk-confirm \
  --rules rules --id pershing_gcus.quantity_sign \
  --by steward@example.com --note "Confirmed across both accounts."
```

Confirms the named rule and every other rule in the catalog with the exact same text (this
module's own definition of "identical" — ADR 0061, point 4). Each rule's own confirm attempt is
independent: one rule already confirmed does not stop the others in the same batch. Exit 0 when
every rule in the batch confirmed, 1 when at least one did not — read the printed table, it names
which and why, nothing is dropped silently.

## Deciding

- **A rule shows no citation link, or the link 404s**: the rule's own citation may name no
  document (falls back to a generic label, not a real file); the recovery step (spec reader or
  rule recovery agent) is where that is fixed, not this screen.
- **`--rejection-code` finds nothing**: that is the honest state of the catalog today — no rule is
  tagged yet, not a bug in the filter (ADR 0061).
- **Bulk confirm exits 1**: check which rule failed and why in the printed table — usually a rule
  in the batch was already at the target status, which is refused, not silently skipped.

## Notes

- Every example here is real: `rules/pershing_gcus/quantity_sign.yaml` and `.../refresh_mode.yaml`,
  `rules/drip_transaction_example/drip_split.yaml` and `.../cancel_correct.yaml` are the four rules
  actually committed to this repository.
- `rule-review show|set-status|bulk-confirm` take the same optional `--role` every other command in
  this plane does (ADR 0057). `show` is a read, available to every role; `set-status` and
  `bulk-confirm` are steward's own first write actions (ADR 0061).
- Testing a status change never runs against the committed `rules/` directory itself — `set_status`
  really rewrites the rule's file — always point `--rules` at a private copy when trying this out.
