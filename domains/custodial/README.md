# Custodial domain pack

Canonical model, vocabulary, rule patterns, DQ patterns, rejection taxonomy and reference-data patterns for custodial and wealth data (product spec Section 4). Every client instance in this domain starts from this pack.

```
domains/custodial/
  glossary.yaml            the vocabulary: entity terms and concept terms
  rejections.yaml          the rejection taxonomy: every code with level, severity, owner, resolution
  loader-rejections.csv    the Loader Rejections reference (client material; not yet added), for parity
  reference-data.yaml      the reference-data feeds replicated into REFERENCE: security master, account cross-reference
  read-models.yaml         the Gold read models: consumer-shaped tables over the canonical model, published per custodian and business date behind a watermark
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

## Rejection taxonomy

[rejections.yaml](rejections.yaml) is every reason the factory rejects a file, a record or a value: the code, a name, a description, the level it reaches (`file`, `record`, `field`), its severity (`critical`: nothing from the file is loaded; `error`: the record is not loaded; `warning`: loaded and flagged), a category, the owner who resolves it (`custodian`, `data_engineer`, `steward`, `platform`), what they do, whether Exception Triage may do it without a person, and the Loader codes it reproduces. Codes are never renamed or deleted; one that no longer applies is marked `retired`.

The pattern library raises these codes on every problem it reports, and a test fails when a problem has no code or a code that is not here. `astra-data rejections sync` brings `CONTROL.REJECTION_CODES` in line with this file on every deploy, and the Exception entity's `REJECTION_CODE` column declares a lookup on that table, so the rendered test `exception_rejection_code_lookup.sql` returns any exception row whose code does not exist.

**Parity with the Loader.** Export the Loader Rejections reference from the client's document to `loader-rejections.csv` (a header row naming `code` and `description`, one legacy code per line) and map each legacy code in a code's `loader_codes`. Once the file is in the pack, validation fails for every Loader code no rejection code reproduces and for every Loader code named here that the reference does not list; `astra-spec rejections parity --domain custodial` prints the same report. The reference is not in the repository yet, so the parity criterion of S2.3.2 is enforced by the tooling but not yet satisfied by data.

| Command | What it does |
|---|---|
| `astra-spec rejections list [--domain custodial] [--level file|record|field] [--owner ...]` | The codes with level, severity, owner and Loader codes |
| `astra-spec rejections parity --domain custodial [--reference file.csv]` | Loader codes reproduced, not reproduced, and named but not in the reference |

## Reference data

[reference-data.yaml](reference-data.yaml) declares the reference data the pack replicates so that resolution is a join against a local replica, never a call to the source system: the security master (SOS) and the account cross-reference (CAS). A feed says what it carries and how it is keyed, the folder under the landing bucket's `reference/` prefix where the source drops full snapshots (CSV, header row, the columns in order), its cron schedule and how often a success is expected, which entity it resolves and through which identifiers in order, and which taxonomy codes resolution raises (not found, ambiguous, conflict).

`astra-data reference render` turns the feeds into the bundle [releases/custodial-reference-data](../../releases/custodial-reference-data): replica, staging, change and conflict tables in `REFERENCE`; a procedure per feed that loads the newest snapshot files, computes the delta (inserted, updated, deleted, with the row before and after in `<TABLE>_CHANGES`), brings the replica in line and records the run with its row counts in `CONTROL.REFERENCE_DATA_RUNS`; identifier views for one-join resolution; a serverless task per feed; and tests. CI fails when the bundle is stale. `astra-data reference sync` keeps `CONTROL.REFERENCE_FEEDS` in line so the foundation alerts when a feed has no successful run within its expected interval. `astra-verify reference status` shows each feed's last run, counts and delta. The reference implementation is `astra_knowledge.patterns.reference_data` ([ADR 0015](../../docs/adr/0015-reference-data-replication.md)).

Snapshots must arrive under a new file name each time: the replication loads only files no earlier run has loaded, and a run that finds none is recorded as `skipped`.

| Command | What it does |
|---|---|
| `astra-spec reference list [--domain custodial]` | The feeds: what they resolve, how they arrive, when they run |
| `astra-data reference render [--check]` | Writes the replication bundle; `--check` fails when it is stale |
| `astra-data reference sync --environment <env>` | Syncs `CONTROL.REFERENCE_FEEDS` |
| `astra-verify reference status --environment <env>` | Last run, row counts, delta and freshness per feed |

## Still to come in this pack

- Rule and DQ patterns (F2.4, F3.3).
