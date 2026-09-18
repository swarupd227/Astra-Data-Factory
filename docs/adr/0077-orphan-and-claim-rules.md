# ADR 0077: Orphan is a real, new per-custodian threshold; claim is already-real precedence, now proven on seeded data

Date: 2026-09-18
Status: Accepted
Story: S7.1.3 Account, security, tx-code and price resolution configured (E7, F7.1, WBS 2.7.5–2.7.8)

## Context

ADR 0022 (S3.2.4) already built all four resolutions as set-based joins and already forward-
referenced this exact story by name — "S7.1.3 configures the four resolutions to Loader behaviour
with the client's orphan and claim rules" — but never defined what "orphan" or "claim" mean.
Research before writing anything found, exhaustively:

- **"Orphan" and "claim" are not implemented anywhere.** The only repo hits for "orphan" are the
  CDM renderer's unrelated foreign-key referential-integrity test concept; the only hits for
  "claim" are unrelated senses (file-routing, English "assertion," SSO claims). Neither word names
  a real config field, taxonomy concept, or rendered behavior before this story.
- **No real Loader documentation exists to match against.** `domains/custodial/README.md` itself
  names `loader-rejections.csv` as "client material; not yet added." Rule Recovery's own example
  (`agents/examples/rule_recovery/java/Loader.java`) is explicitly illustrative, not real Envestnet
  source, and says nothing about orphan or claim handling. "Configured to Loader behaviour" cannot
  honestly mean byte-for-byte parity with a system this repository has no record of.
- **Precedence for ambiguous security matches already exists and is already proven.** `resolve.py`
  tries each configured identifier in order; the first with any match decides — exactly one match
  resolves, more than one is `SECURITY_AMBIGUOUS`. The reference implementation
  (`astra_knowledge.patterns.reference_data.Replica.resolve`, ADR 0015) implements the identical
  precedence and is already exercised with real seeded data:
  `test_securities_resolve_by_identifiers_in_order`, `test_an_identifier_shared_by_two_rows_is_
  ambiguous`, `test_accounts_resolve_by_their_key` (`knowledge/tests/test_reference_data.py`).
  This *is* a claim rule — which candidate match a record's own identifiers "claim" — already real,
  already tested, already configurable per custodian via `resolution.security.by`'s own order.
- **No config field or renderer concept exists for "how long an unresolved row is tolerated."**
  Every resolution failure is unconditionally held back from the merge and written as an exception
  — this never changes, and is itself the platform's best-faith Loader-parity design (never
  silently drop data waiting on reference data). What genuinely does not exist is a per-custodian
  *threshold* for when a held row becomes an aged concern rather than a routine one — the literal
  meaning of "orphan policy... from config."
- **Two of the four resolutions had no seeded-data proof of any kind**, only static rendered-SQL-
  text assertions: transaction-code mapping and the price lookback. Account and security already
  had real, executable, seeded coverage via `Replica.resolve`.

## Decision

1. **"Claim" needs no new code — it is documented and its existing seeded-data proof is cited
   directly**, not rebuilt. Building a second precedence mechanism would duplicate `Replica.
   resolve` rather than extend it, the same reasoning that kept earlier stories from duplicating
   already-built capability (S7.1.2; S6.2.5's own per-exception-store decision).

2. **"Orphan" becomes a new, real, additive config capability: `resolution.orphan_policy.
   grace_hours`** (default 24), added to `config-v0.schema.json`, threaded through
   `CompiledConfig.resolution` (`OrphanPolicy`, `compiler.py`), and rendered into the resolve
   procedure's own `COMMENT` — a real, visible, deployed artifact — mirroring the exact shape
   `reference-data.yaml`'s own `expected_every_hours` already established for feed freshness
   (S2.3.3), not an invented pattern. **It never changes which rows are held or merged** — only
   when a held row is an aged concern — so it carries zero risk to the already-deployed,
   already-tested exception-write and merge logic in `resolve.py`.

3. **No real Envestnet grace-period value is guessed at.** 24 hours is a sensible, documented
   default (roughly one business day), explicitly named as a default a real engagement can
   override — the same discipline that kept S7.1.2 from touching a real cron schedule without a
   real client fact to set it to.

4. **The two resolutions with no prior seeded-data proof get a small, real reference
   implementation, mirroring `resolve.py`'s own rendered SQL exactly**, the identical "a Python
   twin the rendered SQL is checked against" shape `Replica.resolve` already established:
   - `resolve.py`'s own `resolve_price`/`resolved_price` — the price lookback (most recent within
     `[as_of - lookback_days, as_of]`) and the `when: missing` vs `when: always` source-price
     preference — seeded with real date/price histories, reproducing `PRICE_MISSING` on an
     out-of-window and an empty history.
   - Transaction-code mapping needed no new function at all: `CompiledConfig.resolution.
     transaction_code.map` *is* the exact dict the rendered `VALUES` join is built from, so seeding
     real codes directly through the compiled config's own map reproduces `TRANSACTION_CODE_
     UNMAPPED` with no reimplementation risk whatsoever.

5. **Exhaustive proof of every rejection code stays S7.1.6's own job**, per ADR 0022's own explicit
   split ("S7.1.6 reproduces every code on seeded data"). This story's seeded-data work is scoped
   to the seven codes the four resolutions directly wire today (`ACCOUNT_NOT_FOUND`,
   `ACCOUNT_CLOSED`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `SECURITY_INACTIVE`,
   `TRANSACTION_CODE_UNMAPPED`, `PRICE_MISSING`) — all seven now have real, executable seeded
   coverage, three pre-existing and cited, four new.

## Consequences

- `configs/examples/pershing_position.yaml` (still explicitly never deployed) now declares
  `orphan_policy: { grace_hours: 24 }` explicitly, so the one real example this repository has
  demonstrates the new field compiling, validating and rendering end to end.
- No real Pershing/Envestnet source config was created by this story. `configs/` still has only
  the example — standing up a real, deployable custodian config is a distinct, consequential
  decision (what actually gets deployed to dev/qa on the next merge) this story does not make
  unilaterally.
- The remaining ten resolution-category rejection codes not wired into `resolve.py`
  (`ACCOUNT_FIRM_MISMATCH`, `ACCOUNT_AMBIGUOUS`, `SECURITY_IDENTIFIER_INVALID`,
  `TRANSACTION_CODE_AMBIGUOUS` real detection, `PRICE_STALE`, `PRICE_ZERO`, `CURRENCY_UNKNOWN`,
  `FIRM_NOT_FOUND`, `REFERENCE_DATA_STALE`, `REFERENCE_DATA_CONFLICT`) are an honest, named gap —
  not silently left implicit — for S7.1.6 or a future story to close, not fabricated here.
- Matching the deployed `grace_hours` against Envestnet's own real tolerance for an unresolved
  record, and matching resolution's hold-everything design against the actual legacy Loader's own
  documented behavior, both remain genuine operational confirmations for a real engagement — the
  same class of gap ADR 0076 named for the replication schedule, not something this repository can
  supply on its own.
