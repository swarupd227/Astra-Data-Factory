"""Tasks for one source: the per-custodian DAG that runs its process procedure when the custodian's file set is complete.

A source whose config has a delivery block joins its custodian's Tasks DAG
(ADR 0024): the root task <CUSTODIAN>_GATE runs every minute and calls
CONTROL.CUSTODIAN_GATE, which returns 'run' when every expected file
pattern of the custodian (CONTROL.CUSTODIAN_FILES, synced from the same
delivery block) has a loaded file for a business date and a file arrived
since that date's last run. The source's process task runs after the gate,
only then. A file that completes the set after the cutoff starts the DAG on
arrival, so the late alert is followed by a run. The DAG ends in
<CUSTODIAN>_PUBLISH, after every source's process task, which publishes
the Gold read models for the custodian and writes the watermark last
(ADR 0026). Each custodian has its own gate and its own DAG; nothing in
one references another.

Every rendered source is in a DAG: a config without a delivery block
cannot be rendered at all, since the pipe needs its patterns (ADR 0019).
"""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, CONTROL, WAREHOUSE_BY_TIER, gate_task_name, lit, procedure, publish_task_name, q, task_name

# Order of pipeline steps within the manifest, by the suffix of the file name.
STEP_ORDER = {"pipe.sql": 0, "lines.sql": 1, "parse.sql": 2, "intake.sql": 3, "merge.sql": 4, "resolve.sql": 5, "process.sql": 6, "tasks.sql": 7}

GATE_SCHEDULE_MINUTES = 1


def render_gate(compiled: CompiledConfig) -> list[str]:
    """The custodian's root task. Created once and never replaced here, so the other sources' tasks stay attached to it."""
    custodian = compiled.source["custodian"]
    gate = f"{BRONZE}.{q(gate_task_name(compiled))}"
    delivery = compiled.delivery
    return [
        f"ALTER TASK IF EXISTS {gate} SUSPEND;",
        f"CREATE TASK IF NOT EXISTS {gate}",
        f"  SCHEDULE = '{GATE_SCHEDULE_MINUTES} MINUTE'",
        "  USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'",
        "  ALLOW_OVERLAPPING_EXECUTION = FALSE",
        "  SUSPEND_TASK_AFTER_NUM_FAILURES = 10",
        f"  COMMENT = {lit(f'Gate of custodian {custodian}: starts its DAG when the expected file set for a business date is complete (cutoff {delivery['cutoff_time']} {delivery['timezone']}); a late file starts it again on arrival.')}",
        "AS",
        f"  CALL {CONTROL}.\"CUSTODIAN_GATE\"({lit(custodian)});",
    ]


def render_publish(compiled: CompiledConfig) -> list[str]:
    """The custodian's publish task, after this source's process task; created once, and every source adds itself as a predecessor."""
    custodian = compiled.source["custodian"]
    tier = compiled.source["tier"]
    publish = f"{BRONZE}.{q(publish_task_name(compiled))}"
    return [
        f"CREATE TASK IF NOT EXISTS {publish}",
        f"  WAREHOUSE = {WAREHOUSE_BY_TIER[tier]}",
        f"  COMMENT = {lit(f'Publishes the Gold read models of custodian {custodian} and writes the watermark last, after every source of its DAG has processed.')}",
        "AS",
        f"  CALL {CONTROL}.\"PUBLISH_GOLD\"({lit(custodian)});",
        f"ALTER TASK {publish} ADD AFTER {BRONZE}.{q(task_name(compiled))};",
    ]


def render_tasks(compiled: CompiledConfig) -> str:
    source = compiled.id
    tier = compiled.source["tier"]
    custodian = compiled.source["custodian"]
    task = f"{BRONZE}.{q(task_name(compiled))}"
    process = f"CALL {BRONZE}.{q(procedure(compiled, 'PROCESS'))}();"
    gate = f"{BRONZE}.{q(gate_task_name(compiled))}"
    patterns = ", ".join(f["pattern"] for f in compiled.delivery["files"])
    return "\n".join(
        [
            f"-- Tasks DAG of custodian {custodian} for source {source}. The gate {gate_task_name(compiled)} is the root: every minute it",
            f"-- asks CONTROL.CUSTODIAN_GATE whether a business date's expected file set ({patterns}) is complete and a file",
            "-- arrived since that date's last run; the process task below runs after the gate only when it answers 'run'. A late",
            "-- file after the cutoff completes the set and starts the DAG on arrival; a re-delivery starts it again (ADR 0024).",
            f"-- The DAG ends in {publish_task_name(compiled)}, after every source's process task: it publishes the Gold read models for the",
            "-- custodian and writes the watermark last (CONTROL.PUBLISH_GOLD, ADR 0026), so a consumer never reads a half day.",
            "-- The gate and the publish task are created once and not replaced, so the other sources of the custodian stay attached;",
            "-- the DAG is suspended while this task is attached and every task of it is resumed at the end. Nothing here refers to",
            "-- another custodian.",
            f"-- Named {task_name(compiled)} so a failure is attributed to custodian {custodian} by CONTROL.DETECT_TASK_FAILURES.",
            "-- Rendered by astra-data render.",
            *render_gate(compiled),
            f"CREATE OR REPLACE TASK {task}",
            f"  WAREHOUSE = {WAREHOUSE_BY_TIER[tier]}",
            f"  AFTER {gate}",
            f"  WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE('{gate_task_name(compiled)}') = 'run'",
            f"  COMMENT = {lit(f'Processes source {source} ({compiled.spec.label}) when the gate of custodian {custodian} starts a run; {tier} tier warehouse.')}",
            "AS",
            f"  {process}",
            *render_publish(compiled),
            f"SELECT SYSTEM$TASK_DEPENDENTS_ENABLE('{gate}');",
            "",
        ]
    )


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"pipeline/{compiled.id}_tasks.sql": render_tasks(compiled)}
