# ADR 0049: Drift Watcher proposes a delta and never has a code path that writes a spec

Date: 2026-09-13
Status: Accepted
Story: S5.9.1 Drift Watcher: build and evaluate (E5, F5.9, WBS 2.5.18, 2.5.19)

## Context

A custodian's file can drift from its own spec quietly — a trailing field appended, a new code introduced — and the first sign is often a pile of rejected records well after the fact. The product spec's own agent table already names the shape: *"New files vs spec/config → Drift detection and proposed config delta → Never touches production config → Drift caught before Silver."* Its own test methodology is stated plainly too: *"known layout changes are injected into historical files; the Drift Watcher must catch them"* — this ADR and this agent's test suite follow that literally, not just the acceptance criteria's words.

## Decision

1. **"Config delta" means the Source Spec, not `config-v0.schema.json`'s own config.** Record length and code sets are properties the spec declares (`file.record_length`, `Field.codes`); a source config (mappings, resolution, `dq_rules`) has neither. The backlog's own "config delta" is read here as shorthand for a proposed change to the spec — the same clarifying move ADR 0048 made mapping the backlog's `NEW_SECURITY`/`MISSING_TX_CODE` shorthand onto the real taxonomy's own code names.

2. **The delta is a structured list of changes, not a re-rendered spec file.** `DriftFinding` names exactly what would change (`path`, `current`, `proposed`, the evidence behind it) rather than producing a full candidate `specs/<id>/<version>.yaml` a person could `git mv` into place. This is the same scope line ADR 0045 already drew for CDM change requests: a structured request is precise and directly actionable without needing a new "spec object rendered back to YAML" capability this repository has never needed before (every existing renderer — `render_rule`, `cdm.render_ddl` — renders a different kind of object; none renders a `SourceSpec`). Building one is a reasonable next step once a real engagement needs it, not required to satisfy this story.

3. **Both detectors reuse the parser's own record-type matching, not a hand-rolled scan.** `detect_code_drift` calls `astra_knowledge.patterns.fixed_width.record_type_of` — the exact function `parse_fixed_width` itself uses — so a field is read from the position its own record type declares even after the file has grown a few characters longer, the same way the real parser would locate it. This assumes the drift is additive at the end of the line (a custodian appending a new trailing field, the common case); a field inserted in the middle of a record would shift every position after it and needs a fundamentally different alignment approach this story does not attempt.

4. **A candidate new length or code must be a majority pattern, not one bad row, before it is proposed.** `RECORD_LENGTH_MAJORITY` (half the sample) and `CODE_MIN_OCCURRENCES` (2) are round numbers, not measured ones — the same honesty ADR 0043's tier thresholds and DQ Generator's severity default already used. One truncated line or one corrupted value is real-world noise a spec should not be changed over; a pattern repeating across the sample is what "drift" means here.

5. **The guardrail — never modifies production config — is architectural, not a runtime check.** No function in this module ever opens a spec file for writing; `generate` and `run` only ever return a `DriftDraft`, and `write_draft` writes under a working directory, never near `specs/`. A test proves this directly: the real spec file this agent's own example reads from is hashed before and after a run that actually finds drift, and the hash is unchanged.

6. **The example is drift injected into a real spec, exactly as the product spec's own test methodology describes.** `agents/examples/drift_watcher/` is the real, already-committed `pershing_gcus` spec against two committed samples: `baseline_sample.dat` (clean, 120 characters, every code declared) and `drifted_sample.dat` — the same conceptual delivery with two injected changes: five characters appended to every line, and `security_type` on two of four detail lines changed to `CD`, a code the spec has never declared. Both are entirely synthetic values, not real custodian data.

## Consequences

- No eval gold set: like DQ Generator, Rule Recovery and Exception Triage, this story's acceptance criteria are behavioral (an injected record-length change and an injected code-set change are both detected; the spec is never modified), not a precision/recall percentage. `test_the_story_acceptance_criteria_are_satisfied` proves both directly against the real spec and the injected sample.
- A mid-record field insertion, a delimited-format spec, and a genuinely new record type appearing in the file are all out of scope for this first version — each needs an alignment or classification approach this story's two narrow, well-evidenced detectors do not attempt. Detecting them is a natural extension once a real engagement's drift looks like one of these, not a sign this design was wrong for what it does cover.
- Rendering the delta as a full candidate spec file — so a steward could review a real diff instead of a structured list — is the natural next refinement once a `SourceSpec`-to-YAML renderer exists for some other reason; this story does not need to be the first to build one.
