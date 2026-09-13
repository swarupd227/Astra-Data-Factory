# Runbook: draft a config from a Source Spec and the canonical model

Story S5.5.1 (ADR 0045). The Modeler maps a Source Spec's fields to the domain pack's canonical model, proposes resolution parameters and a conservative set of DQ suggestions, and drafts what will become a real config. It never writes into `configs/`, never writes into the domain pack, and never marks a proposed rule confirmed.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The spec | Data engineer | A Source Spec is in the registry (`specs/<id>/<version>.yaml`), ideally already classified with a family and tier (Pattern Matcher). |
| 2. The domain pack | Data engineer | The target domain pack is known (`domains/custodial`). |
| 3. Credentials | Agent engineer | `ANTHROPIC_API_KEY` is set; `pip install "astra-agents[llm]"`. Check it with `astra-agents spec-reader test-connection` — the same key and account serve every agent that calls the Anthropic API. |
| 4. Who owns a new rule | Steward | If the mapping needs a rule not yet in the catalog, who would confirm or reject it is known. |

## Running it

```bash
astra-agents modeler run \
  --spec specs/pershing_gcus/2017-07-25.yaml --domain domains/custodial --rules rules \
  --custodian pershing --owner-name "Data steward, custodial" --owner-email steward@example.com
```

Writes `report.md` next to `report.json` under `work/modeler/<spec id>/<spec version>/`; any new rule the draft proposes is also written as a real `rules/<group>/<name>.yaml`-shaped file in the same place.

Read the report in order:

1. **Mappings** — target, source, transform and governing rule for every field the model found a home for. A row marked invalid names why (a target outside the model, an unparseable transform, a reference to a rule that is not in the catalog).
2. **Resolution / DQ suggestions** — account and security resolution parameters, and a small number of high-confidence `not_null`/`unique` suggestions. This is a head start, not the full DQ suite — DQ Generator (F5.6) owns the rest.
3. **CDM change requests — never applied, always a request** — every field with no real home in the model today. Breaking is marked in bold; nothing here has touched `domains/<pack>/cdm/` on disk. A steward decides whether to add the column for real (a new model version, a migration note if it breaks) or push back on the mapping instead.
4. **Unmapped** — fields the model would not confidently map at all.

## Deciding

- **A valid mapping**: reasonable to accept as the start of a real config; a person still reviews it before it is ever promoted, exactly like every other agent's draft in this factory.
- **A rule tagged CONFIRM_WITH_LOADER**: read the report's Mappings table for the marker, then check the rule file's own citation against the spec — and, ideally, against a real sample file or the legacy Loader's actual behavior — before confirming it through `astra-spec rules set-status`. This tag means "the spec alone was not enough to be sure," not "this is wrong."
- **A CDM change request**: read the reason and the breaking classification. An additive request (an optional column) is a lighter decision than a breaking one (a required column, which needs a migration note and a major version bump); either way, this agent never makes the change itself.
- **Promoting a draft**: move the report's mappings, resolution and DQ suggestions into a real `configs/<custodian>/<id>.yaml` by hand, exactly as the real `configs/examples/pershing_position.yaml` is shaped; add `processing`, `delivery` and `alerts`, which this agent does not draft.

## Notes

- The real model call needs `ANTHROPIC_API_KEY`; there is no offline mode. `astra_agents.modeler.LlmClient` is the interface a test double implements instead.
- `agents/examples/modeler/` is the gold set for mapping precision/recall — illustrative, the same as Spec Reader's own example, because scoring it for real needs a live model run this session could not make.
- A large spec can produce a tool call big enough to hit the token budget, the same as every other agent here — pass `--max-tokens` with a higher number and re-run.
