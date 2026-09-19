# ADR 0079: Reconciliation is two identities over the canonical tables, with the domain's choices in a pack file and breaks kept apart from exceptions

Date: 2026-09-20
Status: Accepted
Story: S7.1.5 Reconciliation engine (E7, F7.1, WBS 2.7.10)

## Context

The story asks for "control totals and the position identity (prior + transactions) checked with
tolerances, so that breaks are detected automatically", a seeded break "detected and categorised",
and a tolerance "per field type" that is configurable. Research before designing found that the
repository has the vocabulary but no engine:

- **Nine reconciliation codes are declared in the taxonomy and nothing raises them** —
  `CASH_BALANCE_MISMATCH`, `POSITION_VALUE_MISMATCH`, `LOT_TOTAL_MISMATCH`, and six more — with
  text that already says "beyond tolerance". There was **no code for the position identity**; only
  the cash version existed.
- **The word "break" belongs to parity.** The parity engine and the Break Explainer use it for a
  legacy-versus-lakehouse difference (`missing`/`extra`/`mismatch`; causes `transform`/
  `resolution`/`unmapped`/`unexplained`), and the Control plane's `QueueItemKind.BREAK` reads only
  that report. **"Reconciliation identity" is also taken**: the glossary means the entity keys.
- **Tolerances exist only per field, only for parity** (`exact`, `decimal_places`, `absolute`,
  `relative`, on a named field); nothing maps a *type* to a tolerance. The one trailer control-total
  check (`control_total`, S3.2.7) is exact, per file, per source, and exercised in the repository only
  as a record count.
- **The model is silent on what "prior + transactions" means.** `Transaction.QUANTITY` says only
  "units transacted"; nothing says whether it is signed, which types move quantity, which date places
  a transaction between two snapshots, or what the prior snapshot is. Only `STATUS` is explicit —
  "only `ACTIVE` counts towards holdings and cash". `NET_AMOUNT` is signed by its own description.
- **No fixture ties positions to transactions** and no client threshold exists (ADR 0046: nothing
  invented).

## Decision

1. **Two identities, on canonical tables only.** The *position identity*: for each account with an
   earlier snapshot, a position's quantity equals its quantity in the latest earlier snapshot (nothing
   if it was not held) plus the securities movement of the `ACTIVE` transactions dated after that
   snapshot up to the business date. The *cash balance identity*: a balance equals the previous
   balance of the same account, currency and balance type plus the net amount of the `ACTIVE`
   transactions of that currency in the same window. Both run over `SILVER.POSITION`,
   `SILVER.CASH_BALANCE` and `SILVER.TRANSACTION` — cross-source by nature, so no per-source table
   is needed. "Control totals" is read as the cash identity: a control total recomputed from the
   day's transactions and compared to the reported balance. The existing trailer check stays what it
   is (see Consequences).

