# ADR 0033: Parity is measured row by row, field by field, against what the lakehouse actually holds

Date: 2026-09-10
Status: Accepted
Story: S4.2.1 Parity engine (E4, F4.2, WBS 2.4.4)

## Context

The product spec's north-star metric is that ≥99.5% of rows reach parity with the legacy path within one dual-run cycle. Until now that number could only be asserted, not measured: the golden datasets (S4.1.2) hold the legacy oracle, and the config-change replay (S4.1.3) compares the factory against itself, but nothing compared the legacy output against what the lakehouse actually produced, row by row and field by field, with the tolerance a decimal or a date genuinely needs.

## Decision

1. **A parity mapping is a reviewed file per custodian**, `golden/<custodian>/parity.yaml` beside its capture file: which golden capture output is the oracle, which lakehouse table (`SILVER` or `GOLD`) it compares against, the columns that key a row on each side, and the columns compared for value, each with its own tolerance. The two sides rarely share column names or exact types, so the mapping is explicit rather than assumed, and it is validated in CI (`astra-verify parity check`) the same way every other config file in the repository is.

2. **Keys are the custodian's own identifiers, not the platform's resolved ones.** The example mapping keys on account number, CUSIP and business date — the values the legacy row already carries — rather than the platform's resolved `SECURITY_ID`. Parity measures whether the same rows were loaded with the same values; whether resolution found the right security is a separate, already-tested concern (ADR 0022). A key cannot carry a tolerance: two rows that differ only within a tolerance on a key are not the same row, and the validator says so.

3. **Every value field has a tolerance, and the comparison goes through it, not through string equality.** `exact` (the default, for strings, codes and dates), `decimal_places` (round both sides to N places, so "price to 4 dp" is a one-line config, not code), `absolute` and `relative` (an epsilon, for the fields where a fixed number of decimal places is the wrong shape of tolerance). A value present on one side and absent on the other is never a match, whatever the tolerance.

4. **Every difference is classified, and value mismatches are grouped by field.** A legacy key absent from the lakehouse is `missing`; a lakehouse key absent from legacy is `extra`; a key present on both sides with at least one field beyond its tolerance is a value mismatch, reported with the specific fields and their before/after values. The match rate itself is `matched / legacy_rows` — legacy is the oracle, so the rate answers "how much of what should be there is there," not "how much of what's there is right." Comparing a custodian with no rows on either side is defined as perfect parity, not zero, since there was nothing to lose.

5. **The comparison itself is pure.** `compare_rows` takes two lists of plain rows and returns a result with no I/O, so the classification rules are fully tested without an account. `run` wires it to real data: legacy rows come from the golden store for the business date being checked (the latest captured version, or one named explicitly), lakehouse rows from a live query against the environment named on the command line — the actual deployed lakehouse, not a sandbox rerun, because "the match rate is measured, not estimated" means measuring what is really there.

## Consequences

- A parity run needs a golden dataset already captured for the business date (S4.1.2) and reads directly from the live environment; it is not wired into CI, the same way replay (S4.1.3) is not — both are steward- and QE-run steps, not per-commit gates.
- Duplicate keys on either side (a data problem in their own right) are reported separately and do not corrupt the primary classification: the first row for a duplicated key is compared, the rest are counted and named.
- The report here is one business date's comparison; trends across cycles, difference groupings for a release, and the evidence-pack export are S4.2.2's job, built on this engine's `ParityResult`.
- A parity mapping's `keys`/`fields` list is the whole contract; adding a canonical column to compare, or tightening a tolerance, is a config change, reviewed like any other, with no code to write.
