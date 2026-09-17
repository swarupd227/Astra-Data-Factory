# ADR 0075: Wrap the canonical model's own rendered DDL as a release bundle — render nothing new

Date: 2026-09-17
Status: Accepted
Story: S7.1.1 Silver CDM on Iceberg (E7, F7.1, WBS 2.7.1)

## Context

This is the first story of a new epic, E7 "Envestnet instance" — the actual client instance the
factory produces, as opposed to the reusable factory itself (E1–E6). Before designing anything,
research confirmed that most of what this story asks for already exists, one plane over from
where the story's own wording ("Silver tables," "the target model") first points:

- `astra_knowledge.cdm.render_ddl(model)` already renders `CREATE ICEBERG TABLE IF NOT EXISTS` for
  every entity of a domain pack's canonical model, in `SILVER`, with keys `NOT NULL`, per-column
  comments and `{{ DATABASE }}` filled at deploy time (ADR 0002: tables inherit the database's own
  external volume and Open Catalog integration).
- `astra_knowledge.cdm.render_tests(model)` already renders, per entity: a uniqueness test on its
  key, a reference test for every FK, and a lookup test for every coded column — exactly "keys
  enforced by tests," generically, for any entity any domain pack declares.
- Both are already committed and already CI-verified fresh (`astra-spec cdm render --check`) for
  the real custodial domain pack: `domains/custodial/cdm/rendered/1.0/ddl.sql` and 18 real test
  files, covering all nine entities (Firm, Account, Security, Position, Lot, Transaction, Price,
  Cash Balance, Exception), each with a real declared key.

The genuine gap: nothing in the actual deploy pipeline ever executes that DDL. `.github/actions/
deploy-environment/action.yml` runs, on every merge to `main`, against dev then qa:
`astra-data deploy --environment ... releases` and `astra-data test --environment ... releases` —
both of which walk every bundle under `releases/` (`astra_data.bundle.discover_bundles`, driven
entirely by a `manifest.yaml` per directory) and execute it for real through a live `Executor`. The
canonical CDM DDL was never one of those bundles — only two existed, `custodial-gold` and
`custodial-reference-data`, both rendered by their own per-domain-pack modules (`astra_data.gold`,
`astra_data.reference_data`) that already follow this exact bundle shape.

## Decision

**Add a third per-domain-pack renderer, `astra_data.silver`, that wraps `astra_knowledge.cdm`'s
own already-correct, already-tested `render_ddl`/`render_tests` output in the identical bundle
shape `astra_data.gold`/`astra_data.reference_data` already established — reimplementing no DDL or
test logic at all.**

1. `render_bundle(pack)` calls `render_ddl(pack.latest)` and `render_tests(pack.latest)` directly
   and writes their output unchanged into `ddl/silver_tables.sql` and `tests/*.sql` — including
   the DDL's own `-- Rendered by astra-spec cdm render` header, since this bundle carries that
   real output verbatim rather than re-rendering it under a second name. `pack.latest` — the
   current CDM model version, an existing `DomainPack` property — is the one version actually
   meant to exist as the real Silver schema; a domain pack's prior model versions are historical,
   not separately deployed.
2. A `manifest.yaml` in the same schema (`bundle`, `version` as a content digest, `source`,
   `steps`, `tests`) that `astra_data.bundle.load_bundle` already validates and
   `astra-data deploy`/`test`/`bundles check`/`bundles lint` already discover generically — none of
   those four commands, nor the deploy action, needed a single line changed to pick this bundle up.
3. `astra-data silver render [--check]`, wired exactly like `astra-data gold render`, and one new
   CI step, `astra-data silver render --check`, alongside the existing Gold and reference-data
   freshness checks — the only new CI surface this story adds.
4. The real `releases/custodial-silver/` bundle is rendered and committed, the same way
   `custodial-gold`/`custodial-reference-data` already are.

## Consequences

- The real Envestnet instance's Silver schema — all nine entities, keys enforced by 18 real
  tests — is now created and tested on every real deploy to dev and qa (and, once those workflow
  jobs exist, uat and prod), through machinery this story added zero new deploy-time code to: the
  same `astra-data deploy`/`test` that already run today.
- "Keys enforced by tests" was already true as generic, CI-verified renderer output before this
  story; what changed is that it is now also true against a real, live deployed schema, not only
  a hand-run verification sandbox.
- If `astra_knowledge.cdm`'s own renderer or the domain pack's model ever changes, `astra-data
  silver render --check` fails the build the same way `gold render --check`/`reference render
  --check` already do — this bundle can never silently drift from the canonical model it wraps.
- This module renders only the domain pack's *current* model version. A future migration between
  CDM versions (adding a column, a new entity) is `astra-spec cdm`'s own versioning story, not
  this one; `astra-data silver render` re-renders the same bundle from whatever `pack.latest` is
  at the time.
