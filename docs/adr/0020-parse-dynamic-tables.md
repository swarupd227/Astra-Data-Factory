# ADR 0020: Parsing is dynamic tables rendered from the spec, checked against the pattern library

Date: 2026-09-06
Status: Accepted
Story: S3.2.2 Parse Dynamic Table (E3, F3.2, WBS 2.3.3)

## Context

Raw lines land per source (ADR 0019). Turning them into typed columns is the first transformation and the one most likely to be written by hand differently for every custodian. The Source Spec already says everything a parser needs: record types and their match rules, offsets, pictures, formats, codes and sign fields. The pattern library (ADR 0010, 0011) holds the reference implementation of those value rules. Parsing on the target has to be config-driven, incremental, and provably the same as the reference.

## Decision

1. **Parsing is a set of dynamic tables, one per detail record type, over the source's lines view.** `render/parse.py` renders `<SOURCE>_CLASSIFIED`, a view that gives every line the record type its match rule yields and flags lines longer than the record length; a dynamic Iceberg table `<SOURCE>_<RECORD>` per detail record type with every field typed; `<SOURCE>_PARSE_PROBLEMS`, a dynamic table of every file, record and field problem with its rejection code; and `<SOURCE>_FILE_METADATA`, a dynamic table per file with line and record counts, excluded rows, problem counts and the header and trailer values. Snowflake refreshes them incrementally; nothing is scheduled or procedural.

2. **Rows that fail record-level DQ are excluded and counted.** A line no record type matches, or that is longer than the record length, is not in any record table; it is a `record`-level row in the problems table and counted in `EXCLUDED_ROWS` per file. A field-level problem (a value that is not a number, a date in the wrong format, an undeclared code, a blank required field) leaves the value NULL and keeps the row, as the pattern library does. A missing header or trailer, or a second header, is a file-level or record-level problem on the file.

3. **The value rules are the pattern library's, expressed in SQL.** Blank is NULL; a declared code is kept as written and a blank code is a code; integers are digits; implied decimals take their scale from the picture and their sign through the foundation's `SIGNED_IMPLIED_DECIMAL` and `SIGNED_IMPLIED_DECIMAL_PROBLEM`, with the sign convention from the spec's codes (ADR 0011); dates and times follow the declared format and all zeros is NULL; booleans are Y/N, T/F, 1/0. `patterns/values.py` and `patterns/numerics.py` remain the oracle; the rendered SQL is what the parity checks of E4 compare with them, and this ADR is the statement that they must agree row for row.

4. **TARGET_LAG comes from the config.** `processing.target_lag_minutes` (default 15) is the target lag of every parse dynamic table and the interval of the source's process task, so freshness is one number in one place. The tier warehouse of the source refreshes the tables.

5. **Delimited files parse by splitting on the delimiter.** A delimited spec without quoting or escaping renders with `SPLIT_PART` and explicit numbers. A spec that declares quoting or escaping is not rendered by this story: the pattern library handles it, and its rendering is a COPY with a CSV file format rather than a raw-lines parse, which a later F3.2 story adds.

6. **Bronze holds what lands and what happened.** The typed record tables and the problems table that ADR 0018 rendered as Iceberg tables written by a stage are replaced by the dynamic tables above; the raw-lines table, the pipe and the file registry stay. The header and trailer values live on the file metadata table rather than the registry, because they are parsed, not registered.

## Consequences

- The merge stage (S3.2.3) reads the record tables and the file metadata (business date, refresh flag, trailer counts) and pairs records where the spec says so.
- Data metric functions attach to the dynamic record tables with `ALTER DYNAMIC TABLE`; confirming that syntax against a live account is part of the E4 parity work, as is comparing the dynamic tables with the pattern library on the golden files.
- A spec whose date or time format the renderer does not support fails rendering with the supported formats listed; adding a format is a change to both the pattern library and the renderer.
