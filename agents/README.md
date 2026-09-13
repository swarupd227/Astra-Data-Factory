# Agents plane

`astra-agents`: bounded agent workers. Today: the Spec Reader (S5.1.1, ADR 0041), the Profiler (S5.2.1, ADR 0042) and the Pattern Matcher (S5.3.1, ADR 0043). Every agent is scored against its own gold set by `astra-verify agent-eval` (S4.3.4, ADR 0040, product spec Section 11).

```bash
cd agents
python -m venv .venv && . .venv/bin/activate
pip install -e ../core -e ../knowledge -e ".[dev]"
pytest
```

## Spec Reader

A layout document (PDF; Word via the same interface) turned into a Source Spec draft — the same `source-spec-v0` shape `specs/<id>/<version>.yaml` already validates against — with a page citation on every field. A passage the model cannot confidently support with a citation is named in the draft's `unparsed` list, never guessed into a field.

```bash
astra-agents spec-reader test-connection                    # confirm ANTHROPIC_API_KEY works before a real run — no page, no UI, just this
astra-agents spec-reader run GCUS.pdf \
  --id pershing_gcus_full --version 2017-07-25 --effective-from 2017-07-25 \
  --file-type position --custodian pershing
```

`test-connection` lists the account's models (free, generates nothing) to prove the key authenticates and the configured model is available, without ever sending a document. `run` writes `spec.yaml` next to `report.md` / `report.json` under `work/spec-reader/<id>/<version>/`. The draft is never written into `specs/` directly — "agents propose, humans approve" — promoting it is a person's decision after reviewing the report's citation coverage and unparsed list.

The real model call sits behind `astra_agents.spec_reader.LlmClient`, a one-method interface: `AnthropicClient` implements it against the real Anthropic API (needs `ANTHROPIC_API_KEY` in the environment and `pip install "astra-agents[llm]"`); every test in this story implements it with a fake returning a canned response, the same way every Snowflake-touching command in `astra_verification` is tested against a fake executor rather than a live account.

## Profiler

A fixed-width sample file profiled against a Source Spec already in the registry: record-type counts, per-field null rate, distinct values, top values, and any field whose observed data does not fit the type the spec declares for it, flagged with a citation to the offending line. Read-only — it never writes into the registry, never touches production data.

```bash
astra-agents profiler run sample.dat --spec specs/pershing_gcus/2017-07-25.yaml
```

Writes `report.md` next to `report.json` under `work/profiler/<spec id>/<spec version>/`. Exit 0 means nothing to review; exit 1 means a field is flagged or a record type is missing.

No LLM, no credentials: the agent reuses `astra_knowledge.patterns.fixed_width.parse_fixed_width`, the same reference parser generation and verification are held to, and reads "observed type disagrees with the spec" straight off that parser's own field-level conversion problems (ADR 0042) rather than a second, parallel reading of the file. `agents/examples/profiler/` is a self-contained, committed 750-character example with two deliberately corrupted values, so the flagging behavior can be seen without a real document.

## Pattern Matcher

A spec with no family yet compared against every spec already in the registry, by detail-record field shape (picture kind and declared type, never field names), and assigned a family, a tier (`simple`/`medium`/`complex`) and a pattern list. A spec too unlike anything already known gets no family at all — a new pattern proposal for the architect queue, not a guess.

```bash
astra-agents pattern-matcher run --registry specs                                    # classify everything unclassified
astra-agents pattern-matcher run --registry specs --id pershing_gcus --version 2017-07-25   # check one spec by name
```

Writes `report.md` next to `report.json` under `work/pattern-matcher/`. Exit 0 means every spec classified this run got a family; exit 1 means at least one is a new-pattern proposal for a person to decide.

No LLM, no credentials: family similarity is a longest-common-subsequence comparison over detail records only (header and trailer are near-universal boilerplate and would swamp the real signal), gated to zero across a different file format or file type, and penalized when the count of detail record types differs (ADR 0043). The pattern list reuses `astra_knowledge.patterns.patterns_for` as-is. `agents/pattern_matcher/` is this agent's real gold set — not a stand-in like `agents/examples/spec_reader` — built from the real `specs/` directory with every family hidden but one sibling, so "assignment accuracy on known custodians" is measured against custodians already in this repository.

## Evaluation

```
agents/<agent>/
  eval.yaml          reviewed inputs and their correct output, one case at a time, tagged by tier, with the precision/recall threshold each tier must clear; versioned in Git
  predictions.yaml    what the agent actually produced for each case, in the same shape; produced fresh by the agent's own wrapper on each change, not committed for a real agent
```

An agent's output — a mapping, a spec field with its citation, a rule match, whatever that agent makes — is a set of canonical string items in both files; the harness only ever compares two sets, so it never has to know what any particular agent's output means.

```bash
astra-verify agent-eval check agents                                       # every pull request: every gold set valid
astra-verify agent-eval score --gold agents/spec_reader/eval.yaml --predictions agents/spec_reader/predictions.yaml   # the per-change release gate
astra-verify agent-eval report agents                                      # every agent with both files present, scored and published by tier — the weekly report
```

`score` exits 0 when every tier the agent's cases touch meets its own threshold and every case has a prediction; a tier below threshold, or a case with no prediction at all, fails the check and blocks release. `report` runs that same scoring for every agent under the directory that has both files, rolls precision and recall up by tier across every agent (micro-averaged: true positives, predicted and expected summed across the tier's cases before dividing, not the mean of each case's own rate — the same reason S4.2.2's parity report is weighted by rows), and is what `.github/workflows/agent-eval-weekly.yml` publishes every Monday.

`agents/examples/spec_reader` is a worked example against the real `pershing_gcus` spec already in this repository — never a claim about the real Spec Reader above, but checked and scored by name in the test suite, and deliberately imperfect (one field is missing from its predictions) to show the gate catching a real regression. The Spec Reader's own gold set — reviewed, correct output for a real layout document — is not built yet: it needs a person to review a real extraction first (ADR 0041's own consequences).

`agents/pattern_matcher/eval.yaml` and `predictions.yaml` are not a stand-in: they are the Pattern Matcher's real gold set, scored by the exact `score` command above, against `agents/pattern_matcher/gold/` — the real `specs/` registry with every family hidden but one sibling (ADR 0043).
