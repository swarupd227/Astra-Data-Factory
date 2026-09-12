# Agent evaluation

A gold set per agent, scored by `astra-verify agent-eval` (S4.3.4, ADR 0040, product spec Section 11). The Agents plane itself (E5) has not started; this is the harness every future agent's own "build and evaluate" story plugs into.

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

`agents/examples/spec_reader` is a worked example against the real `pershing_gcus` spec already in this repository — never a claim about a real agent, which does not exist yet, but checked and scored by name in the test suite, and deliberately imperfect (one field is missing from its predictions) to show the gate catching a real regression.
