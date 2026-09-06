# ADR 0012: Full versus delta merge decided by a header flag

Date: 2026-09-06
Status: Accepted
Story: S2.2.5 Full vs delta merge (E2, F2.2, WBS 2.2.6)

## Context

A custodian file is either a full refresh or an update, and the header says which. A refresh must replace every position of its remote id as of the business date; an update must merge on keys and carry untouched rows forward; a refresh not seen for N days must raise an alert. The merge semantics have to be defined once, testable without a platform, and enforced identically by the SQL the generation plane renders.

## Decision

1. **The spec declares the merge rule.** A `merge` block names the header field whose codes decide the mode (both `refresh` and `update` must be mapped, and the codes must be declared codes of that field), the header fields that scope a refresh (the remote id; empty means the whole source), the header date field carrying the business date, and the keys of the merged logical record. The registry checks every reference. The GCUS example declares `R` as refresh and `U` as update, scoped by remote id.

2. **The reference implementation is a merge engine over an in-memory Silver state.** `patterns.merge.apply` reads the mode from the parsed header and applies the file: rows in the file are inserted or updated on their keys; rows of the scope not in the file are retired under refresh and carried forward under update. It is the oracle the rendered MERGE (S3.2.3) must agree with row for row, and it runs locally in dry-runs and replays.

3. **A file that cannot be merged safely is not merged at all.** An unknown mode code or a missing header (`MERGE_MODE_UNKNOWN`), a parser rejection, or a business date earlier than what Silver already holds for the scope (`MERGE_OUT_OF_ORDER`) is a file-level problem that leaves the state untouched. Within a file, a duplicate key keeps the first row and reports `MERGE_DUPLICATE_KEY`; a blank key is `MERGE_KEY_BLANK`.

4. **Every merge is recorded on the platform.** `CONTROL.MERGE_LOG` holds custodian, source, scope, business date, mode, file and the four counts. The reference implementation produces the same entry, so a rendered pipeline and a local replay leave the same trail.

5. **A stale refresh is an alert, detected from the log.** The config's `delivery.refresh_expected_every_days` is synced into `CONTROL.CUSTODIANS`; `DETECT_STALE_REFRESHES`, part of the minute-by-minute alerting run, raises one `refresh_stale` alert per day at the custodian's late severity for every custodian, source and scope that has merged files but whose last full refresh is older than expected, or has never happened. Silver for that scope may be carrying positions the custodian has retired, which is what the alert says.

## Consequences

- Sources of the same custodian must agree on the refresh expectation, as they must on cutoffs and severities.
- Scope is per header, so one file replaces one remote id; a custodian delivering several remote ids in separate files refreshes each independently.
- Out-of-order files are refused rather than applied; replaying history therefore feeds files in business-date order, which the golden replay (S4.1.3) does.
- The stale-refresh detector only considers sources that have merged at least one file, so a custodian not yet live raises nothing.
- The renderer writes `MERGE_LOG` after each MERGE; until E3 lands, the log is populated by dry-runs and replays through the reference implementation.
