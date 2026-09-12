# astra-verification

The verification plane of Astra Data Factory (product spec Section 5). Today it provides ephemeral sandboxes (S1.2.3), dry-runs, golden datasets, config-change replay and the Snowpark Connect assessment harness; parity and the DQ runner (E4) build on them.

It runs dry runs (S4.1.1, ADR 0030): `astra-verify dryrun --config configs/<source>.yaml --sample <file> --environment dev` compiles and renders the config as the pipeline would, creates a sandbox, deploys the canonical model, the reference-data tables and the source bundle into it, loads the samples the way Snowpipe would, runs the stages and writes `work/dryrun/<task id>/report.md` and `report.json`: rows parsed, rejected by code, control-total gaps, the rendered tests; the sandbox is destroyed whatever happened, and the elapsed time is reported against the ten-minute budget.

It captures golden datasets (S4.1.2, ADR 0031): `astra-verify golden capture golden/<custodian> --from <date> --to <date> --store s3://<golden bucket>` replays a custodian's historical files through Splitter/Loader in non-production and stores the outputs and rejections as hashed, versioned, read-only datasets, indexed in `golden/<custodian>/datasets.json`. See [golden/README.md](../golden/README.md). Install `.[golden]` for an S3 store or file source, `.[legacy]` for the SQL Server connection.

It replays a config change against that history (S4.1.3, ADR 0032): `astra-verify replay --new <draft config> --old <path> | --old-ref <git ref> --custodian <name> --environment dev` runs the config as it was and the config as drafted through two sandboxes against the same already-captured business days, and writes `replay.md` / `replay.json`: every mapping, rule, DQ rule and resolution difference from the two configs alone, then every canonical row that differs between the two runs, attributed to the field or rule responsible, plus the exception and DQ test deltas. Exit code 0 means eligible for promotion without SME review, 1 means a difference was found, 2 means the replay could not start.

It measures parity between the golden legacy output and the lakehouse (S4.2.1, ADR 0033): `astra-verify parity run --config golden/<custodian>/parity.yaml --business-date <date> --environment dev --store s3://<golden bucket>` compares the golden dataset's captured rows for that date with a live query against the lakehouse table the mapping names, keyed and tolerated per field, and writes `parity.md` / `parity.json`: the match rate against the 99.5% target, and every difference classified as missing, extra or a value mismatch grouped by field. Exit code 0 means the target is met, 1 means it was not, 2 means the run could not start.

It rolls that up into a report over a dual-run cycle window (S4.2.2, ADR 0034): `astra-verify parity report --config golden/<custodian>/parity.yaml --environment dev --store s3://<golden bucket> [--from <date> --to <date>]` runs parity for every already-captured business date in the window, aggregates the overall match rate, the difference groups and the trend from the first cycle to the last, and exports `report.md` / `report.json` into `releases/<source>-parity/` for every source the custodian's `capture.yaml` names — the release evidence a gate decision is made from. `--no-export` writes the working report only.

It collects DMF results into a score per entity per business date (S4.2.3, ADR 0035): `astra-verify dq run --config configs/<custodian>/<source>.yaml --business-date <date> --environment dev` reads what a source's dq_rules already measured that day from `SNOWFLAKE.LOCAL.DATA_QUALITY_MONITORING_RESULTS` — no check is re-run — and folds them into a severity-weighted score per entity (`error` counts four times what `info` does), written to `dq.md` / `dq.json`. An entity below target (`--target`, default 98%) raises one `dq_score_breach` alert into `CONTROL.ALERTS`, delivered by the same dispatcher ADR 0007 already runs; `--no-alert` writes the report only.

It describes the golden output and lakehouse table for the client's own DQ tool (S4.2.4, ADR 0036): `astra-verify connector describe --config golden/<custodian>/connector.yaml --environment dev --store s3://<golden bucket>` renders a connection sheet — where the golden CSV lives, the lakehouse table's Snowflake identity, and the same keys, fields and tolerances `parity.yaml` already states, so iceDQ or Datagaps reconciles identically to `astra-verify parity run` — with no Snowflake connection needed to render it. `astra-verify connector store-result --config ... --business-date <date> --file <the tool's export>` files that result, hashed, alongside `astra-verify parity run`'s own report for the same business date.

It proves the 20-minute window at peak volume (S4.3.1, ADR 0037): `astra-verify volume run --config configs/<custodian>/<source>.yaml [--config ...] --sample <file> [--sample ...] --factor 3 --business-date <date> --environment dev` inflates a normal day's own sample files `factor` times over, runs every given source and the Gold publish in one sandbox — the same two things a dry run skips, done once by hand instead of waiting on a live task schedule — and reports the end-to-end time from the last file loaded to the watermark write against the 20-minute (1200 s) budget, together with the warehouse size the run used.

