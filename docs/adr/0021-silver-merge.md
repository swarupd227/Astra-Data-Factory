# ADR 0021: The Silver merge is a rendered procedure that reproduces the pattern library's merge

Date: 2026-09-06
Status: Accepted
Story: S3.2.3 Silver MERGE (E3, F3.2, WBS 2.3.4)

## Context

The Loader applies a custodian file to its current state by the file's header: a full refresh replaces everything in the file's scope, a delta merges by key and carries the rest forward. Files that split a position across two records are paired first. The pattern library holds the reference semantics (ADR 0012 merge, ADR 0010 pairing) and raises the rejection codes for what cannot be merged. The parse tables (ADR 0020) refresh on their own lag, so a merge must know a file is fully parsed before it applies it, and the platform must log every merge for the stale-refresh alert (ADR 0012).

## Decision

1. **Silver holds the source's logical record, one active row per scope and key.** `SILVER.<SOURCE>_<RECORD>` carries the logical record's columns (paired where the spec pairs), the scope values from the header, the business date, first and last file and line, and `RETIRED_AT` and `RETIRED_BY_FILE`. A refresh retires rather than deletes, so history stays queryable and the retired count is the count the pattern library reports. Projection into the canonical entities with resolved identifiers is the next stage (S3.2.4); it reads this table.

2. **The MERGE stage is a procedure that applies pending files in arrival order.** `<SOURCE>_MERGE` reads the file registry, skips a file until its parse is complete (the file metadata's line count equals the lines Snowpipe loaded, and every record table holds the rows the metadata expects), reads the mode, scope and business date from the header values on the file metadata, builds the file's logical rows, raises exceptions, merges, retires or carries, logs, and marks the file. It is the second stage of the process procedure, after intake.

3. **Full mode replaces; delta mode merges.** Rows in the file are inserted or updated on scope and keys either way. In refresh mode every active row of the scope the file no longer carries is retired; in update mode those rows are carried forward and counted. The counts are the pattern library's: inserted, updated, carried, retired, written to `CONTROL.MERGE_LOG` with the scope as text.

4. **Paired rows only; unpaired go to exceptions.** Where the spec pairs records, the file's rows are the join of the record tables on the pairing keys within the file, first record for each key. A record whose partner never appeared (`PAIR_INCOMPLETE`), a second record for the same keys (`PAIR_DUPLICATE`) and a record with a blank key (`PAIR_KEY_BLANK`) are exceptions and not merged. A blank merge key (`MERGE_KEY_BLANK`) and a second row for the same keys in one file (`MERGE_DUPLICATE_KEY`) are exceptions too; the first row is kept. A file whose header declares no known mode (`MERGE_MODE_UNKNOWN`) or whose business date is earlier than what Silver holds for its scope (`MERGE_OUT_OF_ORDER`) is rejected whole.

5. **Exceptions are rows in the EXCEPTIONS schema, in the shape of the canonical Exception entity.** `EXCEPTIONS.<SOURCE>` carries the rejection code, level, stage, message, the record key as text, the rejected row as JSON, the file and line, the config digest and the run, with the workflow columns (status, resolution) the exception store (S3.2.5) operates. The stage writes exceptions the way the pattern library reports problems: code, level, line.

6. **The golden comparison is E4's.** This story makes the rendered merge agree with the pattern library by construction: same modes, same keys, same scope, same counts, same codes, same first-row-kept rule. Comparing the output with the golden dataset on the pilot custodian needs the replayed Loader outputs (S4.2.x) and a live environment; that is the parity work, and this ADR is what it checks against.

## Consequences

- Intake records the lines Snowpipe loaded per file, so the merge can tell a fully parsed file from one still refreshing.
- The bundle gains `ddl/silver_<source>.sql`, `pipeline/<source>_merge.sql`, and tests that active Silver keys are unique, exception codes are in the taxonomy, and every merged file has one log entry.
- A spec without a merge block cannot be rendered into Silver; the message says what to add.
- S3.2.4 projects `SILVER.<SOURCE>_<RECORD>` into the canonical entities with resolved account and security ids; S3.2.5 gives the exception tables their workflow.
