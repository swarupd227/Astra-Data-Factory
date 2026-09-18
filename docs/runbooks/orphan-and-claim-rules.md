# Runbook: configuring orphan policy and understanding claim precedence

Story S7.1.3 (ADR 0077). The four resolutions (account, security, transaction code, price) are
already real, deployed, set-based joins (ADR 0022) — this runbook covers the two things new here:
setting a custodian's own orphan grace period, and how a security's own identifiers "claim" a
resolution when more than one could match.

## Setting the orphan grace period

```yaml
resolution:
  account: { source: account_number, require_open: true }
  security: { by: [{ identifier: CUSIP, source: cusip }] }
  price: { when: missing, lookback_days: 5 }
  orphan_policy:
    grace_hours: 24   # default; how long a held row may wait before it is an aged concern
```

Add `orphan_policy.grace_hours` under `resolution` in the source's own config. It never changes
whether a row merges — an unresolved row is always held back and always raises its real rejection
code, the platform's Loader-parity design (never silently drop data). It only names, per
custodian, when a still-held row stops being routine and becomes an aged orphan worth escalating.
Render and check the change the same way as any other config edit:

```bash
astra-data render --specs specs --rules rules --domains domains --out releases configs/<custodian>/<source>.yaml
astra-data bundles check releases
```

The value lands in the deployed resolve procedure's own `COMMENT`, so it is visible against the
real object in Snowflake, not only in the source config.

## How claim precedence already works

`resolution.security.by` is an ordered list — the first identifier with any real match in the
security master decides the outcome: exactly one match resolves the security (that identifier
"claims" it); more than one is `SECURITY_AMBIGUOUS`; no match moves to the next identifier in the
list. This is not new — it is `astra_knowledge.patterns.reference_data.Replica.resolve`'s own
real, tested precedence (ADR 0015), which the rendered SQL joins the same way. To change which
identifier wins for a custodian, reorder `resolution.security.by` in that source's own config —
nothing else needs to change.

## Verifying a resolution reproduces the rejection code you expect

The seven rejection codes the four resolutions directly wire (`ACCOUNT_NOT_FOUND`,
`ACCOUNT_CLOSED`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `SECURITY_INACTIVE`,
`TRANSACTION_CODE_UNMAPPED`, `PRICE_MISSING`) all have real, executable, seeded-data tests:

- Account and security: `knowledge/tests/test_reference_data.py` (`Replica.resolve`, seeded with
  real identifier/account rows).
- Transaction code: `generation/tests/test_render_resolve.py` — seed real custodian codes through
  the compiled config's own `resolution.transaction_code.map`.
- Price: same file — `astra_data.render.resolve.resolve_price`/`resolved_price`, seeded with a
  price history and an as-of date.

Run `pytest knowledge/tests/test_reference_data.py generation/tests/test_render_resolve.py` to see
all seven reproduced. The other ten resolution-category codes in the taxonomy
(`ACCOUNT_FIRM_MISMATCH`, `ACCOUNT_AMBIGUOUS`, `SECURITY_IDENTIFIER_INVALID`,
`TRANSACTION_CODE_AMBIGUOUS`'s real detection, `PRICE_STALE`, `PRICE_ZERO`, `CURRENCY_UNKNOWN`,
`FIRM_NOT_FOUND`, `REFERENCE_DATA_STALE`, `REFERENCE_DATA_CONFLICT`) are not wired into any
renderer yet — a named gap for S7.1.6, not silently assumed covered.

## What is left for a real client engagement

Two genuine operational facts this repository cannot supply on its own, named plainly:

1. **Confirm the deployed `grace_hours` matches how long Envestnet actually tolerates an
   unresolved record** before it should be treated as urgent, rather than the safe 24-hour
   default.
2. **Confirm resolution's hold-everything design actually matches the legacy Loader's own real
   behavior** — no Loader Rejections reference (`loader-rejections.csv`) is in this repository yet
   to check against (`domains/custodial/README.md`).
