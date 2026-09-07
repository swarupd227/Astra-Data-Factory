# ADR 0027: A complete bundle: Terraform prerequisites, cited docs, PII in the catalog, lintable tests

Date: 2026-09-07
Status: Accepted
Story: S3.2.9 Terraform, docs, Atlan payloads, tests (E3, F3.2, WBS 2.3.10–2.3.13)

## Context

The bundle already rendered DDL, pipeline SQL, tasks, DMFs, tests, docs, an Atlan payload and provenance (ADR 0018). Four things were missing for it to be complete in the sense the backlog asks: infrastructure from the same config with a plan that is clean; documentation that cites every field, rule and DQ check; a catalog payload that carries PII on every column that holds it; and a way for CI to run the generated tests green before anything is deployed.

## Decision

1. **The bundle's Terraform is its infrastructure contract, not a second deployer.** Snowflake objects stay SQL steps (ADR 0018). Each source bundle renders `terraform/`, a root module with no resources: data sources with postconditions on what the bundle takes from the foundation: the tier warehouse, the BRONZE, SILVER, EXCEPTIONS and CONTROL schemas, the `CONTROL.PII` tag and the landing bucket. `terraform plan` is clean when the environment can hold the bundle and fails naming the missing piece otherwise. The deploy action plans every bundle root before deploying any bundle; CI validates and tests the roots with mocked providers, and the rendered tftest asserts both that a complete foundation plans clean and that a missing warehouse fails the plan. The outputs are what an operator hands the custodian: the landing URL, the pipe, the warehouse, the tasks.

2. **Docs cite everything.** The layout section lists every field of every record, header and trailer included, with position, picture, type and the spec citation. Rules carry the catalog citation as before. DQ checks carry a citation too: the config may state one, otherwise the check cites the field it measures (the trailer field of a control total, the field of a not-null or range check, the fields a unique or condition check names). Mappings show the PII category and the source field's citation.

3. **PII follows the mappings.** A source field mapped to a PII column of the canonical model carries that category: the Bronze record column and the Silver source column are bound to the `CONTROL.PII` tag in the rendered DDL, and the Atlan payload classifies them. Exception payloads and raw values hold the whole record, so they are `raw_record`, like the raw line. Every table asset carries the config's owner.

4. **Generated tests are linted in CI and run after every deploy.** `astra-data bundles lint` parses every test of every bundle, placeholders filled, with the Snowflake dialect of sqlglot and requires one SELECT statement; a renderer bug that leaves a dangling comma or an unbalanced bracket fails the pull request on the file. The tests themselves run against the environment in the deploy action, after the bundles and the syncs, which is where "green" is decided.

## Consequences

- The bundle grows by four files under `terraform/`; the manifest's steps and tests are unchanged, and `bundles check` and provenance cover the new files.
- Pack-level bundles (reference data, Gold) render no Terraform: every prerequisite they have is a prerequisite of some source bundle in the same environment.
- Column tags on dynamic tables (`ALTER DYNAMIC TABLE ... MODIFY COLUMN ... SET TAG`) are recreated with the table on every deploy, since the tables are replaced; the DDL re-binds them in the same script.
- The lint is a syntax check, not a semantic one: it cannot know a column exists. sqlglot is an optional extra (`astra-data[lint]`), included in `dev`.
- A CI runner needs Terraform and provider downloads for the bundle roots; the configs job installs it like the foundation job does.
