# ADR 0042: The Profiler reuses the reference parser instead of a second, parallel reading of the file

Date: 2026-09-13
Status: Accepted
Story: S5.2.1 Profiler: build and evaluate (E5, F5.2, WBS 2.5.3, 2.5.4)

## Context

The Profiler reads sample files for types, nulls, value sets and record types, and flags drift against the spec — the second onboarding agent, and the first with no model in the loop: profiling a sample file against positions and pictures the spec already declares is exactly the kind of deterministic, mechanical work the product's own principle reserves for code, not an agent call. The product spec's guardrail for it is "read-only; samples only" — it never writes into the registry, never touches a production table, and takes only a sample file already on disk.

## Decision

1. **The agent does not parse the sample file itself.** `astra_knowledge.patterns.fixed_width.parse_fixed_width(spec, lines)` — the same reference parser generation's rendered SQL must agree with row for row, and the one Registry and verification code already exercise — already turns every line into typed field values, a record-type count per label, and a `RowProblem` wherever a field's raw characters didn't fit the type the spec declares for it. Building a second, agent-specific reader of the same spec would risk it disagreeing with the one everything else in the factory is held to; `astra_agents.profiler` is an aggregation over that parser's own output, not a competing implementation of it.

2. **"Observed type disagrees with the spec" is read off `convert()`'s own problems, not a separate type-inference heuristic.** Every field of every parsed row already went through `astra_knowledge.patterns.values.convert()` against the spec's declared type; a field where that conversion failed on at least one row (`FIELD_NOT_NUMERIC`, `FIELD_DATE_INVALID`, `FIELD_CODE_UNKNOWN`, and so on) is precisely a field whose observed data does not match what the spec says it should be. `FIELD_REQUIRED_BLANK` is excluded from this count on purpose: a required field left blank is a completeness problem, not a shape one, and conflating the two would flag a field for being empty rather than for looking like the wrong kind of value. This keeps the drift signal exactly aligned with the parser everything else already trusts, instead of a fresh set of rules that could disagree with it.

3. **The output is a profile for a person to read, not a spec change or a DQ rule.** `run` writes `report.md` (record-type counts, per-field null rate/distinct count/top values, which fields are flagged with a citation back to the offending line, and any file- or record-level problem — a missing header, a line no record type matches) and `report.json` under a working directory (`work/profiler/<spec id>/<spec version>/` by default), the same draft-not-registry shape Spec Reader already established. Nothing here is promoted automatically; "findings confirmed by reviewer" is the product spec's own metric for this agent.

4. **A committed 750-character example fixture stands in for a private layout document.** The backlog's acceptance criterion asks for "a profile produced for a 750-char fixed-width sample" — the real documents this size are Pershing's own layout PDFs, present only on one machine and never committed. `agents/examples/profiler/specs/profiler_demo/2026-01-01.yaml` (a fabricated, illustrative spec, `record_length: 750`, following the same header/detail/trailer shape as `specs/pershing_gcus`) and `agents/examples/profiler/sample.dat` (seven 750-character lines) give the story a real, reproducible fixture instead: one line has a non-numeric `quantity`, another an undeclared `security_type` code, both there on purpose to prove the flagging behavior end to end. A test (`test_example_spec_and_sample_satisfy_the_story_acceptance_criteria`) asserts both acceptance criteria directly against this fixture.

5. **No gold set, no accuracy threshold.** Unlike Spec Reader's "field-level accuracy ≥ [T]" (a model call, so an accuracy metric makes sense), the Profiler's two acceptance criteria are behavioral, not statistical: a profile is produced, and a mismatched field is flagged. `astra_verification.agent_eval` (S4.3.4) is still the harness this agent would plug into once real reviewed samples exist, but building a gold set for a deterministic function whose own reference implementation (`parse_fixed_width`) is already unit-tested would be testing the test, not the agent.

## Consequences

- A delimited-format spec is out of scope today; `profile_lines` raises a clear `ProfilerError` rather than guessing at a delimited parse, the same way `parse_fixed_width` itself refuses a non-`fixed_width` spec. Extending to delimited files is a matter of calling the delimited pattern instead, once one exists.
- Key-candidate detection (named in the product spec's table row for this agent but not in the backlog's own acceptance criteria) is not built here; distinct-value counts are already in the profile if a later story wants to add it.
- Because the Profiler is pure aggregation over `astra_knowledge`, adding a delimited or a nested pattern later needs no change to `astra_agents.profiler` beyond calling that pattern's own parser — the field-level shape (`ParsedFile.rows`, `.metadata`, `.problems`) is already pattern-agnostic.
