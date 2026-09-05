# Astra Data Factory

An Artizent platform where agents generate, test and operate data pipelines, migrations and data quality, and people approve. Give it a source description, a target profile and a domain pack; it produces running, tested, governed pipelines with the evidence behind them.

Product specification: [docs/product-spec-v0.2.md](docs/product-spec-v0.2.md). Backlog: [docs/backlog-v0.2.md](docs/backlog-v0.2.md). Decisions: [docs/adr](docs/adr).

## Repository layout

| Path | Plane | Contents |
|---|---|---|
| `infra/terraform/foundation` | Control (E1) | Per environment: Snowflake database, schemas, roles, tier-sized warehouses, Iceberg bucket and external volume, Open Catalog sync |
| `tools/opencatalog` | Control (E1) | Provisions and verifies Snowflake Open Catalog, which has no Terraform provider |
| `docs` | | Specification, backlog and architecture decision records |

Further planes (knowledge, generation, verification, agents, workbench) are added as their epics start.

## Working in this repository

- Git is the system of record. Every generated artifact and every approval lands here; the factory database never holds the only copy of anything.
- Nothing secret is committed. Credentials come from the environment or the client's secret manager.
- Each component has a `make check` target that runs without external accounts. CI runs it on every change.
