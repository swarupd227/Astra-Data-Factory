# Runbook: review an agent's draft

Story S6.3.6 (ADR 0062). A draft agent output shown beside its own reasoning and citation, edited
with the original kept for comparison, and accepted or rejected straight into that agent's own
evaluation set. Today's one concrete integration is Rule Recovery's own draft rules.

## Showing a draft

```bash
astra-control agent-review show \
  --draft work/rule-recovery/rules/pershing_loader/unknown_record_type_rejected.yaml \
  --tier medium \
  --input "agents/examples/rule_recovery/java/Splitter.java:33-47"
```

`--draft` is a draft rule file Rule Recovery already wrote (real `rules/<group>/<name>.yaml`
shape, status always `recovered`). `--input` is where the reviewed evidence lives — usually the
draft's own citation span, given explicitly since the gold set needs it recorded. Prints the
draft's own text (reasoning) beside its citation (source evidence, AC1) and what accepting it
would record in the evaluation set.

## Editing (writes nothing)

```bash
astra-control agent-review edit \
  --draft work/rule-recovery/rules/pershing_loader/unknown_record_type_rejected.yaml \
  --tier medium --input "Splitter.java:33-47" \
  --text "A line whose record type code is not HDR, DTL or TRL is rejected before the Loader ever sees it."
```

Diffs the corrected text (or `--citation-file`/`--citation-line`/`--citation-end-line`/
`--citation-repository` for a corrected citation) against the original — the original is always
kept, never overwritten (AC2). Nothing is written to disk by this command; it only shows the
comparison. Given no `--text` or `--citation-*`, prints "No changes."

## Accepting or rejecting

```bash
astra-control agent-review accept \
  --draft work/rule-recovery/rules/pershing_loader/unknown_record_type_rejected.yaml \
  --tier medium --input "Splitter.java:33-47" \
  --gold-set agents/rule_recovery/eval.yaml --case-id unknown_record_type_review \
  --role steward
```

Accept appends a new case to the given `--gold-set` file: `input` is the reviewed evidence,
`expected` is `rule:<id>` for the (possibly edited-in-place with `--text`/`--citation-*`) draft's
own rule id — recording that the agent should recover this rule id from this input (AC3). Reject
(`agent-review reject`, same arguments minus the edit flags) appends a case with an empty
`expected` — the agent should have produced nothing here. Both are refused outright, nothing
written, if the case id already exists, the gold set is for a different agent, or the given tier
has no threshold in that gold set yet.

## Deciding

- **`--gold-set` refuses with "no threshold"**: a tier needs a threshold set from a real pilot
  baseline before a case can use it — this command never invents one; add the threshold to the
  gold set's own `thresholds:` section first, from real measured numbers, not here.
- **Editing text doesn't change what gets recorded on accept**: correct — `expected` tracks which
  rule id should be recovered from the input, not the literal wording (ADR 0062, point 3). If a
  correction means a genuinely different rule id is the right answer, edit that into the draft's
  own id upstream (or reject this one and accept a corrected draft separately), rather than
  expecting a text edit alone to change scoring.
- **Rejecting a draft recovered from a broad citation span**: give a narrower `--input` yourself —
  an empty `expected` over a wide span would wrongly claim the agent should recover nothing at all
  from that whole span (ADR 0062, point 3).

## Notes

- Accept/reject record no "who, when" of their own — the gold set schema's own cases are
  `id`/`tier`/`input`/`expected` only (`additionalProperties: false`), unlike a rule's own
  history (S6.3.5). Who reviewed a draft is whoever ran the command, same as any other CLI action
  in this plane without its own audit field.
- No committed gold set exists yet for `rule_recovery` (only `agents/pattern_matcher`,
  `agents/break_explainer` and `agents/test_generator` have real ones) — a first one needs real
  pilot-measured thresholds this repository does not have yet, not fabricated ones from this tool.
- `agent-review show|edit|accept|reject` take the same optional `--role` every command in this
  plane does. `accept`/`reject` are granted to both steward and BSA — the story's own actor.
