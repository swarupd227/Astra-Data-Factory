# ADR 0066: The index read directly, gaps from the same walk the real capture CLI uses, and a shown command that is never run

Date: 2026-09-15
Status: Accepted
Story: S6.3.10 Golden dataset viewer (E6, F6.3, WBS 2.6.22)

## Context

AC1 ("calendar view of captured days... with hashes") and AC2 ("gap highlighted; capture can be
requested") both have real, already-built pieces in `astra_verification.golden` (S4.1.2, ADR
0031) to reuse, not reinvent:

- **The index.** `load_index(golden_dir, custodian)` already reads a custodian's own
  `datasets.json`, returning `{"custodian": ..., "datasets": [...]}` — never raising, an empty
  list when nothing has been captured. Each entry already has exactly the fields backlog S4.1.2's
  own AC names: `hash` and `source_files`, plus `business_date`, `version`, `captured_at`, `store`,
  `rows`. `astra_verification.parity_report.captured_business_dates` reads the same index but
  *raises* when nothing has been captured (built for "give me dates to run parity against," where
  zero is a hard failure) and collapses to only the latest version per date — the wrong shape for
  a calendar that should show every version's own hash, superseded ones included ("hashes,"
  plural, in the story's own words), and should render an empty calendar cleanly rather than
  erroring. This module reads `load_index` directly instead.
- **The expected calendar.** `astra_verification.golden.Capture.business_days_between(start, end)`
  is the exact function the real `astra-verify golden capture` CLI itself walks to decide which
  days to capture — the natural, already-correct source of "which business days *should* have
  been captured," not a second calendar computed against a source config's own (logically
  separate) `delivery.business_days`.
- **The coverage summary.** `astra_verification.golden.coverage(golden_dir, custodian)` already
  computes the "30 to 60 business days" verdict (S4.1.2's own AC). Reused directly for this
  viewer's own summary line, not recomputed.

AC2's "capture can be requested from the screen" is the one question with a real mechanical
answer, following the same pattern S6.3.7/S6.3.8/S6.3.9 each already established: does a live
capture need anything no environment here has? `cmd_golden_capture` needs a real non-prod Loader
database connection (`connection_env`), a real historical-file location, and a real
`loader-replay` subprocess — none of the three exist in any environment this codebase runs in.

## Decision

1. **A gap is a business day (per `Capture.business_days_between`) with no entry at all in the
   real index** — not "no *current* version," since there is no concept of a superseded capture
   being any less real than a current one for completeness purposes; a day with even one version
   captured is not a gap.

2. **Every entry in the requested window shows, not just the latest per day** — `CapturedDay`
   carries one row per (business_date, version), each with its own real `hash`. This is a
   deliberate difference from `custodian_page`'s or `parity_viewer`'s own single-latest-value
   conventions: AC1 asks for "hashes" in the plural, and a day recaptured with different content
   getting a new version (module docstring of `astra_verification.golden`: "different content adds
   the next version and never touches the previous one") is exactly the kind of history this
   screen exists to make visible.

3. **A gap's own `capture_command` is a real, complete, single-day `astra-verify golden capture`
   invocation** — `--from`/`--to` both set to the gap's own date, `--store` the caller's real
   store when given, an honest placeholder when not. It is composed and printed; nothing in this
   module ever executes it, matching the identical boundary S6.3.7's `parity-viewer`, S6.3.8's
   `run-status`, and S6.3.9's `drift-review` each already drew around a real action no environment
   here can perform live.

4. **This story adds no write action.** `golden-viewer.show` is a read, available to every role
   uniformly — showing a command is not running one, so there is nothing here for a role to be
   authorized or refused for beyond reading. Its own actor, "QE engineer," is the same role gap
   already named honestly for S6.3.7 and S6.3.8, moot for the same reason.

## Consequences

- `golden-viewer show` takes the same optional `--role` every command in this plane does, though
  every role is already authorized for it.
- No real multi-version, multi-gap golden index is committed anywhere in this repository (`golden/
  pershing/` has only `capture.yaml`, `connector.yaml` and `parity.yaml` — never a real
  `datasets.json`) — this module's own tests build one directly, in the real index's own exact
  schema, against the real, committed `golden/pershing/capture.yaml`'s own real `business_days`.
- No rendered calendar screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested gap computation and
  capture-command composition a screen would be built on top of.
