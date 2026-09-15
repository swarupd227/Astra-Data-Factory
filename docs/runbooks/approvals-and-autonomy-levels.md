# Runbook: approve or reject a draft, with the proposer's own autonomy level shown

Story S6.2.1 (ADR 0070). A general approval record — who, when, the evidence seen, the agent's
own version if known, and the autonomy level it was operating at — so every change has an
accountable person. Opens feature F6.2.

## Approving

```bash
astra-control approvals approve \
  --log releases/pershing_gcus/approvals.yaml \
  --subject pershing_gcus_full --agent spec_reader --task-class spec_draft \
  --guardrails-changes work/guardrails/changes.yaml \
  --approved-by steward@example.com \
  --evidence-path work/spec-reader/pershing_gcus_full/2017-07-25/report.json \
  --agent-version claude-sonnet-5 \
  --comment "Layout matches the sample." \
  --role steward
```

`--evidence-path` is checked to exist — a real file the reviewer actually looked at, never a
fabricated reference. `--guardrails-changes`, when given, shows the proposing agent's own real
current autonomy level (`astra-control autonomy-admin show-levels` reads the same log); omitted,
the level shows as `L0`, the same default guardrails itself uses when nothing has ever been
recorded. `--agent-version` is honestly optional — nothing in this repository tracks one
automatically for most agents.

## Rejecting

```bash
astra-control approvals reject \
  --log releases/pershing_gcus/rejections.yaml \
  --subject pershing_gcus_full --agent spec_reader --task-class spec_draft \
  --rejected-by steward@example.com \
  --comment "The trailer record length looks wrong." \
  --draft-dir work/spec-reader/pershing_gcus_full/2017-07-25 \
  --role steward
```

`--comment` is required — a rejection with nothing said back is refused outright, nothing
written. `--draft-dir`, when given, gets a real `rejection.yaml` written into it, right alongside
the draft's own `report.json`/`report.md` — "returning the item to the agent" as a real file. No
agent's own CLI in this repository reads that file back today; this is the record a future agent
enhancement could pick up.

## Showing

```bash
astra-control approvals show \
  --approvals releases/pershing_gcus/approvals.yaml \
  --rejections releases/pershing_gcus/rejections.yaml
```

## Deciding

- **`error: evidence file not found`**: the path given to `--evidence-path` does not exist —
  point it at the real report/draft file the reviewer actually opened.
- **The autonomy level shows `L0` unexpectedly**: no `--guardrails-changes` was given, or the log
  given has no entry for this exact `(agent, task_class)` pair yet — check with `autonomy-admin
  show-levels` first.
- **`error: comment must be given`**: a rejection always needs one; an approval's own `--comment`
  is optional.

## Notes

- This is a new, general mechanism — it does not replace `rule-review set-status`, `agent-review
  accept/reject`, `drift-review approve`, or `autonomy-admin set-level`, each of which already has
  its own accountable-person record grounded in its own domain. Use this one for a draft with no
  dedicated review flow of its own.
- `approvals approve|reject|show` take the same optional `--role` every command in this plane
  does; `show` is a read, available to every role; `approve`/`reject` are granted to steward.
