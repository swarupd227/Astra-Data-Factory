# Runbook: score an agent against its gold set

Story S4.3.4 (ADR 0040). Every agent is scored against a reviewed gold set of inputs and correct output, by tier, on each change; results are published weekly.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. A gold set | Agent engineer | `agents/<agent>/eval.yaml` exists: reviewed cases, each with its input, its correct output as canonical string items, and a tier; `astra-verify agent-eval check agents` passes. Thresholds per tier are set from the pilot baseline, never invented (the backlog's own `[T]` convention). |
| 2. A canonicalizer | Agent engineer | The agent's own wrapper turns its structured output (a mapping, a spec field, a rule match) into the same shape of canonical string items the gold set uses, so the two sets are actually comparable. |
| 3. Predictions | Agent engineer | The agent has been run against every gold set case and its output written to `agents/<agent>/predictions.yaml` in the same case ids. |

## Running it

```bash
astra-verify agent-eval score \
  --gold agents/spec_reader/eval.yaml \
  --predictions agents/spec_reader/predictions.yaml
```

The summary line gives precision and recall per tier against each tier's own threshold; the full report is `eval.md` next to `eval.json` under `work/agent-eval/`.

Read the report in order:

1. **Missing predictions** — a gold set case the predictions file has no entry for at all; not scored as a failure at zero, reported by name. Fix by running the agent against that case before scoring again.
2. **Precision and recall by tier** — micro-averaged across the tier's cases (summed true positives, predicted and expected, then divided once), not the mean of each case's own rate, so a tier's larger or harder cases weigh proportionally more.
3. **False positives and false negatives, by case** — exactly which items the agent invented and which it missed; this is where to start when a tier misses its threshold.

## Deciding

- **Passed**: every tier the agent's cases touch meets its own threshold, and every case has a prediction. Release is not blocked.
- **A tier is below threshold**: this is the regression the story asks the gate to catch — read the false positives and false negatives for that tier's cases first; a pattern across several cases usually points at one systematic mistake, not many unrelated ones.
- **A case is missing**: the agent was not run against it, or the run failed before producing output for it; this is a coverage gap, not a quality regression, but it still blocks release until it is resolved.

## The weekly report

```bash
astra-verify agent-eval report agents
```

Scores every agent with both a gold set and predictions present, and rolls precision and recall up by tier across every agent — the same report `.github/workflows/agent-eval-weekly.yml` publishes every Monday as a job summary and an artifact. An agent with a gold set but no predictions yet is named as skipped, not failed; it has simply not been run.

## Notes

- The harness reads files only; it needs no Snowflake connection.
- `agents/examples/spec_reader` is a worked, deliberately imperfect example — safe to run `agent-eval score` against directly to see the report format before a real agent exists.
- A real agent's `predictions.yaml` is produced fresh on each run; it is not meant to be committed the way the example's is.
