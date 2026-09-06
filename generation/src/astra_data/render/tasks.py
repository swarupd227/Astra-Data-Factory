"""Tasks for one source: the serverless orchestration that runs its process procedure."""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import BRONZE, WAREHOUSE_BY_TIER, procedure, q, task_name

# Order of pipeline steps within the manifest, by the suffix of the file name.
STEP_ORDER = {"lines.sql": 0, "intake.sql": 1, "process.sql": 2, "tasks.sql": 3}

# How often a source is processed. Files that land between runs wait at most this long.
INTERVAL_MINUTES = 15


def render_tasks(compiled: CompiledConfig) -> str:
    source = compiled.id
    tier = compiled.source["tier"]
    timezone = compiled.delivery["timezone"] if compiled.delivery else "UTC"
    task = task_name(compiled)
    return "\n".join(
        [
            f"-- Task for {source}: runs the process procedure every {INTERVAL_MINUTES} minutes on the {tier} tier warehouse.",
            f"-- Named {task} so a failure is attributed to custodian {compiled.source['custodian']} by CONTROL.DETECT_TASK_FAILURES.",
            "-- Rendered by astra-data render.",
            f"CREATE OR REPLACE TASK {BRONZE}.{q(task)}",
            f"  WAREHOUSE = {WAREHOUSE_BY_TIER[tier]}",
            f"  SCHEDULE = 'USING CRON */{INTERVAL_MINUTES} * * * * {timezone}'",
            "  SUSPEND_TASK_AFTER_NUM_FAILURES = 10",
            f"  COMMENT = 'Processes source {source} ({compiled.spec.label}) every {INTERVAL_MINUTES} minutes.'",
            "AS",
            f"  CALL {BRONZE}.{q(procedure(compiled, 'PROCESS'))}();",
            f"ALTER TASK {BRONZE}.{q(task)} RESUME;",
            "",
        ]
    )


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"pipeline/{compiled.id}_tasks.sql": render_tasks(compiled)}
