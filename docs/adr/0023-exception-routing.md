# ADR 0023: Every rejected row is an exception with its source record, and a run ledger proves it

Date: 2026-09-07
Status: Accepted
Story: S3.2.5 Exception routing (E3, F3.2, WBS 2.3.6)

## Context

The parse tables exclude lines that fail record-level checks (ADR 0020), the merge holds back unpaired and duplicate rows and rejects whole files (ADR 0021), and the resolution holds back rows it cannot resolve (ADR 0022). Each stage already reports what it rejected, but parse problems lived only in a dynamic table and the store's state vocabulary said "open". What was missing was one store with one rule: a rejected row is never dropped without an exception row that carries the record itself, and a way to check that rule in every environment.

## Decision

1. **One exception store per source, one state on arrival.** Every stage writes to `EXCEPTIONS.<SOURCE>` with `STATUS = 'NEW'`; the canonical Exception entity's codes are `NEW`, `RESOLVED`, `AUTO_RESOLVED` and `DISMISSED`. Triage (E5) moves a row out of `NEW`; nothing else does.

2. **The payload is the full source record.** A parse problem carries the raw line with its file, line number and record type; a merge or resolution exception carries the parsed row as JSON; a file-level exception carries the file's metadata row (counts, header and trailer values). The column is NOT NULL: an exception without its record cannot be written.

3. **Parse problems are routed when a file is taken.** The merge stage, before it decides anything about a file, copies that file's parse problems into the store under its run id, joining the lines view for the raw line. The lines the parse excluded are that run's rejected rows; field-level problems travel too, as field-level exceptions, so a flagged value is visible where triage looks.

4. **A run ledger counts what the stages rejected, from their own inputs.** `BRONZE.<SOURCE>_RUNS` is opened by the process procedure and written by every stage: files registered, merged and rejected, rows merged and projected, and `ROWS_REJECTED`, which the merge adds from the parse metadata and its own key checks and the resolution adds from the rows it held. The process procedure closes the run with the store's exception counts by level.

5. **The acceptance criterion is a deployed test.** `<source>_rejected_rows_equal_exceptions.sql` returns any finished run whose `ROWS_REJECTED` differs from the number of distinct source rows with a record-level exception in the store for that run. The two numbers come from different places, so the test is not tautological: the stages count what they left out, the store holds what they wrote. `<source>_exceptions_have_payload_and_start_new.sql` returns any exception without a payload or with a state outside the vocabulary. A file rejected whole is counted as a rejected file, its rows stay in Bronze and are not counted as rejected rows; the file-level exception with the file's metadata is the record of it.

6. **Pairing follows the pattern library exactly.** Only the first record for a key is checked for its partner; a second record for the same key is a duplicate, not also an unpaired record, so each rejected physical row has exactly one merge exception and the counts agree.

## Consequences

- The bundle gains the run ledger in the Bronze DDL and two tests; the stages' procedures write the ledger; the exception store's payload is mandatory.
- The exception store's workflow columns (`RESOLUTION`, `RESOLVED_BY`, `RESOLVED_AT`) are ready for Exception Triage (E5) and the workbench (E6), which read `NEW` rows with their payloads and codes.
- The E4 parity work compares the ledger's rejected counts with the Loader's rejection counts per file, which is what "no data silently dropped" means against the golden dataset.