2. **Every domain choice is configuration, not code, and none is guessed.** A new per-pack file,
   `domains/<pack>/reconciliation.yaml`, follows the convention `read-models.yaml` and
   `reference-data.yaml` set (a JSON schema, a loader, a `DomainPack.reconciliation` attribute,
   cross-checks at load). It names which date places a transaction (`trade_date` by default here;
   the model has no basis column for positions), a movement for **every** transaction type of the
   model exactly once — a new type forces a decision — and the cash checks per balance type, each on
   its own date (a settled balance moves on the settle date, a trade-date balance on the trade date,
   which the balance type's own name defines).
   - `BUY`/`TRANSFER_IN` add, `SELL`/`TRANSFER_OUT` subtract, and the cash-only types do not touch
     quantity: each is what the model's own code descriptions say. The movement is the type's
     direction times the size of the row's quantity, so a row that carries its own sign cannot flip a
     sale into a purchase.
   - `CORPORATE_ACTION`, `ADJUSTMENT` and `OTHER` are `unreconcilable`. Their effect on quantity
     cannot be computed from a row (no ratios feed exists), so a position they touch is reported
     `unverifiable` — the equation is incomplete — rather than passed or failed on a guess. The
     same holds for a type the model does not know, a movement missing its quantity, and a
     transaction the movement date cannot place.

3. **Tolerance is per field type, in the shape parity already uses.** Two types, `quantity` and
   `amount`, each `exact`, `decimal_places`, `absolute` or `relative`; a type left out is exact. The
   committed pack sets both to `exact`, because no client threshold exists to set — a real
   engagement supplies them. The mechanism is proven with real tolerances in the tests, and each
   type reaches only its own check: a quantity tolerance never loosens cash.

4. **Breaks are categorised, and the category set is closed.** `mismatch` (in both snapshots, the
   identity fails), `appeared` (a position the earlier snapshot did not have, not explained by the
   transactions), `disappeared` (a position or balance the identity says should exist is absent) and
   `unverifiable`. An account with no earlier snapshot is a baseline, not a break. The position
   identity raises a **new** taxonomy code, `POSITION_QUANTITY_MISMATCH` (severity `error`, steward,
   entity Position), because none existed; cash raises the existing `CASH_BALANCE_MISMATCH`. Adding
   a code changes no existing one.

5. **A break is not an exception row.** An exception belongs to one source's `EXCEPTIONS.<SOURCE>`
   table and leaves `NEW` exactly once (S7.1.4); a reconciliation break compares two sources and is
   found again, or has gone, on the next run. So `CONTROL.RECONCILIATION_BREAKS` holds the *current*
   findings of a custodian and business date, replaced whole by each run in one transaction — a fixed
   break simply is not there — and `CONTROL.RECONCILIATION_RUNS` logs each run with its row counts.
   Each break carries the taxonomy code of its check, so the taxonomy stays the one list of what can
   be wrong. Assigning and working a break is a later story; it is not built here.

6. **One definition, two implementations, executed against each other.** The Python reference
   implementation (`astra_knowledge.patterns.reconciliation`) defines the identities. The renderer
   emits each check as a single portable `SELECT` — the same text whether the procedure binds
   `:CUSTODIAN_ID` or a test supplies a literal. The renderer's tests transpile that SQL from
   Snowflake's dialect to DuckDB, **run it over seeded copies of the three tables**, and require it
   to find exactly what the reference implementation finds: on hand-seeded days covering every
   category, and on 320 randomised worlds (80 seeds × 4 configurations — tolerance kinds, movement
   dates, swapped cash dates) with every transaction type and status, missing settle dates and
   quantities. A coverage assertion keeps the comparison from being vacuous. The five deployable
   invariant tests are also run over DuckDB, each against good rows (no findings) and a deliberately
   bad row (it is returned). DuckDB is a dev-only test dependency.

## Consequences

- **This closes the "rendered SQL is only checked from its text" gap for these checks** — the first
  rendered logic in the repository that is executed, not only parsed, though not on Snowflake itself.
  The procedure's own scripting shell (`BEGIN TRANSACTION`, `DELETE`, `INSERT`, the run log) is
  asserted structurally; it embeds the executed query verbatim, and a test says so.
- **The trailer control total keeps its own story.** Tolerance on the per-file `control_total` DQ
  rule (taxonomy `FILE_CONTROL_TOTAL`) would change the per-source DQ renderer and the verification
  runner, and no real spec carries a value-total trailer field; it is not folded in here.
- **Eight of the nine reconciliation codes the taxonomy declared before this story stay unraised**
  (`POSITION_VALUE_MISMATCH`, `POSITION_NEGATIVE_LONG`, `POSITION_DUPLICATE_SECURITY`,
  `LOT_TOTAL_MISMATCH`, `TRANSACTION_AMOUNT_MISMATCH`, `TRANSACTION_FUTURE_DATE`,
  `TRANSACTION_SETTLE_BEFORE_TRADE`, `TRANSACTION_DUPLICATE_EVENT`) — they are checks within a record
  or a table, not identities between tables. Only `CASH_BALANCE_MISMATCH` is raised, plus the new
  `POSITION_QUANTITY_MISMATCH`. S7.1.6's "test per code" needs the rest reproduced; this story does
  not claim to.
- **Confirmations a real engagement owes, not guessed here:** that positions are as of trade date
  (`movement_date`), that `OTHER` should be `unreconcilable`, that no custodian's transaction feed
  carries corporate-action quantities, and the tolerances themselves. Each is one line of
  `reconciliation.yaml`.
- **Not built:** a Control-plane screen or queue for breaks (`QueueItemKind.BREAK` stays parity's),
  owners and assignment for a break, a schedule that calls `CONTROL.RECONCILE` after each custodian's
  run, and per-custodian tolerance overrides (the tolerance is per pack). The procedure runs
  `EXECUTE AS OWNER`, so, as with the exception procedures, nobody but the owner can call it until
  the foundation grants access.
