# ADR 0006: Ephemeral sandboxes

Date: 2026-09-05
Status: Accepted
Story: S1.2.3 Ephemeral sandboxes (E1, F1.2, WBS 2.1.6)

## Context

Dry-runs, replays and agent experiments must never touch shared environments. The agent runtime needs an isolated place per task, created in under two minutes with the requested config deployed, destroyed automatically when the task ends or a time limit passes, and with its cost attributable to the task.

## Decision

1. **A sandbox is a database, not a schema.** `<PREFIX>_<ENV>_SBX_<TASK>` with the standard schemas (Bronze, Silver, Gold, Exceptions, Control) and its own warehouse `<sandbox>_WH`. The story says "an isolated schema"; a database is what makes isolation useful, because a release bundle (ADR 0005) can then be deployed into it unchanged: only `{{ DATABASE }}` and the warehouse placeholders differ. A single schema would force generated SQL to be rewritten for sandboxes, which is exactly the divergence between test and production the factory exists to remove.

2. **Sandboxes are Iceberg on the environment's volume with zero retention.** Same storage as the environment, no Time Travel, so dropping the database releases the files. Sample files for a dry-run are staged under a `sandbox/` prefix of the landing bucket that Snowpipe does not watch.

3. **Expiry lives in Snowflake, not in the runner.** The database comment carries the task, creation time and expiry as JSON. `CONTROL.REAP_SANDBOXES`, an owner's-rights stored procedure run by a serverless task every ten minutes, drops any sandbox past its expiry or older than a hard maximum age. A runner that crashes, or a task that never reports completion, cannot leave a sandbox behind for longer than the reap interval past its expiry.

4. **The runner destroys on task completion**, including on failure: the Python `sandbox()` context manager drops the warehouse and database in its exit path and logs the reason. The reaper is the safety net, not the primary path.

5. **Cost is attributed through tags and query tags.** `TASK_ID` and `PURPOSE` tags in Control are applied to every sandbox database and warehouse at creation; the session sets a query tag naming the task. Warehouse metering joined to tag references gives credits per task; `astra-verify sandbox cost` reads the sandbox warehouse's metering directly.

6. **A SANDBOX role with the minimum to do this.** Account-level `CREATE DATABASE` and `CREATE WAREHOUSE`, task execution, `USAGE` on the external volume and the storage integration, `APPLY` on the two tags, read on Control and `INSERT` on the sandbox log. It has no access to the environment's Bronze, Silver or Gold.

7. **Every event is logged** in `CONTROL.SANDBOX_LOG`: created, destroyed, with reasons `task_done`, `task_failed`, `expired`, `max_age` or `manual`.

8. **The verification plane starts here.** The runner is the first module of `verification/astra_verification`; dry-runs (S4.1.1), golden replay and parity build on it. It depends on `astra-data` for bundle deployment and connections rather than duplicating them.

## Consequences

- Sandbox names are bounded (task token of at most 40 characters) and identifier-safe; two tasks with ids that normalise to the same token cannot coexist. Task ids are expected to be unique per task, as workflow engines guarantee.
- `EXECUTE MANAGED TASK` is granted to SANDBOX so a dry-run can exercise serverless tasks inside its sandbox.
- The reaper's hard maximum (24 hours by default) is the ceiling on any sandbox's life; the TTL a runner requests cannot exceed it in effect.
- Cost figures from warehouse metering lag by minutes to hours; the tags are present immediately, the numbers follow.
- The stored procedure resource is a provider preview feature, added to the enabled list alongside the others from ADR 0003.
