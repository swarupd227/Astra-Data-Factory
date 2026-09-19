# Runbook: reconciling positions and cash, and setting the tolerances

Story S7.1.5 (ADR 0079). `CONTROL.RECONCILE` checks two identities per custodian and business date
over the canonical tables and records every break it finds, categorised.

## What is checked

**Position identity.** For each account that has an earlier snapshot, a position's quantity must
equal its quantity in the latest earlier snapshot (nothing, if it was not held) plus the movement
of the `ACTIVE` transactions dated after that snapshot, up to and including the business date. A
transaction moves quantity by the direction of its type times the size of its quantity
(`BUY`/`TRANSFER_IN` add, `SELL`/`TRANSFER_OUT` subtract; the cash-only types do not touch it).

**Cash balance identity.** For each account, currency and balance type that has an earlier
balance, the balance must equal that balance plus the net amount of the `ACTIVE` transactions of
the same currency in the same window. A `SETTLED` balance moves on the settle date, a `TRADE_DATE`
balance on the trade date.

An account with no earlier snapshot is a baseline: nothing is said about it until the next one.

## Running it

```sql
CALL CONTROL.RECONCILE('pershing', '2026-09-17');
```

It replaces whatever an earlier run found for that custodian and date, in one transaction, so a
break that has been fixed is gone on the next run. The call needs a grant (see "Not yet in place").

```sql
SELECT * FROM CONTROL.RECONCILIATION_BREAKS WHERE CUSTODIAN_ID = 'pershing' AND AS_OF_DATE = '2026-09-17';
SELECT * FROM CONTROL.RECONCILIATION_RUNS   WHERE CUSTODIAN_ID = 'pershing' ORDER BY STARTED_AT DESC;
```

## Reading a break

`EXPECTED` is `PRIOR_VALUE` plus `MOVEMENT`; `ACTUAL` is what the business date reports (null if
it is not there); `DIFFERENCE` is actual (zero if absent) minus expected. `REJECTION_CODE` is
`POSITION_QUANTITY_MISMATCH` or `CASH_BALANCE_MISMATCH`, so the taxonomy's owner and resolution
text apply. The category says what kind of break it is:

| Category | Meaning | Where to look |
|---|---|---|
| `mismatch` | in both snapshots, and prior + transactions does not equal current | missing, duplicated or late transactions; a wrong quantity sign |
| `appeared` | not in the earlier snapshot, and the transactions do not explain it | a purchase missing from the transaction file; a transfer in |
| `disappeared` | prior + transactions says it should be there and it is not (or, for a balance, an emptied one is not reported) | a sale that never arrived; a position missing from the file |
| `unverifiable` | the identity cannot be evaluated: see `UNVERIFIABLE_COUNT` | see below |

**Unverifiable** means the equation is incomplete, so it is reported rather than passed or failed.
The causes are a transaction of an `unreconcilable` type (`CORPORATE_ACTION`, `ADJUSTMENT`,
`OTHER`: their effect on quantity cannot be computed from a row), a type the model does not know,
a buying or selling row with no quantity, and — for a movement date that can be missing, such as
the settle date of a `SETTLED` balance — a transaction dated in the window by trade date that has
no such date.

## Setting the tolerances and the domain's choices

Everything the checks assume is in `domains/<pack>/reconciliation.yaml`:

```yaml
tolerances:
  quantity: { kind: absolute, epsilon: 0.5 }
  amount:   { kind: decimal_places, places: 2 }
position_identity:
  movement_date: trade_date      # or settle_date
  movements: { BUY: 1, SELL: -1, ..., CORPORATE_ACTION: unreconcilable }
cash_identity:
  - { balance_type: SETTLED, movement_date: settle_date }
```

- **Tolerance per field type.** `quantity` is applied to positions, `amount` to cash. Each is
  `exact`, `decimal_places` (`places`: both sides are rounded and compared), `absolute` (`epsilon`:
  the largest difference that still agrees) or `relative` (`epsilon`: a fraction of the larger
  magnitude). A type left out is exact. The committed pack is exact for both, because no client
  threshold has been agreed; set the client's own here.
- **Movements.** Every transaction type of the model must be named once — `1`, `-1`, `0`, or
  `unreconcilable`. Adding a type to the model makes the pack refuse to load until it is decided.
- **Movement date.** The date that places a transaction between two snapshots.

After any change:

```bash
astra-data reconciliation render          # rewrites releases/<pack>-reconciliation/
astra-data reconciliation render --check  # what CI runs: fails if the committed bundle is stale
```

## Checking a deployed environment

`astra-data test --environment <env> releases` runs five invariants over the findings: every break
carries its check's taxonomy code, has a valid check and category and is unverifiable exactly when
its count says so, is unique, lies outside its tolerance, and adds up (expected is prior plus
movement; the difference is actual minus expected). Each returns the offending rows.

## How it is verified

The reference implementation is `astra_knowledge.patterns.reconciliation`. The renderer's tests run
the rendered SQL, converted to DuckDB's dialect, over seeded copies of `SILVER.POSITION`,
`SILVER.TRANSACTION` and `SILVER.CASH_BALANCE` and require it to find exactly what the reference
implementation finds — on hand-seeded days covering every category and on 320 randomised worlds
under four configurations. `pip install duckdb` (a dev extra of `astra-data`) to run them.

## Not yet in place

- Calling `CONTROL.RECONCILE` needs a grant: it runs `EXECUTE AS OWNER`, so until the foundation
  grants USAGE to a role, only the owner can call it. Nothing yet calls it after a custodian's run.
- The scripting shell of the procedure (`BEGIN TRANSACTION`, `DELETE`, `INSERT`, the run log) is
  checked from its text; the query it embeds is executed by the tests, but on DuckDB, not
  Snowflake. Run it against a sandbox before relying on it.
- No screen or queue shows breaks, and a break has no owner or assignee yet.
- Eight other reconciliation codes in the taxonomy (`POSITION_VALUE_MISMATCH`,
  `LOT_TOTAL_MISMATCH`, `TRANSACTION_AMOUNT_MISMATCH`, …) are still not raised by anything.
- Confirm with the client before relying on the defaults: positions as of trade date, and `OTHER`
  as unreconcilable.
