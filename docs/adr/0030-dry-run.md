# ADR 0030: A dry run is the deploy pipeline's own path, in a sandbox, with a report and no trace left

Date: 2026-09-08
Status: Accepted
Story: S4.1.1 Dry-run a config in a sandbox (E4, F4.1, WBS 2.4.1)

## Context

A BSA drafting a config has sample files from the custodian and one question: what would the pipeline make of them? Until now the answer came from reading the rendered SQL or from a deploy to dev, which is where mistakes should not be found. The verification plane already has ephemeral sandboxes (ADR 0006, S1.2.3); what was missing was a run that takes samples through the same bundle the pipeline would deploy and reads back the numbers a BSA needs.

## Decision

1. **The dry run uses the pipeline's own artifacts.** `astra-verify dryrun` compiles and renders the config with the same registry, catalog and packs as `astra-data render`, refusing it with the same problems. A dry run never runs code the deploy would not.

2. **The sandbox holds what the bundle needs and nothing the sandbox cannot have.** The sandbox database gets the standard schemas plus REFERENCE; the control tables the bundle writes are created `LIKE` the environment's, so their shape is the foundation's, and the rejection taxonomy is copied so codes resolve; the canonical model DDL and the reference-data bundle are deployed first, then the source bundle without its landing pipe, its tasks DAG and its data metric function bindings. Every warehouse placeholder maps to the sandbox's own warehouse.

3. **Samples are loaded the way Snowpipe loads them.** Each sample is refused unless its name matches a delivery pattern of the config, because the pipe would not load it either. The files are put on an internal stage under the custodian's folder and copied into the raw lines table with the pipe's column list, metadata columns and the foundation's raw-lines file format, then logged in the file load log the way the reconcile task would. The parse dynamic tables are refreshed by hand in dependency order instead of waiting for the target lag, and the process procedure runs intake, merge and resolution as the DAG would.

4. **The report is what the pipeline recorded, read back.** Rows parsed per record; per file the line count, excluded rows and problem counts; parse problems and exceptions by code, level and stage; the run ledger; Silver and canonical row counts; the control-total gap per file for every control-total rule; and the rendered tests with their failing-row counts, which are the DQ checks the DMFs would measure. It is written as Markdown and JSON under `work/dryrun/<task id>/`, with the elapsed time per phase against the ten-minute budget.

5. **The sandbox is destroyed whatever happened.** Destruction runs in a `finally`; a failure is recorded in the report with the phase it stopped in and the sandbox is dropped with reason `task_failed`. The time limit given to the sandbox is the backstop if the process itself dies.

## Consequences

- Reference data in the sandbox is whatever the reference bundle's tables hold after deploy: empty, unless the BSA loads snapshots. Rows will resolve as not found, and the report says so by code; seeding reference snapshots into a dry run is a follow-up.
- The SANDBOX role cannot bind data metric functions, so the DMF step is skipped; the same checks run as the rendered tests, and the control-total gap is queried directly.
- The ten-minute budget is measured and reported, not enforced by killing the run; the sandbox's own statement timeout and time limit bound a runaway.
- The dry run is proven here against a fake executor that answers the read-back queries; the first live run against dev is the acceptance step for the budget on a real sample.
