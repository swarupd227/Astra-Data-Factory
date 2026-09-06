"""Reference-data replication status: did the nightly job run, what did it count, what changed.

Reads CONTROL.REFERENCE_DATA_RUNS and CONTROL.REFERENCE_FEEDS. A feed is
healthy when its most recent run did not fail and a run succeeded within
the feed's expected interval; the delta of the last run is its inserted,
updated and deleted counts, with the rows themselves in
REFERENCE.<TABLE>_CHANGES under that run id.
"""

from __future__ import annotations

from dataclasses import dataclass

from astra_data.bundle import Executor, Target


@dataclass(frozen=True)
class FeedStatus:
    feed_id: str
    name: str
    table: str
    expected_every_hours: int
    last_run_id: str | None
    last_started_at: str | None
    last_status: str | None
    last_error: str | None
    last_success_at: str | None
    hours_since_success: float | None
    rows_source: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_deleted: int | None
    rows_conflict: int | None
    rows_total: int | None

    @property
    def stale(self) -> bool:
        return self.hours_since_success is None or self.hours_since_success > self.expected_every_hours

    @property
    def healthy(self) -> bool:
        return self.last_status != "failed" and not self.stale

    @property
    def delta(self) -> str:
        if self.last_run_id is None:
            return "no run yet"
        return f"+{self.rows_inserted or 0} ~{self.rows_updated or 0} -{self.rows_deleted or 0}" + (f" ({self.rows_conflict} in conflict)" if self.rows_conflict else "")


def status_query(target: Target) -> str:
    control = f'"{target.environment_database}"."CONTROL"'
    return (
        "SELECT f.FEED_ID, f.NAME, f.TABLE_NAME, f.EXPECTED_EVERY_HOURS, "
        "l.RUN_ID, l.STARTED_AT::STRING, l.STATUS, l.ERROR, s.LAST_SUCCESS::STRING, "
        "TIMESTAMPDIFF('minute', s.LAST_SUCCESS, SYSDATE()) / 60.0, "
        "l.ROWS_SOURCE, l.ROWS_INSERTED, l.ROWS_UPDATED, l.ROWS_DELETED, l.ROWS_CONFLICT, l.ROWS_TOTAL "
        f"FROM {control}.\"REFERENCE_FEEDS\" f "
        f"LEFT JOIN (SELECT * FROM {control}.\"REFERENCE_DATA_RUNS\" QUALIFY ROW_NUMBER() OVER (PARTITION BY FEED_ID ORDER BY STARTED_AT DESC) = 1) l ON l.FEED_ID = f.FEED_ID "
        f"LEFT JOIN (SELECT FEED_ID, MAX(FINISHED_AT) AS LAST_SUCCESS FROM {control}.\"REFERENCE_DATA_RUNS\" WHERE STATUS = 'succeeded' GROUP BY FEED_ID) s ON s.FEED_ID = f.FEED_ID "
        "WHERE f.ENABLED ORDER BY f.FEED_ID"
    )


def changes_query(target: Target, table: str, run_id: str, limit: int = 20) -> str:
    return f'SELECT CHANGE, BEFORE, AFTER FROM "{target.environment_database}"."REFERENCE"."{table}_CHANGES" WHERE RUN_ID = \'{run_id}\' ORDER BY CHANGE LIMIT {int(limit)}'


def feed_statuses(executor: Executor, target: Target) -> list[FeedStatus]:
    statuses: list[FeedStatus] = []
    for row in executor.query(status_query(target)):
        (feed_id, name, table, expected, run_id, started, status, error, success, hours, source, inserted, updated, deleted, conflict, total) = row
        statuses.append(
            FeedStatus(
                feed_id=feed_id,
                name=name,
                table=table,
                expected_every_hours=int(expected),
                last_run_id=run_id,
                last_started_at=started,
                last_status=status,
                last_error=error,
                last_success_at=success,
                hours_since_success=float(hours) if hours is not None else None,
                rows_source=_int(source),
                rows_inserted=_int(inserted),
                rows_updated=_int(updated),
                rows_deleted=_int(deleted),
                rows_conflict=_int(conflict),
                rows_total=_int(total),
            )
        )
    return statuses


def _int(value) -> int | None:
    return None if value is None else int(value)
