import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from astra_verification.sandbox import (
    SandboxRecord,
    SandboxSpec,
    cost_credits,
    create,
    create_statements,
    destroy,
    destroy_statements,
    list_sandboxes,
    reap,
    sandbox,
    sandbox_token,
)

NOW = datetime(2026, 9, 5, 14, 0, 0, tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self, rows_by_marker: dict[str, list[tuple]] | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.rows_by_marker = rows_by_marker or {}

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return rows
        return []


def make_bundle(root: Path) -> Path:
    bundle = root / "pershing-position"
    (bundle / "ddl").mkdir(parents=True)
    (bundle / "manifest.yaml").write_text("bundle: pershing-position\nversion: '2026.09'\nsource: pershing_position\nsteps:\n  - ddl/bronze.sql\n", encoding="utf-8")
    (bundle / "ddl" / "bronze.sql").write_text('CREATE ICEBERG TABLE IF NOT EXISTS "{{ DATABASE }}".BRONZE.T (X INT); CREATE OR REPLACE DYNAMIC TABLE "{{ DATABASE }}".SILVER.D WAREHOUSE = {{ WAREHOUSE_MEDIUM }} AS SELECT 1;\n', encoding="utf-8")
    return bundle


# -- naming --------------------------------------------------------------------


def test_sandbox_names_are_derived_from_environment_and_task():
    spec = SandboxSpec("dryrun-2026-09-05:pershing/position", "dev")
    assert sandbox_token(spec.task_id) == "DRYRUN_2026_09_05_PERSHING_POSITION"
    assert spec.database == "ASTRA_DEV_SBX_DRYRUN_2026_09_05_PERSHING_POSITION"
    assert spec.warehouse == spec.database + "_WH"
    assert spec.external_volume == "ASTRA_DEV_ICEBERG"
    assert spec.log_table == '"ASTRA_DEV"."CONTROL"."SANDBOX_LOG"'
    assert spec.tag("TASK_ID") == '"ASTRA_DEV"."CONTROL"."TASK_ID"'


def test_token_is_bounded_and_identifier_safe():
    assert len(sandbox_token("x" * 100)) == 40
    assert sandbox_token("__a--b__") == "A__B"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"task_id": ""},
        {"task_id": "has space"},
        {"task_id": "---"},
        {"ttl_minutes": 0},
        {"ttl_minutes": 24 * 60 + 1},
        {"warehouse_size": "X4LARGE"},
        {"environment": "Prod"},
        {"schemas": ()},
    ],
)
def test_spec_rejects_bad_input(kwargs):
    base = {"task_id": "t1", "environment": "dev"}
    with pytest.raises(ValueError):
        SandboxSpec(**{**base, **kwargs})


def test_target_points_at_the_sandbox():
    params = SandboxSpec("t1", "qa", prefix="ENV").target().parameters()
    assert params["DATABASE"] == "ENV_QA_SBX_T1"
    assert params["WAREHOUSE_COMPLEX"] == "ENV_QA_SBX_T1_WH"


# -- statements ----------------------------------------------------------------


def test_create_statements_build_an_isolated_tagged_expiring_sandbox():
    spec = SandboxSpec("t1", "dev", ttl_minutes=90, warehouse_size="SMALL")
    statements = create_statements(spec, NOW, NOW + timedelta(minutes=90))

    assert statements[0] == 'ALTER SESSION SET QUERY_TAG = \'{"astra":{"task":"t1","sandbox":"ASTRA_DEV_SBX_T1","purpose":"sandbox"}}\''

    database = statements[1]
    assert database.startswith('CREATE DATABASE "ASTRA_DEV_SBX_T1" DATA_RETENTION_TIME_IN_DAYS = 0 EXTERNAL_VOLUME = "ASTRA_DEV_ICEBERG" CATALOG = \'SNOWFLAKE\'')
    comment = json.loads(database.split("COMMENT = '")[1].split("' WITH TAG")[0].replace("''", "'"))
    assert comment == {"task": "t1", "purpose": "sandbox", "environment": "dev", "created_at": "2026-09-05 14:00:00", "expires_at": "2026-09-05 15:30:00", "ttl_minutes": 90}
    assert database.endswith('WITH TAG ("ASTRA_DEV"."CONTROL"."TASK_ID" = \'t1\', "ASTRA_DEV"."CONTROL"."PURPOSE" = \'sandbox\')')

    schemas = statements[2:7]
    assert [s.split('"')[3] for s in schemas] == ["BRONZE", "SILVER", "GOLD", "EXCEPTIONS", "CONTROL"]
    assert all("WITH MANAGED ACCESS DATA_RETENTION_TIME_IN_DAYS = 0" in s for s in schemas)

    warehouse = statements[7]
    assert warehouse.startswith('CREATE WAREHOUSE "ASTRA_DEV_SBX_T1_WH" WAREHOUSE_SIZE = \'SMALL\' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE')
    assert "STATEMENT_TIMEOUT_IN_SECONDS = 1800" in warehouse and "\"CONTROL\".\"TASK_ID\" = 't1'" in warehouse

    assert statements[8] == 'USE WAREHOUSE "ASTRA_DEV_SBX_T1_WH"'
    assert statements[9].startswith('INSERT INTO "ASTRA_DEV"."CONTROL"."SANDBOX_LOG"') and "'created', NULL" in statements[9]


