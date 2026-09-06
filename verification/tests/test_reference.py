"""Reference-data replication status (S2.3.3)."""

from __future__ import annotations

import json

from astra_data.bundle import Target

from astra_verification.cli import main
from astra_verification.reference import changes_query, feed_statuses, status_query

TARGET = Target("dev")

HEALTHY = ("security_master", "Security master", "SECURITY_MASTER", 26, "run-2", "2026-09-07 02:00:00.000", "succeeded", None, "2026-09-07 02:03:10.000", 5.5, 12000, 40, 15, 3, 0, 12010)
STALE = ("account_xref", "Account cross-reference", "ACCOUNT_XREF", 26, "run-9", "2026-09-05 02:30:00.000", "failed", "Numeric value 'abc' is not recognized", "2026-09-04 02:31:00.000", 53.0, 900, None, None, None, None, None)
NEVER = ("prices", "Prices", "PRICES", 26, None, None, None, None, None, None, None, None, None, None, None, None)


class FakeExecutor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        raise AssertionError("status only reads")

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return self.rows

    def close(self) -> None:
        pass


def test_statuses_come_from_the_latest_run_and_the_last_success():
    executor = FakeExecutor([HEALTHY, STALE, NEVER])
    healthy, stale, never = feed_statuses(executor, TARGET)
    assert executor.queries == [status_query(TARGET)]
    assert 'FROM "ASTRA_DEV"."CONTROL"."REFERENCE_FEEDS" f' in executor.queries[0] and "QUALIFY ROW_NUMBER() OVER (PARTITION BY FEED_ID ORDER BY STARTED_AT DESC) = 1" in executor.queries[0]

    assert healthy.healthy and not healthy.stale and healthy.delta == "+40 ~15 -3" and healthy.rows_total == 12010
    assert not stale.healthy and stale.stale and stale.last_status == "failed" and stale.delta == "+0 ~0 -0"
    assert not never.healthy and never.stale and never.delta == "no run yet" and never.hours_since_success is None


def test_conflicts_show_in_the_delta():
    row = HEALTHY[:14] + (7, 12010)
    (status,) = feed_statuses(FakeExecutor([row]), TARGET)
    assert status.delta == "+40 ~15 -3 (7 in conflict)"


def test_changes_query_reads_the_run_from_the_feeds_change_log():
    assert changes_query(TARGET, "SECURITY_MASTER", "run-2", 5) == 'SELECT CHANGE, BEFORE, AFTER FROM "ASTRA_DEV"."REFERENCE"."SECURITY_MASTER_CHANGES" WHERE RUN_ID = \'run-2\' ORDER BY CHANGE LIMIT 5'


def test_cli_status_prints_each_feed_and_fails_when_one_is_unhealthy(monkeypatch, capsys):
    monkeypatch.setattr("astra_verification.cli._executor", lambda: FakeExecutor([HEALTHY, STALE]))
    assert main(["reference", "status", "--environment", "dev"]) == 1
    out = capsys.readouterr().out
    assert "OK    security_master" in out and "last run 2026-09-07 02:00:00.000 succeeded" in out and "delta +40 ~15 -3" in out and "replica 12010" in out
    assert "FAIL  account_xref" in out and "failed" in out and "Numeric value 'abc' is not recognized" in out and "53.0h ago" in out
    assert "REFERENCE.SECURITY_MASTER_CHANGES" in out

    monkeypatch.setattr("astra_verification.cli._executor", lambda: FakeExecutor([HEALTHY]))
    assert main(["reference", "status", "--environment", "dev", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["feed_id"] == "security_master" and payload[0]["healthy"] is True and payload[0]["delta"] == "+40 ~15 -3"
