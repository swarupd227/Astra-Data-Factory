# Custodial domain pack

Canonical model, vocabulary, rule patterns, DQ patterns, rejection taxonomy and reference-data patterns for custodial and wealth data (product spec Section 4). Every client instance in this domain starts from this pack.

```
domains/custodial/
  glossary.yaml            the vocabulary: entity terms and concept terms
  cdm/<major>.<minor>.yaml one file per model version
  cdm/migrations/<v>.md    migration note, required for every new major version
  cdm/rendered/<v>/        DDL and key tests rendered from the model (checked in CI)
```

## Canonical data model

Nine entities, each defined once by its glossary term and identified by a key that is its reconciliation identity:

| Entity | Table | Key |
|---|---|---|
| Firm | `FIRM` | `FIRM_ID` |
| Account | `ACCOUNT` | `CUSTODIAN_ID, ACCOUNT_NUMBER` |
| Security | `SECURITY` | `SECURITY_ID` |
| Position | `POSITION` | `CUSTODIAN_ID, ACCOUNT_NUMBER, SECURITY_ID, AS_OF_DATE` |
| Lot | `LOT` | `CUSTODIAN_ID, ACCOUNT_NUMBER, SECURITY_ID, LOT_ID, AS_OF_DATE` |
| Transaction | `TRANSACTION` | `CUSTODIAN_ID, TRANSACTION_ID` |
| Price | `PRICE` | `SECURITY_ID, PRICE_DATE, PRICE_SOURCE` |
| Cash Balance | `CASH_BALANCE` | `CUSTODIAN_ID, ACCOUNT_NUMBER, CURRENCY, BALANCE_TYPE, AS_OF_DATE` |
| Exception | `EXCEPTION` | `EXCEPTION_ID` |

Every table also carries the lineage columns (`SOURCE_SYSTEM`, `SOURCE_FILE`, `SOURCE_LINE`, `CONFIG_VERSION`, `LOADED_AT`, `UPDATED_AT`). Columns tagged `pii` are masked by the foundation's policies once the PII tag is applied (S1.2.5).

Definitions live in the glossary only. An entity names its term; the validator rejects an entity whose term is missing or is not an entity term, and a glossary entity term that no model version uses. The term's definition becomes the table comment in the rendered DDL, so the model, the glossary and the database say the same thing.

## Versioning rule

Versions are `MAJOR.MINOR`, one file each, and each version follows from the one before it.

- A **breaking** change needs a new major version (`2.0`) and a migration note at `cdm/migrations/2.0.md`, named in the model's `migration` field. Breaking: an entity or column removed or renamed, a key changed, a column made required, a type changed other than widening, lineage columns changed, the target schema changed.
- An **additive** change needs a new minor version (`1.1`): a new entity, a new optional column, a wider type, a changed description or code list.

`astra-spec cdm validate` enforces the rule by diffing each version against its predecessor; `astra-spec cdm diff --from 1.0 --to 2.0` shows the classified changes. A version that skips a number, or a major version without a migration note, fails validation.

## Rendered DDL

`astra-spec cdm render` writes, for every version, `cdm/rendered/<version>/ddl.sql` (one `CREATE ICEBERG TABLE IF NOT EXISTS` per entity, on the environment database's external volume, with `{{ DATABASE }}` as the placeholder the deploy pipeline fills) and `cdm/rendered/<version>/tests/*.sql` (one uniqueness test per key and one orphan test per reference; a test returns failing rows). The rendered files are committed so a model change is reviewed as a DDL diff; CI runs `astra-spec cdm render --check` and fails when they are stale. S7.1.1 deploys them as a release bundle.

## Still to come in this pack

- Rejection taxonomy as data (S2.3.2).
- Reference-data replication patterns for the security master and account cross-reference (S2.3.3).
- Rule and DQ patterns (F2.4, F3.3).
