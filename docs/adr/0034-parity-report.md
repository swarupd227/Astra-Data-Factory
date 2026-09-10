# ADR 0034: A parity report is the engine aggregated over a cycle window, exported where the evidence pack expects it

Date: 2026-09-10
Status: Accepted
Story: S4.2.2 Parity report (E4, F4.2, WBS 2.4.5)

## Context

The parity engine (S4.2.1) answers one question well: for one custodian and one business date, how much of the legacy output does the lakehouse reproduce. A gate decision needs more than one day's number: a readable report a steward can hand to a reviewer, the differences grouped so a reviewer knows where to look, and whether parity is improving or getting worse across the dual-run cycles that have run so far. The product spec's Appendix A places this report inside a release, `parity/report.md`, alongside the rest of what a gate decision is made from.

## Decision

1. **A dual-run cycle is one business date's comparison.** "Trend over cycles" is the sequence of already-captured business dates' parity results, oldest to newest — not a separate concept from what S4.2.1 already computes, just many of it in a row. `astra-verify parity report` selects every captured date in an optional `--from`/`--to` window (all of them by default) from the same golden index S4.1.2 and S4.1.3 already read, runs the engine for each, and aggregates.

2. **The aggregate is honest about what a window means.** The overall match rate is `matched / legacy_rows` summed across every cycle in the window, not an average of daily rates (a date with more rows should weigh more than a date with fewer). Difference groups sum each field's mismatch count across the window, so the report still answers "which field, most" the way a single day's report does. The trend compares the first cycle in the window to the last, past a small epsilon, and says `improving`, `declining` or `flat`; with one cycle it says so plainly rather than inventing a trend from a single point.

3. **The report is exported where the release evidence lives, not invented as a new pack format.** A parity mapping's custodian directory already holds `capture.yaml`, which already names the source configs the custodian's golden data speaks to (`sources`). The report is written to `releases/<source>-parity/report.md` and `report.json` for every one of those sources — the same `<name>-<suffix>` convention already used for the reference-data, Gold and migration bundles (ADR 0015, ADR 0026, ADR 0028), so it sits beside a release the way `parity/report.md` sits inside one in the spec's own sketch, without colliding with anything `astra-data render` produces. That matters concretely: `write_bundle` (ADR 0018) deletes any file under a source's own bundle directory that its own render did not produce, so a parity report living inside `releases/<source>/` would be deleted by the next render; a sibling `-parity` directory is never touched by it.

4. **The engine is untouched.** `parity_report.py` is a new module that calls S4.2.1's `compare_rows` and row loaders once per cycle and aggregates the results; nothing about `ParityResult` or the single-date `astra-verify parity run` command changed.

## Consequences

- Compiling the full gate evidence pack described in the product spec (a PDF assembled from every kind of verification result, approvals and metrics, by a Gate Evidence Compiler) is Control-plane work well past E4; this story produces the one piece of evidence parity contributes, in the place a later compiler would look for it.
- `--no-export` writes the working report without touching `releases/`, for a trial run or a report over a window that is not meant to represent a release yet.
- A source with no capture file, or whose capture names no sources, exports nothing; the working report under `--out` is still written, so the command is never silent about what it found.
