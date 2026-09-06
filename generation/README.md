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
  bundle.py                        bundle loading, placeholder rendering, deploy and test
  custodians.py                    delivery expectations and alert severities, from configs to CONTROL
  rejections.py                    the rejection taxonomy, from the domain pack to CONTROL
  reference_data.py                reference-data replication bundles rendered from the domain pack
  snowflake_connection.py          connection from the environment
  cli.py
tests/                             run against an in-memory executor; no account needed
```

See [configs/README.md](../configs/README.md) for the config layout and [releases/README.md](../releases/README.md) for the bundle contract.
