# ADR 0040: The harness is agent-agnostic — canonical string items, micro-averaged by tier, no agent required to exist

Date: 2026-09-12
Status: Accepted
Story: S4.3.4 Agent evaluation harness (E4, F4.3, WBS 2.4.11, 2.4.12)

## Context

Product spec Section 11 asks for a gold set per agent, every agent change scored against it before release, and precision/recall by agent and by tier published weekly — "the harness is what makes the factory trustworthy and sellable. It ships with the product." No agent exists yet: the Agents plane (E5) has not started, and each agent's own "build and evaluate" story (S5.1.1, S5.5.1, S5.6.1 and the rest) is still ahead. The harness has to be useful and testable today, against nothing more than files, and still be the exact thing every future agent story plugs into without change.

## Decision

1. **An agent's output is a set of canonical string items; the harness never interprets what they mean.** A gold set (`agents/<agent>/eval.yaml`) names, per reviewed case, the items the agent should produce — a mapping, a spec field with its citation, a rule match, whatever that agent makes. An agent's predictions (`agents/<agent>/predictions.yaml`) name the same shape of items it actually produced. Precision and recall are computed the classic way over these two sets. This is the one design choice that lets the harness exist before any agent does: every future agent's own wrapper canonicalizes its structured output into strings once, and the harness never has to know the Spec Reader's output shape differs from the Modeler's.

2. **Tier aggregation is micro-averaged — true positives, predicted and expected summed across every case of a tier before dividing — not the mean of each case's own rate.** The same reasoning as S4.2.2's parity report (weighted by rows, not averaged by cycle, ADR 0034): a tier with one large, hard case should not be drowned out by three small, easy ones. "Tier" is the same simple/medium/complex vocabulary a source config's own `tier` field already uses (product spec's KPI table already reports agent acceptance "by agent and tier" this way).

3. **Thresholds live inside the gold set, per tier, and are never invented.** The backlog's own convention (`docs/backlog-v0.2.md` line 11) is explicit: a `[T]` placeholder is set by the QE or agent engineer from the pilot baseline, not assumed by the agent that implements the story. `astra-verify agent-eval score` is the per-change gate: a tier whose measured precision or recall falls below its own threshold fails, and so does a gold set case with no prediction at all — a missing case is reported as missing, never silently scored as zero, so a caller can tell "the agent got nothing right" from "the agent was never run for this."

4. **`report` and `score` are the same scoring engine used two ways.** `score` is the per-change gate for one agent (exit 1 on a regression, blocking release). `report` runs it for every agent that has both a gold set and predictions present under an agents directory, aggregates by tier across every agent, and writes the published weekly evidence — an agent with a gold set but no predictions yet is named as skipped, not failed, since it has simply not been run.

5. **One example gold set is committed, `agents/examples/spec_reader/`, deliberately imperfect.** It scores the Spec Reader's future output against the real `pershing_gcus` spec already in this repo (its field names and page/line citations, not invented data), with one field left out of the predictions on purpose — the exact "detail medium tier's recall regressed" scenario a real gate would need to catch. It sits under `examples/`, the same convention `configs/examples/pershing_position.yaml` already established: a smoke test of the schema, checked and scored by name in the test suite, not swept up by a directory-wide scan of `agents/` the way a real agent's own gold set would be.

## Consequences

- Nothing about a specific agent's evaluation methodology is decided here — only the shape every agent's own future story must produce a `predictions.yaml` in. A story that needs a different comparison (a numeric tolerance, not a set) is out of this harness's scope and would need its own.
- `predictions.yaml` is not meant to be committed for a real agent the way the example's is; a real one is produced fresh by that agent's own wrapper on each change, most often written to a working directory outside Git, the same way a dry run's or a volume test's own reports are.
- The weekly report's own scheduling (a cron trigger) is a CI concern, wired into `.github/workflows/agent-eval-weekly.yml` alongside this story, not something the Python harness itself manages.
