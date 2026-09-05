# Astra Data Factory

An Artizent platform where agents generate, test and operate data pipelines, migrations and data quality, and people approve. Give it a source description, a target profile and a domain pack; it produces running, tested, governed pipelines with the evidence behind them.

Product specification: [docs/product-spec-v0.2.md](docs/product-spec-v0.2.md). Backlog: [docs/backlog-v0.2.md](docs/backlog-v0.2.md). Decisions: [docs/adr](docs/adr).

## Repository layout

| Path | Plane | Contents |
|---|---|---|
| `configs` | Knowledge (E2) | Source configs, one YAML file per source, validated on every pull request |
| `releases` | Generation (E3) | Release bundles rendered from configs; deployed by the pipeline |
| `generation` | Generation (E3) | `astra-data`: config validation, bundle deploy and generated tests. The compiler and renderers grow here. |
| `verification` | Verification (E4) | `astra-verify`: ephemeral sandboxes per task. Dry-runs, golden replay and parity grow here. |
| `infra/terraform/foundation` | Control (E1) | Per environment: Snowflake database, schemas, roles, tier-sized warehouses, Iceberg bucket and external volume, Open Catalog sync, landing zone with Snowpipe and file load log |
| `infra/terraform/bootstrap` | Control (E1) | Once per AWS account: the bucket that holds Terraform state for every environment |
| `tools/opencatalog` | Control (E1) | Provisions and verifies Snowflake Open Catalog, which has no Terraform provider |
| `.github` | Control (E1) | `ci` on every pull request; `deploy` to dev on merge and to qa on approval |
| `docs` | | Specification, backlog, architecture decision records and runbooks |

Further planes (agents, workbench) are added as their epics start.

## Working in this repository

- Git is the system of record. Every generated artifact and every approval lands here; the factory database never holds the only copy of anything.
- Nothing secret is committed. Credentials come from the environment or the client's secret manager.
- `make check` at the root runs everything CI runs on a pull request, without accounts. Each component has its own `make check` too.
- A merge to main deploys to dev; qa follows once a reviewer approves. See [docs/runbooks/ci-cd-setup.md](docs/runbooks/ci-cd-setup.md).
