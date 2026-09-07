# ADR 0022: Resolution is set-based joins against the replicas, configured per source, with the taxonomy's codes

Date: 2026-09-07
Status: Accepted
Story: S3.2.4 Resolution steps (E3, F3.2, WBS 2.3.5)

## Context

The Loader resolves four things record by record: the custodian's account number to a platform account, the security by whichever identifier the custodian sends, the custodian's transaction code to a canonical type, and a price when the custodian sends none. The reference data is replicated (ADR 0015) so that these can be joins, the taxonomy names the codes each failure raises (ADR 0014), and the merge (ADR 0021) leaves the source's logical record in Silver waiting to become canonical rows.

## Decision

1. **Resolution is a config block, compiled and checked.** `resolution.account`, `resolution.security`, `resolution.transaction_code` and `resolution.price` say which source field carries the custodian's identifier, which feed and identifiers to try, the transaction-code map and the price lookback, and the rejection code each failure raises. Codes default to the feed's (`ACCOUNT_NOT_FOUND`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `TRANSACTION_CODE_UNMAPPED`, `PRICE_MISSING`) and must be in the taxonomy; identifiers must be identifiers of the feed; source fields must be fields of the spec; canonical transaction types must be the model's.

2. **A source maps into one canonical entity, completely.** Mappings may carry a constant instead of a source field (a position type, a currency), typed by the target column. The compiler checks that every key and required column of the entity is produced by a mapping, a constant, the resolution (`ACCOUNT_ID`, `FIRM_ID`, `SECURITY_ID`, `CUSTODIAN_SECURITY_ID`, `TRANSACTION_TYPE`, `CUSTODIAN_TRANSACTION_CODE`, `PRICE`) or the stage (`CUSTODIAN_ID` and the lineage columns), and names the columns that are missing.

3. **The resolve stage is a rendered procedure of set-based joins.** This run's active Silver rows join the account cross-reference on the custodian and account number, the security master's identifier view once per configured identifier (first identifier with exactly one match resolves; several is ambiguous; none is not found, the order the pattern library's `Replica.resolve` uses), the transaction-code map as a VALUES table, and `SILVER.PRICE` within the lookback for the security. No stage calls a source system.

4. **Every failure is an exception row with the rejected row as payload.** `ACCOUNT_NOT_FOUND`, `ACCOUNT_CLOSED`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `TRANSACTION_CODE_UNMAPPED` and `PRICE_MISSING` hold the row back; `SECURITY_INACTIVE` is raised as a warning and the row is projected unless the config requires active securities. Exceptions carry the entity, the field at fault, the record key as text, the file and line, the config digest and the run, in the same table the merge writes (ADR 0021).

5. **Rows that resolve are merged into the canonical entity on its key.** The projection applies the mappings (constants typed, transforms at projection: trim, upper, to_date, nullif, negate; implied decimals were realised by the parse from the spec's picture and pass through), the resolved identifiers, the custodian id and the lineage columns, and merges on the entity's key so a re-delivered file updates rather than duplicates.

## Consequences

- The example config maps every required Position column and resolves account, security by CUSIP and price; the security master gains the OCC option symbol as an identifier so listed options resolve the same way.
- The bundle gains `pipeline/<source>_resolve.sql` as the third stage of the process procedure. The canonical tables (`SILVER.POSITION`, `SILVER.PRICE`, ...) are deployed by the CDM bundle of S7.1.1; until then the stage's targets are the rendered DDL under the domain pack.
- S7.1.3 configures the four resolutions to Loader behaviour with the client's orphan and claim rules; S7.1.6 reproduces every code on seeded data.
- The exception store (S3.2.5) gives these rows their workflow; the Exception Triage agent reads the payload and the code.
