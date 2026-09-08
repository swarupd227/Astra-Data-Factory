# astra-verification

The verification plane of Astra Data Factory (product spec Section 5). Today it provides ephemeral sandboxes (S1.2.3); dry-runs, golden replay, parity and the DQ runner (E4) build on them.

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
