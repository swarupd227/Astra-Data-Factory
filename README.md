# Astra Data Factory

An Artizent platform where agents generate, test and operate data pipelines, migrations and data quality, and people approve. Give it a source description, a target profile and a domain pack; it produces running, tested, governed pipelines with the evidence behind them.

Product specification: [docs/product-spec-v0.2.md](docs/product-spec-v0.2.md). Backlog: [docs/backlog-v0.2.md](docs/backlog-v0.2.md). Decisions: [docs/adr](docs/adr).

## Repository layout

| Path | Plane | Contents |
|---|---|---|
| `core` | shared | `astra-core`: line-aware YAML, problem reporting, schema error wording, Snowflake connection. Every plane depends on it. |
| `specs` | Knowledge (E2) | The spec registry: one directory per layout, one file per version, validated on every pull request |
| `knowledge` | Knowledge (E2) | `astra-spec`: the registry, resolution of the version in force, and later patterns, domain packs and the rule catalog |
| `domains` | Knowledge (E2) | Domain packs: glossary, canonical data model versions with rendered DDL and key tests, the rejection taxonomy and the reference-data feeds |
| `rules` | Knowledge (E2) | The rule catalog: one file per business rule with citation, class, owner, status and its history; configs reference rules by id |
| `configs` | Knowledge (E2) | Source configs, one YAML file per source, validated against the schema, the spec registry and the rule catalog on every pull request |
| `assessments` | Discovery (WBS 1.4) | Assessment memos with their evidence: the Normalizer decision's Spark transformers, their Snowpark Connect versions, recorded changes, samples, expected rows and harness results |
| `golden` | Verification (E4) | Golden datasets: per pilot custodian, how the legacy path is replayed, the index of every captured version (hash, source files, store reference), and how a captured output compares with the lakehouse (keys, fields, tolerances); the data lives in the golden bucket, read-only |
| `migrations` | Generation (E3) | Historical migrations driven through SnowConvert AI: one file per SQL Server source naming the schemas, the archive-store schema and the command line of each phase; results and logs land with the release |
| `releases` | Generation (E3) | Release bundles rendered from configs and domain packs (reference-data replication, Gold read models and the watermark) and written by migration runs (converted archive-store DDL with logs and results); deployed by the pipeline |
| `generation` | Generation (E3) | `astra-data`: config validation, bundle deploy and generated tests. The compiler and renderers grow here. |
| `verification` | Verification (E4) | `astra-verify`: ephemeral sandboxes per task, dry runs of a drafted config, golden dataset capture, replay of a config change against captured history, the parity engine and its report across a dual-run cycle window, the DQ runner and its per-entity scores, a connection sheet for the client's own DQ tool, the 3x volume test, chaos scenarios proving recoverability, and the Snowpark Connect assessment harness. |
| `infra/terraform/foundation` | Control (E1) | Per environment: Snowflake database, schemas, roles, tier-sized warehouses, Iceberg bucket and external volume, Open Catalog sync, landing zone with Snowpipe and file load log |
| `infra/terraform/bootstrap` | Control (E1) | Once per AWS account: the bucket that holds Terraform state for every environment |
| `tools/opencatalog` | Control (E1) | Provisions and verifies Snowflake Open Catalog, which has no Terraform provider |
| `.github` | Control (E1) | `ci` on every pull request; `deploy` to dev on merge and to qa on approval |
| `docs` | | Specification, backlog, architecture decision records and runbooks |

Further planes (agents, workbench) are added as their epics start.

## Working in this repository

- Git is the system of record. Every generated artifact and every approval lands here; the factory database never holds the only copy of anything.
- Nothing secret is committed. Secret values live in the client's secret manager and are read by the pipeline at run time; a secret scan runs on every pull request and fails the build if one slips in.
- PII columns are tagged, masked for non-privileged roles, and their reads are recorded. See [ADR 0008](docs/adr/0008-secrets-access-history-masking.md).
- `make check` at the root runs everything CI runs on a pull request, without accounts. Each component has its own `make check` too.
- A merge to main deploys to dev; qa follows once a reviewer approves. See [docs/runbooks/ci-cd-setup.md](docs/runbooks/ci-cd-setup.md).
