# Runbook: render a source's doc

Story S5.12.1 (ADR 0052). The Docs & Runbook Writer renders one doc per source config under `docs/runbooks/sources/`, straight from the config, its spec, its domain pack's rejection taxonomy and its rule catalog — the same render-and-check shape `astra-spec cdm render` already uses for the canonical model. Deterministic: no model call, no credentials, nothing to approve.

## Running it

```bash
astra-agents docs-writer render configs --specs specs --rules rules --domains domains --out docs/runbooks/sources
```

`configs` (the default) is a directory; pass one or more specific config files instead to render only those. Every doc's file name is its config's own release bundle name (`astra_data.render.names.bundle_name`) — `pershing_position.yaml` renders `pershing-position.md`, found next to `releases/pershing-position/`.

Read the doc:

1. **Delivery and alerting** — the source's own cutoff, timezone, business days and expected file patterns, and the severity of its `late` and `task_failure` alerts (ADR 0007).
2. **Chaos drill** — what each of the drill's four real scenarios (`docs/runbooks/chaos-drill.md`) means for this source: what it detects, the taxonomy code (or dq_rule) that names it, and how to resolve it. This is the source-specific companion the generic chaos-drill runbook does not have.
3. **Data quality rules, exceptions and rules** — this source's own `dq_rules`, the rejection codes its `resolution` block can actually raise, and the rule catalog entries its mappings cite.

## Checking it is current

```bash
astra-agents docs-writer render configs --specs specs --rules rules --domains domains --out docs/runbooks/sources --check
```

Exits 1 and lists every doc that is missing, stale (the source changed since it was last rendered) or orphaned (its config no longer exists); exits 0 when every doc matches what its source would render right now. CI runs this on every pull request, in the `agents` job, alongside `astra-spec cdm render --check`; a pull request that changes a config, a spec, the taxonomy or a cited rule without re-running `docs-writer render` fails here.

## Deciding

- **`--check` passes**: nothing to do — the committed docs already match their sources.
- **`--check` fails, "stale" or "not rendered"**: run `docs-writer render` (no `--check`) and commit the result. This is a render, not a draft — there is nothing to review before committing, the same as `astra-spec cdm render`.
- **`--check` fails, "no longer produced"**: a config was removed or renamed; running `docs-writer render` deletes the orphaned file.

## Notes

- Unlike every other agent in this plane, this one writes straight into `docs/`, not `work/docs-writer/` — a doc rendered from already-approved registry data has nothing left for a person to approve (ADR 0052). Its CLI exit codes follow `astra-spec cdm render`'s contract, not the other eleven agents': plain `render` exits 0 once written; `--check` exits 1 only when something is missing, stale or orphaned.
- The four chaos scenarios and their taxonomy codes are the same fixed set `astra_verification.chaos` actually runs (ADR 0038) — `late` and a source with no `control_total` dq_rule are both documented honestly rather than guessed at.
- This file documents the agent itself, hand-written like every other file under `docs/runbooks/`; the docs this agent generates live under `docs/runbooks/sources/`.
