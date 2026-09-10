# ADR 0032: A config change is safe when its replay against history finds nothing, not when it merely compiles

Date: 2026-09-10
Status: Accepted
Story: S4.1.3 Replay N days after a config change (E4, F4.1, WBS 2.4.3)

## Context

A steward's self-service change to an already-promoted config — a new DQ rule, a corrected transform, a widened resolution window — compiles and dry-runs clean on a sample, but a sample is not history. Before S4.1.1 and S4.1.2 there was no way to ask "what would this change actually do to the last two months of real files" without a manual side-by-side deploy. The backlog asks for a difference report grouped by rule and field, and for a change with zero differences to skip SME review; anything less than a real replay against real history invites exactly the silent regression self-service is supposed to avoid.

## Decision

1. **Two dry runs, not a diff of intentions.** `astra-verify replay` runs the old config and the new config through the pipeline's own path — the same sandbox mechanics as the dry run (S4.1.1) — against the same historical files, in two isolated sandboxes. `dry_run` gained one optional hook, `on_before_destroy`, called with the still-live sandbox right before it is dropped; replay uses it to read the canonical entity's full row content, which the dry run's own report does not carry. Nothing about the dry run's existing behavior or its report shape changed for its own callers.

2. **History is what was already captured, not a fresh guess at it.** The N business days replayed are read from the custodian's golden index (S4.1.2): the most recent N dates, latest version of each, and the exact files that capture recorded for them. A replay and a capture therefore never disagree about what "the last N days" means, and a custodian with no golden datasets gets a clear refusal naming the command that would fix it, not an empty or arbitrary run.

3. **The config diff is computed once, from the two compiled models, before any data runs.** Every mapping, DQ rule, referenced catalog rule and resolution part that differs between the old and new `CompiledConfig` is grouped the same way the data diff will group rows: by rule id where a mapping has one, `mapping: <record>.<field>` or `constant: <column>` otherwise, `dq:<id>`, `rule:<id>` or `resolution:<part>`. This costs nothing in Snowflake time and is often enough on its own to tell a steward what changed.

4. **Every differing row is attributed, not just counted.** After both runs, the canonical entity's rows (lineage columns excluded, since those differ between any two runs by construction) are compared by key: added, removed, or changed with the specific columns that differ. Each differing column is attributed to the same group the config diff uses, so "12 rows changed" reads as "12 rows changed under `pershing_gcus.quantity_sign`" — the steward sees the row content, but the finding is filed under the field or rule responsible for it, wherever it came from.

5. **Zero differences is a computed fact, not a claim.** `ReplayReport.auto_promotable` is true only when both runs completed, the config diff is empty, and no row, exception-code count or rendered-test result differs. Replaying a config against itself is the regression test for this: identical configs against identical history must report zero differences, and the CLI's exit code (0 when eligible, 1 when SME review is needed, 2 when the replay could not start) makes the gate usable from a promotion pipeline without parsing the report.

6. **The old config is a file, however it was obtained.** `--old` takes a path directly; `--old-ref` reads it with `git show <ref>:<path>` and writes it to the work directory, so a steward can replay against `HEAD`, a tag, or a specific commit without checking out a second working tree. A `git show` failure (bad ref, path never existed there) is reported as a `ReplayError`, not a stack trace.

## Consequences

- `load_capture` (S4.1.2) previously raised an unhandled `FileNotFoundError` on a missing capture file instead of returning a `Problem`; replay's "no golden datasets captured" path exposed this, and it is fixed for every caller, with a regression test.
- Replaying doubles the cost of a dry run (two sandboxes, sequentially, on the same executor) and multiplies it by however many files N days of history holds; it is a steward's pre-promotion step, not a per-commit CI gate, and is not wired into CI for that reason.
- Row attribution can only name a canonical column's mapping, rule, resolution part or "unmapped"; it cannot yet attribute a difference that arises purely from a resolution join (a different account or security resolving) to the specific reference-data row that changed — that is reference data's own concern, not the config's, and outside this story.
- The row diff compares full snapshots after each complete run, not an incremental log; a replay over many days and a large canonical entity is proportional to the entity's row count for the custodian, not to the number of differences.
