# Runbook: rendering and deploying the Silver CDM bundle

Story S7.1.1 (ADR 0075), opening epic E7 "Envestnet instance." The domain pack's canonical model,
already rendered by `astra-spec cdm render`, wrapped as a real release bundle so it deploys and is
tested the same way every other release bundle already is.

## Rendering

```bash
astra-data silver render --domains domains --releases releases
```

Writes `releases/<pack>-silver/` — `ddl/silver_tables.sql` and `tests/*.sql`, plus `manifest.yaml`
— from the domain pack's current (latest) CDM model version. Run this after any change to
`domains/<pack>/cdm/*.yaml`, and after running `astra-spec cdm render` itself (this bundle carries
that renderer's own output verbatim). `--domain <pack>` renders one pack only.

## Checking it is current

```bash
astra-data silver render --domains domains --releases releases --check
```

Fails (exit 1) and names every stale or missing file if the domain pack's CDM changed since the
bundle was last rendered — the same check CI runs on every pull request, alongside `astra-spec cdm
render --check`, `astra-data gold render --check` and `astra-data reference render --check`.

## Deploying and testing

Nothing new to run — `astra-data deploy --environment <env> releases` and
`astra-data test --environment <env> releases` already discover every bundle under `releases/`
generically (`astra_data.bundle.discover_bundles`), so once `custodial-silver` is committed it
deploys and its 18 key/reference/lookup tests run on every real deploy, exactly like
`custodial-gold` and `custodial-reference-data` already do — this is the same
`.github/actions/deploy-environment/action.yml` step that runs on every merge to `main`.

To check it locally against a real Snowflake target:

```bash
astra-data bundles check releases   # every bundle's manifest and files are valid
astra-data bundles lint releases    # every generated test parses as one Snowflake SELECT (needs astra-data[lint])
astra-data deploy --environment dev releases
astra-data test --environment dev releases
```

## Deciding

- **`not rendered; run astra-data silver render`**: the bundle directory is missing or incomplete
  — run the render command and commit the result.
- **`stale: the domain pack's CDM changed since it was rendered`**: a column, entity or key
  changed in `domains/<pack>/cdm/*.yaml` (or its own rendered `ddl.sql`/tests changed via
  `astra-spec cdm render`) since this bundle was last rendered — re-render and commit.
- **A key or reference test fails after a real deploy**: that is real, live data violating the
  canonical model's own key or foreign key — this is exactly what "keys enforced by tests" means
  once the bundle actually runs against a live schema, not a renderer bug.

## Notes

- Renders nothing new: `ddl/silver_tables.sql` and every `tests/*.sql` file are
  `astra_knowledge.cdm.render_ddl`/`render_tests`'s own real output, unchanged, for the pack's
  current model version (`DomainPack.latest`) — this module only wraps that output in the bundle
  shape `astra-data deploy`/`test` already understand.
- One step only (`ddl/silver_tables.sql`) — unlike Gold, there is no publish procedure or view:
  Silver tables are written directly by each source's own resolution step
  (`astra_data.render.resolve`), not by a scheduled publish.
