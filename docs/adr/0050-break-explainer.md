# ADR 0050: Break Explainer classifies a cause from the mapping's own structure and never fabricates a rule id

Date: 2026-09-13
Status: Accepted
Story: S5.10.1 Parity / Break Explainer: build and evaluate (E5, F5.10, WBS 2.5.20, 2.5.21)

## Context

A dual-run difference — the same file, run through the legacy Loader and the new lakehouse pipeline, disagreeing on one field of one row — already has a finder: `astra_verification.parity.compare_rows` (S4.2.1, ADR 0033) already returns exactly which row, which field, and both values. What it does not say is *why*. Break Explainer's whole job is that one missing piece: classify a cause and cite the rule, from what the config already declares, never a guess.

## Decision

1. **Nothing here re-finds a difference.** `compare_rows` is called as-is; `Explanation` is built directly from its own `RowMismatch`/`FieldMismatch`. Reimplementing row comparison would risk it disagreeing with the one the real parity report already uses — the same reason Rule Recovery reuses `astra_knowledge.rules.Rule` instead of a second rule format, and DQ Generator's rules validate against the real `config-v0.schema.json`.

2. **The rule citation reuses the compiler's own resolution, not a second lookup.** `astra_data.compiler.compile_config` already resolves every mapping's `rule` reference to a real `astra_knowledge.rules.Rule` object (S3.1.1); `field_mappings(config)` is a one-pass index from lakehouse column name to that already-resolved `CompiledMapping`, not a fresh parse of the raw config dict. A field with no rule in its mapping gets `rule_id: null` — never invented, proven directly: the real `PRICE` mapping has no rule, and the explanation for it says so.

3. **Cause is read off the mapping's own structure, in a fixed order, not guessed:**
   - **`resolution`** when the lakehouse column is one of `astra_data.compiler`'s own `ACCOUNT_COLUMNS`/`SECURITY_COLUMNS`/`TRANSACTION_CODE_COLUMNS` — checked *first*, regardless of whether the column also has a mapping. This ordering is not incidental: `CUSTODIAN_SECURITY_ID` is both a real, direct mapping (from `cusip`, no transform) in the committed `pershing_position.yaml` *and* a resolution column; checking mapping presence first would have misclassified it as `unexplained` for looking like a plain, unremarkable copy. A test proves this exact case does not get shadowed.
   - **`transform`** when the mapping applies one (`signed_implied_decimal`, `implied_decimal`, ...) — two independent implementations of the same transform can round or scale differently; this is a real, structural possibility, not a stretch.
   - **`unmapped`** when the field has no mapping in this config at all — the real example is `MARKET_VALUE`, which `configs/examples/pershing_position.yaml` never maps (presumably computed downstream); this agent does not trace a computation it was never given.
   - **`unexplained`** — a direct, untransformed, non-resolution mapping still differing. Nothing in the config explains this, so it is reported as unexplained rather than dressed up as an answer. This is exactly why the story's own acceptance criterion is "≥[T]%", not 100%: a genuine anomaly should surface as one, not be quietly counted as "explained."

4. **The gold set is real, not illustrative — proven against the real config, the real parity mapping and the real rule catalog.** `agents/break_explainer/` scores against `configs/examples/pershing_position.yaml`, `golden/pershing/parity.yaml` and the real rule catalog, the same "real gold set" shape Pattern Matcher's and Test Generator's own examples already use. What is illustrative is only the *row data* (`agents/examples/break_explainer/legacy_rows.csv`/`lakehouse_rows.csv`) standing in for a real dual-run: no captured golden dataset with real recorded differences exists in this repository (`astra-verify golden capture` has never been run here), the same situation Exception Triage's exception rows and Test Generator's synthetic files were already in.

5. **No numeric `[T]` threshold is hard-coded into the CLI's own exit code.** Exit 1 fires whenever any difference is unexplained (the same "not 100% of this agent's own completeness metric" gate DQ Generator, Rule Recovery and Test Generator already use), not against a specific percentage — the backlog's own `[T]` is a client-supplied number a person or a CI wrapper decides on, not something this agent should invent a default for.

## Consequences

- `resolution`, `transform` and `unmapped` are real, checkable structural signals; `unexplained` is deliberately the agent's honest floor, not a bug to eliminate — a real engagement's own `[T]` should be measured against how often genuine anomalies (not explainable ones) actually occur, not assumed to be zero.
- A "fix suggestion or legacy defect" call (the product spec agent table's own richer description of this row) is not built here: judging whether a difference is a factory bug, a legacy defect worth preserving, or something else needs more context (a steward's own history with this custodian, prior triage decisions) than a single config can give. This agent explains; a person still decides what, if anything, to do about it — "Reports only" is this story's own guardrail, matching the product spec's row for it exactly.
- `agents` now depends on `astra-verification` in addition to `astra-data` — the second cross-plane dependency an agent has taken (ADR 0045's own consequences noted the first), mirrored in `agents/pyproject.toml`, `.github/workflows/ci.yml`'s `agents` job, and `agents/pyproject.toml`'s `pythonpath`.
