"""Custodian delivery expectations and alert severities, from configs to CONTROL.

Each source config may carry a `delivery` block (cutoff, timezone, business
days, expected files) and an `alerts` block (severity of late and task
failure alerts). Sources of the same custodian must agree on them. This
module folds every config into one row per custodian and produces the SQL
that brings CONTROL.CUSTODIANS and CONTROL.CUSTODIAN_FILES in line, so that
what the alerting procedures act on is exactly what was reviewed in Git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from astra_data.bundle import Executor, Target
from astra_data.validate import Problem, discover, validate_config_file
from astra_data.yamlsource import load

DEFAULT_BUSINESS_DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")
DEFAULT_SEVERITY = "error"
DAY_NAMES = {"mon": "MON", "tue": "TUE", "wed": "WED", "thu": "THU", "fri": "FRI", "sat": "SAT", "sun": "SUN"}

# A custodian with alerts but no delivery block never becomes late.
NO_DELIVERY_CUTOFF = "23:59"
NO_DELIVERY_TIMEZONE = "UTC"


@dataclass(frozen=True)
class ExpectedFile:
    pattern: str
    description: str = ""


@dataclass(frozen=True)
class Custodian:
    custodian_id: str
    name: str
    cutoff_time: str
    timezone: str
    business_days: tuple[str, ...]
    late_severity: str
    failure_severity: str
    files: tuple[ExpectedFile, ...]
    sources: tuple[str, ...]

    @property
    def business_days_text(self) -> str:
        return ",".join(DAY_NAMES[d] for d in self.business_days)


@dataclass
class _Draft:
    custodian_id: str
    delivery: dict | None = None
    delivery_source: str = ""
    late: str | None = None
    failure: str | None = None
    severity_source: str = ""
    files: dict[str, ExpectedFile] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)


def custodians_from_configs(paths: Iterable[Path | str], root: Path | None = None) -> tuple[list[Custodian], list[Problem]]:
    """Fold every valid config under the paths into one Custodian per custodian id.

    Configs that fail validation are reported and skipped. Configs of the same
    custodian that disagree on delivery or severities are reported too.
    """
    files, problems = discover(paths)
    drafts: dict[str, _Draft] = {}

    for file in sorted(files):
        file_problems = validate_config_file(file, root)
        if file_problems:
            problems.extend(file_problems)
            continue
        data = load(file.read_text(encoding="utf-8"))
        display = _display(file, root)
        custodian_id = data["source"]["custodian"]
        delivery = data.get("delivery")
        alerts = data.get("alerts") or {}
        if delivery is None and not alerts:
            continue

        draft = drafts.setdefault(custodian_id, _Draft(custodian_id))
        draft.sources.append(display)

        if delivery is not None:
            schedule = (delivery["cutoff_time"], delivery["timezone"], tuple(delivery.get("business_days") or DEFAULT_BUSINESS_DAYS))
            if draft.delivery is None:
                draft.delivery, draft.delivery_source = {"schedule": schedule}, display
            elif draft.delivery["schedule"] != schedule:
                problems.append(Problem(display, None, f"delivery for custodian '{custodian_id}' (cutoff, timezone, business days) differs from {draft.delivery_source}; every source of a custodian must agree"))
            for entry in delivery["files"]:
                draft.files.setdefault(entry["pattern"], ExpectedFile(entry["pattern"], entry.get("description", "")))

        late, failure = alerts.get("late"), alerts.get("task_failure")
        for label, value, current in (("alerts.late", late, draft.late), ("alerts.task_failure", failure, draft.failure)):
            if value is not None and current is not None and value != current:
                problems.append(Problem(display, None, f"{label} for custodian '{custodian_id}' is '{value}' here and '{current}' in {draft.severity_source}; every source of a custodian must agree"))
        if late is not None and draft.late is None:
            draft.late, draft.severity_source = late, display
        if failure is not None and draft.failure is None:
            draft.failure, draft.severity_source = failure, draft.severity_source or display

    custodians = [
        Custodian(
            custodian_id=d.custodian_id,
            name=d.custodian_id.replace("_", " ").title(),
            cutoff_time=d.delivery["schedule"][0] if d.delivery else NO_DELIVERY_CUTOFF,
            timezone=d.delivery["schedule"][1] if d.delivery else NO_DELIVERY_TIMEZONE,
            business_days=d.delivery["schedule"][2] if d.delivery else (),
            late_severity=d.late or DEFAULT_SEVERITY,
            failure_severity=d.failure or DEFAULT_SEVERITY,
            files=tuple(d.files.values()),
            sources=tuple(d.sources),
        )
        for d in sorted(drafts.values(), key=lambda d: d.custodian_id)
    ]
    return custodians, problems


# -- SQL -----------------------------------------------------------------------


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sync_statements(
    custodians: list[Custodian],
    target: Target,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> list[str]:
    """One transaction that makes CONTROL.CUSTODIANS and CUSTODIAN_FILES match the configs.

    Custodians absent from the configs are disabled, not deleted, so their
    history in ALERTS keeps a referent.
    """
    control = f'"{target.environment_database}"."CONTROL"'
    custodians_table = f'{control}."CUSTODIANS"'
    files_table = f'{control}."CUSTODIAN_FILES"'
    updated_at = _lit(clock().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    statements = ["BEGIN TRANSACTION"]

    if custodians:
        rows = ", ".join(
            "("
            + ", ".join(
                [
                    _lit(c.custodian_id),
                    _lit(c.name),
                    _lit(c.cutoff_time),
                    _lit(c.timezone),
                    _lit(c.business_days_text),
                    _lit(c.late_severity),
                    _lit(c.failure_severity),
                    _lit(", ".join(c.sources)),
                ]
            )
            + ")"
            for c in custodians
        )
        statements.append(
            f"MERGE INTO {custodians_table} t "
            f"USING (SELECT * FROM VALUES {rows} AS v (CUSTODIAN_ID, NAME, CUTOFF_TIME, TIMEZONE, BUSINESS_DAYS, LATE_SEVERITY, FAILURE_SEVERITY, SOURCE)) s "
            f"ON t.CUSTODIAN_ID = s.CUSTODIAN_ID "
            f"WHEN MATCHED THEN UPDATE SET NAME = s.NAME, CUTOFF_TIME = s.CUTOFF_TIME, TIMEZONE = s.TIMEZONE, BUSINESS_DAYS = s.BUSINESS_DAYS, "
            f"LATE_SEVERITY = s.LATE_SEVERITY, FAILURE_SEVERITY = s.FAILURE_SEVERITY, ENABLED = TRUE, SOURCE = s.SOURCE, UPDATED_AT = {updated_at}::TIMESTAMP_NTZ "
            f"WHEN NOT MATCHED THEN INSERT (CUSTODIAN_ID, NAME, CUTOFF_TIME, TIMEZONE, BUSINESS_DAYS, LATE_SEVERITY, FAILURE_SEVERITY, ENABLED, SOURCE, UPDATED_AT) "
            f"VALUES (s.CUSTODIAN_ID, s.NAME, s.CUTOFF_TIME, s.TIMEZONE, s.BUSINESS_DAYS, s.LATE_SEVERITY, s.FAILURE_SEVERITY, TRUE, s.SOURCE, {updated_at}::TIMESTAMP_NTZ)"
        )
        keep = ", ".join(_lit(c.custodian_id) for c in custodians)
        statements.append(f"UPDATE {custodians_table} SET ENABLED = FALSE, UPDATED_AT = {updated_at}::TIMESTAMP_NTZ WHERE ENABLED AND CUSTODIAN_ID NOT IN ({keep})")
    else:
        statements.append(f"UPDATE {custodians_table} SET ENABLED = FALSE, UPDATED_AT = {updated_at}::TIMESTAMP_NTZ WHERE ENABLED")

    statements.append(f"DELETE FROM {files_table}")
    file_rows = [
        f"({_lit(c.custodian_id)}, {_lit(f.pattern)}, {_lit(f.description)})"
        for c in custodians
        for f in c.files
    ]
    if file_rows:
        statements.append(f"INSERT INTO {files_table} (CUSTODIAN_ID, FILE_PATTERN, DESCRIPTION) VALUES " + ", ".join(file_rows))

    statements.append("COMMIT")
    return statements


def sync(executor: Executor, custodians: list[Custodian], target: Target) -> int:
    """Apply the sync. Returns the number of custodians now enabled."""
    executor.execute_script(";\n".join(sync_statements(custodians, target)) + ";")
    return len(custodians)


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