def test_destroy_statements_drop_warehouse_then_database_then_log():
    statements = destroy_statements(SandboxSpec("t1", "dev"), "task_done", "it's over")
    assert statements[0] == 'DROP WAREHOUSE IF EXISTS "ASTRA_DEV_SBX_T1_WH"'
    assert statements[1] == 'DROP DATABASE IF EXISTS "ASTRA_DEV_SBX_T1"'
    assert "'destroyed', 'task_done', 'it''s over'" in statements[2]


# -- operations ----------------------------------------------------------------


def test_create_deploys_bundles_into_the_sandbox_and_times_it(tmp_path):
    executor = FakeExecutor()
    ticks = iter([10.0, 52.5])
    result = create(executor, SandboxSpec("t1", "dev"), [make_bundle(tmp_path)], clock=lambda: NOW, monotonic=lambda: next(ticks))

    assert result.database == "ASTRA_DEV_SBX_T1" and result.seconds == 42.5
    assert result.expires_at == NOW + timedelta(minutes=120)
    assert result.deployed == ("pershing-position 2026.09",)
    rendered = executor.scripts[-1]
    assert '"ASTRA_DEV_SBX_T1".BRONZE.T' in rendered and "WAREHOUSE = ASTRA_DEV_SBX_T1_WH" in rendered
    assert executor.scripts[-2] == 'USE WAREHOUSE "ASTRA_DEV_SBX_T1_WH"' or executor.scripts[-2].startswith("INSERT INTO")


def test_create_fails_before_touching_snowflake_when_a_bundle_is_broken(tmp_path):
    executor = FakeExecutor()
    (tmp_path / "broken").mkdir()
    with pytest.raises(Exception):
        create(executor, SandboxSpec("t1", "dev"), [tmp_path / "broken"], clock=lambda: NOW)
    assert executor.scripts == []


def test_context_manager_destroys_on_success_and_on_failure(tmp_path):
    executor = FakeExecutor()
    with sandbox(executor, SandboxSpec("ok", "dev")) as box:
        assert box.database == "ASTRA_DEV_SBX_OK"
    assert executor.scripts[-3] == 'DROP WAREHOUSE IF EXISTS "ASTRA_DEV_SBX_OK_WH"'
    assert "'destroyed', 'task_done'" in executor.scripts[-1]

    executor = FakeExecutor()
    with pytest.raises(RuntimeError):
        with sandbox(executor, SandboxSpec("bad", "dev")):
            raise RuntimeError("dry-run blew up")
    assert executor.scripts[-2] == 'DROP DATABASE IF EXISTS "ASTRA_DEV_SBX_BAD"'
    assert "'destroyed', 'task_failed', 'RuntimeError: dry-run blew up'" in executor.scripts[-1]


def test_destroy_is_explicit_about_the_reason():
    executor = FakeExecutor()
    destroy(executor, SandboxSpec("t1", "dev"), reason="manual", detail="operator")
    assert "'destroyed', 'manual', 'operator'" in executor.scripts[-1]


def test_list_sandboxes_reads_the_comment_and_flags_expiry():
    executor = FakeExecutor(
        rows_by_marker={
            "RESULT_SCAN": [
                ("ASTRA_DEV_SBX_T1", json.dumps({"task": "t1", "created_at": "2026-09-05 12:00:00", "expires_at": "2026-09-05 13:00:00"}), None),
                ("ASTRA_DEV_SBX_T2", "not json", datetime(2026, 9, 5, 13, 50)),
                ("ASTRA_DEV_SBX_OLD", None, datetime(2026, 9, 1, 0, 0)),
            ]
        }
    )
    records = list_sandboxes(executor, "dev")
    assert executor.scripts == ["SHOW DATABASES LIKE 'ASTRA_DEV_SBX_%'"]
    assert [r.database for r in records] == ["ASTRA_DEV_SBX_T1", "ASTRA_DEV_SBX_T2", "ASTRA_DEV_SBX_OLD"]
    assert records[0].task_id == "t1" and records[0].is_expired(NOW)
    assert records[1].task_id is None and not records[1].is_expired(NOW)
    assert records[2].is_expired(NOW, max_age_hours=24)
    assert not SandboxRecord("x", None, None, None).is_expired(NOW)


def test_reap_calls_the_environment_procedure_and_returns_the_count():
    executor = FakeExecutor(rows_by_marker={"REAP_SANDBOXES": [(3,)]})
    assert reap(executor, "qa") == 3
    assert executor.queries == ['CALL "ASTRA_QA"."CONTROL"."REAP_SANDBOXES"()']


def test_cost_queries_metering_history_for_the_sandbox_warehouse():
    executor = FakeExecutor(rows_by_marker={"WAREHOUSE_METERING_HISTORY": [(0.0421,)]})
    assert cost_credits(executor, SandboxSpec("t1", "dev"), days=3) == 0.0421
    assert "WAREHOUSE_NAME => 'ASTRA_DEV_SBX_T1_WH'" in executor.queries[0] and "DATEADD('day', -3" in executor.queries[0]
