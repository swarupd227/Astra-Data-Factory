# releases

Release bundles: everything rendered for one source, committed as one reviewable unit (product spec Appendix A). The deploy pipeline (S1.2.2) deploys every bundle here to dev on merge and to qa on approval. `astra-data render` renders a bundle per config; `astra-data reference render` renders each domain pack's reference-data bundle. The example config is never deployed, so its bundle is rendered and checked in CI but not committed here.

## Bundle contract, version 0

```
releases/<bundle>/
  manifest.yaml
  ddl/*.sql  pipeline/*.sql  dq/*.sql   # steps: Bronze and Silver DDL, pipe, lines view, parse dynamic tables, intake, merge, process, tasks, data metric functions
  tests/**/*.sql                         # generated tests
  docs/<source>.md                       # the source, its layout, mappings and rules
  atlan/<source>.json                    # catalog assets and lineage for Atlan
  PROVENANCE.json                        # inputs and their digests, the digest of every file
```

`manifest.yaml`:

```yaml
bundle: pershing-position      # equals the directory name
version: "2026.09"
source: pershing_position      # source id of the config it was rendered from
steps:                         # run in this order, each file may hold several statements
  - ddl/bronze_pershing_position.sql
  - pipeline/bronze_parse.sql
  - pipeline/silver_merge.sql
  - pipeline/tasks.sql
  - dq/dmf_pershing_position.sql
tests:                         # files or globs; a test returns failing rows, zero rows is a pass
  - tests/unit/*.sql
```

SQL is environment-neutral. It names the target through placeholders filled at deploy time:

| Placeholder | dev example |
|---|---|
| `{{ DATABASE }}` | `ASTRA_DEV` |
| `{{ ENVIRONMENT }}` | `dev` |
| `{{ PREFIX }}` | `ASTRA` |
| `{{ WAREHOUSE_SIMPLE }}`, `{{ WAREHOUSE_MEDIUM }}`, `{{ WAREHOUSE_COMPLEX }}` | `ASTRA_DEV_WH_SIMPLE`, ... |

Steps must be idempotent (`CREATE OR REPLACE`, `IF NOT EXISTS`, `MERGE`), because the same bundle is applied to every environment and re-applied on every deploy.

## Commands

```bash
astra-data bundles check releases                  # manifest, files and placeholders; no connection
astra-data deploy --environment dev releases       # run every bundle's steps
astra-data test   --environment dev releases       # run every bundle's tests
```

Deploy renders every step before running any, so an unknown placeholder never leaves a bundle half applied. A failing step stops that bundle and names the step and the database's error.
