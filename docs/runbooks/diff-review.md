# Runbook: review a config change

Story S6.1.3 (ADR 0056). A diff of two config versions — changed fields, rules touched with a
real citation, and every other custodian a touched rule also affects. No credentials: reads the
spec registry, rule catalog and domain packs already on disk, and reuses
`astra_verification.replay.config_diff` and `astra_knowledge.rules.lineage` directly rather than
computing either itself.

## Running it

```bash
astra-control diff-review run \
  --old configs/pershing/pershing_position.yaml.old \
  --new configs/pershing/pershing_position.yaml \
  --other-config configs/fidelity/fidelity_position.yaml \
  --specs specs --rules rules --domains domains
```

`--old`/`--new` are the two config versions to compare — typically a checked-out earlier revision
and the working copy. `--other-config` (repeatable) names every other config to check for the
same rule references; a rule touched by this diff that no other config references shows "none
found" honestly rather than an empty-looking blank. Writes `report.md` next to `report.json`
under `work/diff-review/` by default.

Read the report:

1. **Changed fields** — every mapping, `dq_rules` entry, rule reference and resolution part that
   differs, with a plain description of what changed.
2. **Rules touched — citation and impact** — only the rules this diff actually added, removed or
   changed the reference/status/text of. Each row: the real citation text, an `Open` reference
   (`specs/<id>/<version>.yaml#page=N` for a spec citation, `<file>:<line>` for a code citation —
   AC2), and every custodian (from `--other-config`, plus this diff's own) whose config also
   references that rule.

## Deciding

- **A rule shows several affected custodians**: this change is not scoped to the one config being
  reviewed — read the citation, confirm the rule's own text or status change is actually correct
  for every custodian listed, not only the one this diff is nominally about.
- **"None found" under affected custodians**: either the rule is genuinely scoped to this one
  custodian, or no other config was passed with `--other-config` — the second is a real
  possibility, not a proof of the first; pass every config that plausibly shares this source's
  spec or pattern before trusting "none found."
- **A field shows `kind: changed` but the before/after text looks identical**: this is
  `config_diff`'s own known quirk (ADR 0056 point 5, not something this tool works around) — the
  mapping's rule attribution changed even though its rendered source/transform text did not; the
  "Rules touched" table below it is where the real change is described.

## Notes

- `control/examples/diff_review/before/pershing_position.yaml` is an earlier version of the real,
  committed `configs/examples/pershing_position.yaml` — the "new" side of the example is that real
  file itself. `fidelity_position.yaml` is a clearly labeled illustrative second custodian sharing
  the same real spec and rule; no real Fidelity source exists in this repository.
- `diff-review run` never runs a verification tool or an agent itself; it only compiles the two
  configs given and reads the registry, catalog and domain packs already on disk.
