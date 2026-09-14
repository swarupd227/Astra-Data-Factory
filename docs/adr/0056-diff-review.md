# ADR 0056: Diff review adds impact and citation to a diff that already exists

Date: 2026-09-14
Status: Accepted
Story: S6.1.3 Diff review: build and evaluate (E6, F6.1, WBS 2.6.3)

## Context

Two of this story's three real components already existed before this story started. Comparing
two compiled configs field by field — mappings, dq_rules, rules, resolution — is exactly what
`astra_verification.replay.config_diff(old, new) -> ConfigDiff` (S4.1.3) already does, grouped
and described. Finding every config that references a given rule is exactly what
`astra_knowledge.rules.lineage(catalog, config_paths) -> Lineage` already does, built for the
rule catalog's own tooling. A citation is already a structured `astra_knowledge.rules.Citation`
— `kind` `"spec"`, `"code"` or `"document"`, with real page/line or file/line fields, not a
string to parse. This story's own job, once those three are set aside, is small and specific:
decide what "affected custodians" means for a config diff, and turn a citation into something a
person could actually open.

## Decision

1. **"Affected custodians" is scoped to rules, not every field.** A mapping's own target, source,
   transform or constant only ever affects the one custodian whose config file it is — nothing
   else in this repository reads that file. A *rule* is different: many configs across many
   custodians can reference the same rule id, so a rule gaining, losing, or changing status or
   text is the one kind of change with real cross-custodian impact. `review()` computes this by
   feeding every rule id the diff touches into `astra_knowledge.rules.lineage` against the new
   config plus whatever `other_configs` the caller names, not by inventing a second reverse index.

2. **Which rule ids a diff "touches" has one real subtlety, and it is resolved by precedence, not
   guesswork.** `config_diff` records two different kinds of rule-adjacent entries under two
   different group namings: a dedicated `"rule:<id>"` entry when the rule's own reference, status
   or text changed, and a mapping-attributed entry (grouped under the rule id itself, with no
   prefix) when a mapping's *own* attribution gained or lost that rule while everything else about
   the mapping stayed the same. Both can fire for the same rule id in the same diff — proven
   directly against the real `pershing_gcus.quantity_sign` example, where a mapping newly citing
   the rule produces both a `"changed"` mapping entry and an `"added"` `"rule:..."` entry for
   the same id. `_rule_ids_touched` treats the dedicated `"rule:<id>"` entry as authoritative
   (checked first) and the mapping-attributed one only as a fallback for a rule id no dedicated
   entry already covers — "added" is the honest answer for a reference that did not exist before,
   not "changed", which is what the mapping-level entry alone would suggest.

3. **A citation link is a real, openable reference, not a second copy of `Citation.text`.**
   `citation_link` reads the same structured fields `Citation.text` already renders as prose and
   turns them into something with an actual location: `specs/<id>/<version>.yaml#page=N` for a
   spec citation — the registry's own real file layout, not invented — and `<file>:<line>` for a
   code citation, already the exact syntax an editor or `git blame` accepts. This satisfies AC2
   exactly ("opens the spec page or code line") without a rendered Workbench screen to click
   through yet; the link is real and correct today, the click-through is later Control-plane work.

4. **A real bug this story's own tests caught: lineage's display paths are not filesystem
   paths.** `astra_knowledge.rules.lineage` returns config references as `display_path`-relative
   strings — correct for showing a person, wrong for reading a file back off disk when the
   process's own working directory is not the same as `root`. The first draft of `review()` read
   each matched config directly by that display string and silently found nothing (a caught
   `OSError`, not a crash) whenever `root` and the working directory differed — exactly the case
   every test in this repository that passes an explicit `root=REPO` triggers. Fixed by building
   the custodian lookup from the real `Path` objects `review()` already has, keyed by their own
   `display_path`, and never re-deriving a path from lineage's own display string. A dedicated
   test (`test_affected_custodians_includes_both_pershing_and_the_illustrative_fidelity`) pins
   this against the real example, not a synthetic case that could hide the same class of bug.

5. **No live simple-tier or multi-custodian example exists, so this story builds a small, real
   one.** `control/examples/diff_review/before/pershing_position.yaml` is an earlier version of
   the real, committed `configs/examples/pershing_position.yaml` — the "new" side of the diff is
   that real file itself, unduplicated; only the "old" side is new, and it differs from the real
   file in exactly one place (the quantity mapping's own rule citation), so the diff this example
   produces is genuine, not staged to look interesting. `fidelity_position.yaml` is a clearly
   labeled illustrative second custodian sharing the same real spec and rule — the product spec's
   own principle ("Patterns before instances") makes this a plausible, not a contrived, scenario.

## Consequences

- `astra_control.diff_review` adds `astra-knowledge`, `astra-data` and `astra-verification` as
  real dependencies of the Control plane — the same three planes `astra-agents` already depends
  on, added here for the same reason: reusing what those planes already built correctly rather
  than reimplementing it.
- Diff review still has no rendered Workbench screen — "side-by-side" today means a markdown
  table read top to bottom, not two panes a steward's eye moves between; that visual layout is
  later Control-plane work built on top of the same, already-tested `DiffReview` this story
  produces.
- `config_diff`'s own known quirk carries through unfixed: a mapping's rendered before/after
  description (`_origin`) does not mention its own rule attribution, so a mapping-level row can
  read as unchanged text even when `kind="changed"` because only its rule differs. This is
  `astra_verification.replay`'s own existing behavior, not introduced here; fixing it is a
  smaller, separate piece of work in that module, not this story's to take on.
