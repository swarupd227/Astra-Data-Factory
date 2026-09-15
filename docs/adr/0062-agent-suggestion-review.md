# ADR 0062: Review reads a draft's own file, and scores which rule id, not its wording

Date: 2026-09-15
Status: Accepted
Story: S6.3.6 Agent suggestion review (E6, F6.3, WBS 2.6.18)

## Context

AC1 ("draft shown with the source evidence beside it," "reasoning and citations") needs a
concrete draft shape to render, not an abstract one. Of the Agents plane's draft-producing
agents, Rule Recovery is the cleanest fit: a rule's `text` is already a plain-language reasoning,
its `citation` is already a real `astra_knowledge.rules.Citation` naming a file and line of legacy
code (AC1's own "source file:line"), and a draft rule is always `status="recovered"` — the agent's
own tool schema has no `status` field at all, so a draft can never auto-confirm itself. The
Modeler is the second-best fit but uneven across its four artifact types (mappings, new rules, CDM
change requests, DQ suggestions); Pattern Matcher has a numeric confidence score but no citation;
DQ Generator has citations but no model reasoning at all (deterministic, no model in the loop).
This story's one concrete integration is Rule Recovery; a second agent's own review is a later
story's job to add, following the same shape.

AC3 ("accept / reject feeds the agent evaluation set automatically") names a mechanism that did
not exist: `astra_verification.agent_eval` (S4.3.4) only *loads* and *scores* a gold set
(`load_gold_set`, `score`), never writes one. Nothing in the product spec or backlog specifies
this mechanism further than Section 9's own "every human correction becomes a test case for the
agent that missed it" — the append behavior is this story's own design, not a reimplementation of
something already specified.

## Decision

1. **`astra_control.agent_review` never imports `astra_agents`.** It reads a draft rule the exact
   way `astra_control.rule_review` already reads a real catalog rule — `astra_knowledge.rules.
   load_rule_file` — because a draft Rule Recovery writes under `work/rule-recovery/` is already a
   real `rules/<group>/<name>.yaml`-shaped file. This is the same "no new agent import" boundary
   `astra_control.queue`'s own module docstring already established for reading an agent's report
   files rather than its Python objects (S6.3.2).

2. **`append_case` lives in `astra_verification.agent_eval`, not in `control/`** — the module that
   already owns `GoldSet`/`Case`/the schema validates the write too, the same "the owning module
   gets the mutation" shape `astra_knowledge.rules.set_status` already established (loader and
   mutator in the same module; `control/rule_review.py` calls it rather than reimplementing rule
   writing). `append_case` appends **textually** — reads the file, appends a new case block, writes
   it back — rather than reloading into a `GoldSet` and re-rendering the whole document, because
   `GoldSet` does not capture a file's own leading comment header (every real committed gold set
   has one) and a full reload-render round trip would silently drop it. The one risk this
   introduces — a malformed append, or the assumption that `cases:` is the file's last top-level
   key (true of every gold set committed today) turning out wrong — is caught by reloading and
   re-validating the file against the real schema immediately after writing, and rolling back to
   the original text if that reload fails. A case is refused outright, nothing written, when its
   id already exists (case ids are unique per file) or its own tier has no threshold in that gold
   set — thresholds are set from the pilot baseline, never invented by this function (the schema's
   own documented principle, already honored by `agents/pattern_matcher/eval.yaml`'s own comment:
   "thresholds are illustrative starting points, not a pilot-measured baseline").

3. **`expected`, for a Rule Recovery review, is a single new canonical item: `"rule:<id>"`** —
   naming which rule id should be recovered from the reviewed input, reusing the exact
   `"rule:<id>"` item shape `agents/break_explainer/eval.yaml`'s own real gold set already uses to
   cite a rule by id (`"rule:QUANTITY=pershing_gcus.quantity_sign"`), not invented from nothing.
   This tracks *which rule id*, never the literal wording of `text` — editing `text` (AC2) changes
   what a reviewer sees and compares, but never `expected`, matching the harness's own "canonical
   string items," never free text (`astra_verification.agent_eval`'s own module docstring). A
   reject records `expected=()` — "the agent should produce nothing here" — which is only honest
   when the reviewed input is as narrow as the draft's own citation span; a reviewer rejecting a
   draft recovered from a broad, multi-rule span gives a narrower `--input` themselves rather than
   relying on the draft's own citation. No committed gold set for `rule_recovery` exists yet (only
   `pattern_matcher`, `break_explainer` and `test_generator` have real ones); this story does not
   invent one, since a first gold set needs real pilot-measured thresholds this repository does
   not have — `agent-review accept/reject` work against whatever gold set a caller already has,
   refusing cleanly when none exists yet.

4. **AC2's diff is a small, purpose-built comparator over `ReviewItem`'s own fields, not a reuse
   of an existing one.** Neither `astra_verification.replay.config_diff` (needs two compiled
   configs) nor `astra_control.spec_viewer.compare` (needs two `SourceSpec` objects) operates on a
   rule's own text and citation — both are already narrow, shape-specific comparators by this
   plane's own convention (ADR 0060's own point 4, `citation_link` written fresh for the same
   reason). `Edited.diff()` compares `draft_text`, `citation_text` and `expected` field by field,
   naming exactly what changed; `edit_text`/`edit_citation` always keep the original `ReviewItem`
   on the `Edited` pair, satisfying AC2 directly — a reviewer sees both, not just the correction.

5. **Both writes — `agent-review.accept` and `agent-review.reject` — are granted to steward and
   BSA together**, the first action in `astra_control.permissions` given to two roles at once. The
   story's own actor is "steward or BSA" jointly, and the product spec already establishes this
   dual reviewership for a source's own promotion ("Steward reviews (simple tier: BSA reviews)",
   Section 7.1) — this is not a new pattern invented for this story, just the first write action
   to need it.

## Consequences

- `agent-review show|edit|accept|reject` take the same optional `--role` every command in this
  plane does; `show`/`edit` are reads, available to every role.
- Only Rule Recovery has a concrete adapter (`rule_recovery_item`) today. A second draft-producing
  agent's own review needs its own adapter function turning that agent's own draft file into a
  `ReviewItem` — the same small, per-agent function this module already has one of, not a change
  to `ReviewItem`, `Edited` or `accept`/`reject` themselves.
- No rendered review screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested draft-loading,
  diffing and gold-set-writing logic a screen would be built on top of.
