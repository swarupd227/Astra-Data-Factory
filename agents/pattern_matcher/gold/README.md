# Pattern Matcher gold set fixture

A copy of the real `specs/` registry, with the `family` field removed from every spec except
`pershing_gcus/2026-01-01` — the one classified sibling a correct run should find and reuse.

This is what `../eval.yaml`'s cases and `../predictions.yaml` are run against: real specs already
in this repository, not synthetic ones, so "assignment accuracy on known custodians" is measured
against the same custodians the rest of the factory already knows. `pershing_gcus/2017-07-25` should
be reunited with the family its own later version (`2026-01-01`) already carries; the other three are
each the only spec of their family in this repository, so the correct answer for each is *no* match —
a new pattern proposal, not a guess.

Reproduce `predictions.yaml` directly:

```bash
astra-agents pattern-matcher run --registry agents/pattern_matcher/gold/specs --json
```
