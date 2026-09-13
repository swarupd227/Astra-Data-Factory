# Runbook: assign a family, tier and pattern list

Story S5.3.1 (ADR 0043). The Pattern Matcher compares a spec with no family yet against every spec already in the registry, by detail-record shape, and assigns a family, a tier and a pattern list — or, when nothing matches closely enough, proposes a new pattern instead of guessing.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. A spec with no family | Agent engineer | A spec is in the registry (`specs/<id>/<version>.yaml`) with no `family` set — typically a Spec Reader draft, reviewed and moved into place, or an existing spec whose family was never assigned. `Registry.unclassified()` lists every spec in this state. |

No credentials, no deploy, no Snowflake connection: this reads the registry directory already on disk.

## Running it

```bash
astra-agents pattern-matcher run --registry specs
```

Classifies every spec the registry has no family for and writes `report.md` next to `report.json` under `work/pattern-matcher/`. To check one spec by name (already classified or not):

```bash
astra-agents pattern-matcher run --registry specs --id pershing_gcus --version 2017-07-25
```

Read the report in order:

1. **The assignment table** — family, tier and pattern(s) for every spec classified this run.
2. **Reuse candidates** — other specs already sharing the assigned family, for context on what this spec is reusing.
3. **Architect queue: new pattern proposals** — every spec no existing family matched closely enough to reuse, with the nearest known family and its similarity score. This is the whole "architect queue": there is no separate ticketing system, so this section of the report is what a solution architect reads to decide.

## Deciding

- **A confident family assignment**: usually fine to accept as-is; the reuse candidates listed are worth a glance to confirm the match makes sense.
- **A new pattern proposal**: an architect decides whether this is genuinely a new family (name it, and it becomes the anchor future specs of this shape reuse) or the threshold missed a real match (lower `--family-threshold` for this run and recheck, or assign the family by hand in the spec's YAML). Either way, editing the spec's `family` field is the human's decision this agent hands off to — the same "agent proposes, human approves" shape as everywhere else in this factory.
- **Tier**: a starting point, not a locked-in classification (ADR 0043's own consequences). If a spec's real onboarding complexity clearly does not match the tier reported, that is useful signal for recalibrating `tier_score`, not just a one-off override.

## Notes

- Family similarity looks only at detail records' field shapes (picture kind and declared type, never field names) — a custodian naming the same kind of field differently from another custodian is expected and does not affect the match.
- `agents/pattern_matcher/gold/` is this agent's real gold set — not an illustrative stand-in — built from the real `specs/` directory with every family hidden except one sibling; `astra-verify agent-eval score --gold agents/pattern_matcher/eval.yaml --predictions agents/pattern_matcher/predictions.yaml` scores it the same way any other agent's gold set is scored.
