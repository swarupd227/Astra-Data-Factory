# astra-data

The generation plane of Astra Data Factory (product spec Section 5, "Astra Data"). Today it validates source configs and deploys and tests release bundles; the config compiler and the Snowflake renderers (E3) are added here.

```bash
cd generation
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add ,deploy to talk to Snowflake
pytest
```

## Commands

| Command | What it does | Needs |
|---|---|---|
| `astra-data validate [--specs specs] [--rules rules] [paths]` | Validates config files against the config schema and their references; with the registry and the catalog, spec versions must exist and rule ids must exist and not be rejected | nothing |
| `astra-data compile [--specs specs] [--rules rules] [--domains domains] [paths]` | Validates, then resolves every reference of each config (spec version, pattern, target profile, domain pack and model, catalog rules, mapping columns, fields and transforms) into the compiled model the renderers use; `--format json` prints it ([ADR 0017](../docs/adr/0017-config-compiler.md)) | nothing |
| `astra-data render [--out releases] [--check] [paths]` | Compiles each config and renders every artifact into `releases/<bundle>/`: Bronze DDL (raw lines, file registry), Silver DDL (the logical record as merged, the source's exceptions), the source's Snowpipe, pipeline SQL (lines view, parse dynamic tables typed from the spec with problems and per-file metadata, intake, the Silver MERGE stage honouring mode, pairing and deduplication, the resolution stage that projects into the canonical entity with platform identifiers, process, tasks), data metric functions, tests, docs, an Atlan payload and PROVENANCE.json. Byte-identical for an unchanged config; `--check` fails when a committed bundle is stale ([ADR 0018](../docs/adr/0018-release-bundle-rendering.md)) | nothing |
| `astra-data bundles check [releases]` | Checks every release bundle's manifest, files and placeholders | nothing |
| `astra-data deploy --environment <env> [releases]` | Runs every bundle's steps against the environment | Snowflake |
| `astra-data test --environment <env> [releases]` | Runs every bundle's tests; a test passes when it returns no rows | Snowflake |
| `astra-data custodians render --environment <env> [paths]` | Folds the configs' `delivery` and `alerts` blocks into one row per custodian and prints the SQL that syncs `CONTROL.CUSTODIANS` | nothing |
| `astra-data custodians sync --environment <env> [paths]` | Applies that SQL in one transaction | Snowflake |
| `astra-data rejections render --environment <env> [--domains domains]` | Prints the SQL that syncs each domain pack's `rejections.yaml` into `CONTROL.REJECTION_CODES` | nothing |
| `astra-data rejections sync --environment <env> [--domains domains]` | Applies that SQL in one transaction; codes that left the taxonomy are retired, not deleted | Snowflake |
| `astra-data reference render [--check]` | Renders each domain pack's reference-data replication bundle into `releases/<pack>-reference-data/`; `--check` fails when it is stale (CI runs this) | nothing |
| `astra-data reference sync --environment <env> [--dry-run]` | Brings `CONTROL.REFERENCE_FEEDS` in line with the packs' feeds so the stale detector knows each feed's expected interval | Snowflake |

`--format github` prints workflow annotations, which is how a failing pull request shows each problem on its file and line. `--format json` is for other tools. Exit code 0 means nothing wrong, 1 means problems or failing tests, 2 means a usage or connection error.

Snowflake connection settings come from the environment in the same names the Terraform provider uses: `SNOWFLAKE_ORGANIZATION_NAME` and `SNOWFLAKE_ACCOUNT_NAME` (or `SNOWFLAKE_ACCOUNT`), `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY` (PEM text) or `SNOWFLAKE_PRIVATE_KEY_PATH`, and optionally `SNOWFLAKE_ROLE` and `SNOWFLAKE_WAREHOUSE`.

## Layout

```
src/astra_data/
  schemas/config-v0.schema.json    the config schema, versioned by config_version
  schemas/bundle-v0.schema.json    the release bundle manifest schema
  yamlsource.py                    YAML loading that keeps line numbers and rejects duplicate keys
  validate.py                      schema validation plus reference checks, plain-language messages
  compiler.py                      the config compiler: references resolved, mapping types checked, provenance recorded
  transforms.py                    the transform vocabulary mappings may use, with argument and type checks
  targets.py                       target profiles the factory has renderers for
  render/                          renderers per artifact: bronze (raw lines, pipe, file registry, lines view, intake, process), parse (dynamic tables from the spec's offsets, pictures, formats and codes; problems; file metadata), merge (Silver table, exceptions table, the MERGE procedure: refresh replaces the scope, update merges on keys, pairing, first row kept; ADR 0021), resolve (set-based joins against the reference replicas for account, security, transaction code and price; exceptions with the configured codes; projection into the canonical entity; ADR 0022), tasks, dq, tests, docs, atlan; bundle assembly and provenance. Every stage writes rejected rows to EXCEPTIONS.<SOURCE> as NEW with the full source record as payload, and the run ledger BRONZE.<SOURCE>_RUNS counts what each run registered, merged, projected and rejected; a rendered test checks the ledger against the store (ADR 0023)
  bundle.py                        bundle loading, placeholder rendering, deploy and test
  custodians.py                    delivery expectations and alert severities, from configs to CONTROL
  rejections.py                    the rejection taxonomy, from the domain pack to CONTROL
  reference_data.py                reference-data replication bundles rendered from the domain pack
  snowflake_connection.py          connection from the environment
  cli.py
tests/                             run against an in-memory executor; no account needed
```

See [configs/README.md](../configs/README.md) for the config layout and [releases/README.md](../releases/README.md) for the bundle contract.