It proves recoverability from four chaos scenarios (S4.3.2, ADR 0038): `astra-verify chaos run --config configs/<custodian>/<source>.yaml --sample <file> --business-date <date> --environment dev` injects a late, malformed, duplicate and truncated version of one real sample file, in one sandbox, and confirms each reaches its documented state (the taxonomy's rejection code, or the source's own control-total dq_rule), raises an alert, and — for the three content faults — recovers once the same, correct file is simply redelivered under a new name; nothing is ever deleted or edited to make a scenario pass. `--scenario` limits the drill to one of the four.

It proves RTO and RPO with a DR drill (S4.3.3, ADR 0039): `astra-verify dr run --config configs/<custodian>/<source>.yaml --sample <file> --rto-minutes <n> --rpo-minutes <n> --environment dev` loads a normal day's sample into a sandbox, drops the standardized zone (Bronze and Silver) outright, times how long recreating it and replaying the same retained files takes, and confirms every row that was there before the disaster is there after the restore. RTO and RPO are the client's own proposed targets, required inputs never defaulted.

It also carries the Snowpark Connect assessment harness (S3.3.2, ADR 0029): `astra-verify snowpark assess assessments/normalizer --engine local|snowpark-connect` runs the assessment's Spark transformers on a local Spark session or on Snowflake through Snowpark Connect, records effort and result under `results/`, and rewrites the memo's evidence section. Install `.[spark]` (and a Java runtime) for the local engine, `.[snowpark-connect]` for Snowflake.

```bash
cd verification
python -m venv .venv && . .venv/bin/activate
pip install -e ../generation -e ".[dev]"     # add ,deploy to both to talk to Snowflake
pytest
```

## Sandboxes

A sandbox is an isolated database with the standard schemas and its own warehouse, created for one task and dropped when the task ends:

```
ASTRA_DEV_SBX_<TASK>          database: BRONZE, SILVER, GOLD, EXCEPTIONS, CONTROL
ASTRA_DEV_SBX_<TASK>_WH       warehouse, XSMALL by default, auto-suspend 60 s
```

Because a sandbox is a whole database, a release bundle deploys into it unchanged: `{{ DATABASE }}` and the warehouse placeholders point at the sandbox, everything else is the same SQL that later goes to dev. Dry-runs never touch the shared environment.

| Requirement | How |
|---|---|
| Created in under two minutes with the requested config deployed | `create` issues a fixed sequence of statements, then deploys the bundles through `astra-data`, and reports the elapsed time. `astra-verify sandbox check` fails above 120 s. |
| Destroyed after the task | `sandbox()` is a context manager that destroys on exit, including when the task raises. `destroy` and the CLI do it explicitly. |
| Destroyed after a time limit | The expiry is written into the database comment. `CONTROL.REAP_SANDBOXES`, a stored procedure run by a serverless task every 10 minutes in the environment, drops any sandbox past its expiry or older than the hard maximum (24 hours by default). It needs no runner alive. |
| Cost tagged to the task | Database and warehouse carry the `TASK_ID` and `PURPOSE` tags; the session sets a query tag with the task. Cost per task comes from warehouse metering joined to tag references; `sandbox cost` shows the credits of a sandbox warehouse. |

Every create and destroy is logged in `CONTROL.SANDBOX_LOG` with the reason: `task_done`, `task_failed`, `expired`, `max_age` or `manual`.

## Reference data

`astra-verify reference status --environment <env> [--json]` reads `CONTROL.REFERENCE_FEEDS` and `CONTROL.REFERENCE_DATA_RUNS` and prints, per feed, the last replication run with its status, the rows it counted, the delta it made (inserted, updated, deleted, conflicts) and when the replica last succeeded against the feed's expected interval. Exit 1 when any feed's last run failed or no run succeeded within the interval. The rows of a delta are in `REFERENCE.<TABLE>_CHANGES` under the run id.

## Commands

Run as the environment's `SANDBOX` role, with the Snowflake connection in the environment (same names as `astra-data`).

```bash
astra-verify sandbox create  --environment dev --task dryrun-42 --bundle releases/pershing-position --ttl-minutes 60
astra-verify sandbox list    --environment dev
astra-verify sandbox cost    --environment dev --task dryrun-42
astra-verify sandbox destroy --environment dev --task dryrun-42 --reason task_done
astra-verify sandbox reap    --environment dev          # run the reaper now
astra-verify sandbox check   --environment dev --bundle releases/pershing-position   # live acceptance for S1.2.3
```

From Python, which is how the agent runtime uses it:

```python
from astra_data.snowflake_connection import SnowflakeExecutor, connect
from astra_verification import SandboxSpec, sandbox

with SnowflakeExecutor(connect()) as executor:
    with sandbox(executor, SandboxSpec("dryrun-42", "dev", ttl_minutes=60), bundles=["releases/pershing-position"]) as box:
        ...  # run the dry-run against box.database on box.warehouse
```

Task ids are up to 64 characters of letters, digits, `. _ : / -`; the sandbox name uses an upper-case, identifier-safe form of it, truncated to 40 characters.

## Tests

`pytest` runs against an in-memory executor and checks the exact statements issued, the deploy into the sandbox, the context manager's cleanup on success and failure, listing and expiry logic, and the reaper call. No account needed.
