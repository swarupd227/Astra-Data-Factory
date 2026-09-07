"""S3.2.6: the per-custodian Tasks DAG starts when the expected file set is complete, reruns on a late file, and blocks no other custodian."""

from __future__ import annotations

import re

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import compile_config
from astra_data.render import render_bundle
from astra_data.render.names import gate_task_name
from tests.test_render_resolve import _transaction_config
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


def _compile(inputs, path):
    registry, catalog, packs = inputs
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=REPO)


@pytest.fixture(scope="module")
def tasks(inputs):
    return render_bundle(_compile(inputs, EXAMPLE))["pipeline/pershing_position_tasks.sql"]


def _statements(sql: str) -> list[str]:
    return [s.strip().rstrip(";") for s in re.split(r";\n", "\n".join(l for l in sql.splitlines() if not l.startswith("--"))) if s.strip()]


def test_the_gate_is_the_root_and_runs_every_minute(tasks):
    statements = _statements(tasks)
    assert statements[0] == 'ALTER TASK IF EXISTS {{ DATABASE }}."BRONZE"."PERSHING_GATE" SUSPEND'
    gate = statements[1]
    assert gate.startswith('CREATE TASK IF NOT EXISTS {{ DATABASE }}."BRONZE"."PERSHING_GATE"')  # never replaced: the other sources stay attached
    assert "SCHEDULE = '1 MINUTE'" in gate and "USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'XSMALL'" in gate
    assert "ALLOW_OVERLAPPING_EXECUTION = FALSE" in gate and "SUSPEND_TASK_AFTER_NUM_FAILURES = 10" in gate
    assert gate.endswith("CALL {{ DATABASE }}.\"CONTROL\".\"CUSTODIAN_GATE\"('pershing')")
    assert "cutoff 06:00 America/New_York" in gate


def test_the_process_task_runs_after_the_gate_only_when_it_says_run(tasks):
    statements = _statements(tasks)
    process = statements[2]
    assert process.startswith('CREATE OR REPLACE TASK {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"')
    assert "WAREHOUSE = {{ WAREHOUSE_MEDIUM }}" in process
    assert 'AFTER {{ DATABASE }}."BRONZE"."PERSHING_GATE"' in process
    assert "WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE('PERSHING_GATE') = 'run'" in process
    assert "SCHEDULE" not in process  # a child task has no schedule of its own
    assert process.endswith('CALL {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"()')
    assert statements[-1] == "SELECT SYSTEM$TASK_DEPENDENTS_ENABLE('{{ DATABASE }}.\"BRONZE\".\"PERSHING_GATE\"')"
    assert len(statements) == 6


def test_the_dag_ends_in_the_custodians_publish_task(tasks):
    statements = _statements(tasks)
    publish = statements[3]
    assert publish.startswith('CREATE TASK IF NOT EXISTS {{ DATABASE }}."BRONZE"."PERSHING_PUBLISH"')  # created once; every source attaches itself
    assert "SCHEDULE" not in publish and "AFTER" not in publish and "WAREHOUSE = {{ WAREHOUSE_MEDIUM }}" in publish
    assert publish.endswith("CALL {{ DATABASE }}.\"CONTROL\".\"PUBLISH_GOLD\"('pershing')")
    assert statements[4] == 'ALTER TASK {{ DATABASE }}."BRONZE"."PERSHING_PUBLISH" ADD AFTER {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"'
    assert tasks.index('CREATE TASK IF NOT EXISTS {{ DATABASE }}."BRONZE"."PERSHING_PUBLISH"') > tasks.index('CREATE OR REPLACE TASK {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"')
    assert tasks.index("ADD AFTER") < tasks.index("SYSTEM$TASK_DEPENDENTS_ENABLE")


def test_the_dag_is_described_in_terms_of_the_expected_files_and_late_arrivals(tasks):
    assert "pershing/GCUS_%_POS_%.dat, pershing/GCUS_%_TRN_%.dat" in tasks
    assert "A late" in tasks and "starts the DAG on arrival" in tasks and "ADR 0024" in tasks


def test_a_second_source_of_the_custodian_attaches_to_the_same_gate(inputs, tmp_path):
    text = VALID.replace("id: pershing_position", "id: pershing_transaction", 1)
    path = tmp_path / "pershing_transaction.yaml"
    path.write_text(text, encoding="utf-8")
    compiled = _compile(inputs, path)
    sql = render_bundle(compiled)["pipeline/pershing_transaction_tasks.sql"]
    assert gate_task_name(compiled) == "PERSHING_GATE"
    assert 'CREATE TASK IF NOT EXISTS {{ DATABASE }}."BRONZE"."PERSHING_GATE"' in sql
    assert 'CREATE OR REPLACE TASK {{ DATABASE }}."BRONZE"."PERSHING_TRANSACTION_PROCESS"' in sql and 'AFTER {{ DATABASE }}."BRONZE"."PERSHING_GATE"' in sql


def test_one_custodians_dag_shares_nothing_with_another(inputs, tmp_path):
    compiled = _transaction_config(tmp_path, inputs)  # custodian example_custodian, a different spec
    sql = render_bundle(compiled)["pipeline/example_transactions_tasks.sql"]
    assert 'CREATE TASK IF NOT EXISTS {{ DATABASE }}."BRONZE"."EXAMPLE_CUSTODIAN_GATE"' in sql
    assert "CALL {{ DATABASE }}.\"CONTROL\".\"CUSTODIAN_GATE\"('example_custodian')" in sql
    assert "WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE('EXAMPLE_CUSTODIAN_GATE') = 'run'" in sql
    assert 'AFTER {{ DATABASE }}."BRONZE"."EXAMPLE_CUSTODIAN_GATE"' in sql
    assert "pershing" not in sql.lower()  # no task, gate or call of another custodian
    assert 'ALTER TASK {{ DATABASE }}."BRONZE"."EXAMPLE_CUSTODIAN_PUBLISH" ADD AFTER {{ DATABASE }}."BRONZE"."EXAMPLE_CUSTODIAN_EXAMPLE_TRANSACTIONS_PROCESS"' in sql
    assert "ALLOW_OVERLAPPING_EXECUTION = FALSE" in sql  # a slow run of this DAG only holds this DAG back
    assert "SELECT SYSTEM$TASK_DEPENDENTS_ENABLE('{{ DATABASE }}.\"BRONZE\".\"EXAMPLE_CUSTODIAN_GATE\"');" in sql


def test_the_process_task_has_no_schedule_of_its_own(inputs, tmp_path):
    # the target lag governs the parse dynamic tables; the process task waits for the gate
    path = tmp_path / "pershing_position.yaml"
    path.write_text(VALID.replace("target_lag_minutes: 10", "target_lag_minutes: 45"), encoding="utf-8")
    sql = render_bundle(_compile(inputs, path))["pipeline/pershing_position_tasks.sql"]
    assert "45" not in sql and sql.count("SCHEDULE") == 1 and "SCHEDULE = '1 MINUTE'" in sql
