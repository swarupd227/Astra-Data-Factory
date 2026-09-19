"""The exception workflow, rendered as a bundle (S7.1.4)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from astra_knowledge.patterns.exception_workflow import RULES, STATES, TRANSITIONS

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.exception_store import (
    STORAGE_RULES,
    bundle_name,
    check_bundle,
    packs_with_exceptions,
    render_bundle,
    rule_number,
    write_bundle,
)

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
RELEASES = REPO / "releases"


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return []

    def close(self) -> None:
        pass


def _pack():
    packs, problems = packs_with_exceptions(DOMAINS, REPO)
    assert problems == [] and [p.name for p in packs] == ["custodial"]
    return packs[0]


def _procedure(sql: str, name: str) -> str:
    start = sql.index(f'CREATE OR REPLACE PROCEDURE {{{{ DATABASE }}}}."CONTROL"."{name}"')
    end = sql.index("$$;", sql.index("$$", start) + 2) + 3
    return sql[start:end]


def _raised(procedure: str) -> list[str]:
    """The rules a procedure raises, in the order it checks them (the guard clause after the transaction excluded)."""
    return re.findall(r"RAISE (\w+);", procedure)


def test_the_bundle_has_the_history_the_procedures_and_the_invariant_tests():
    files = render_bundle(_pack())
    assert set(files) == {
        "manifest.yaml",
        "ddl/exception_workflow.sql",
        "pipeline/exception_workflow.sql",
        "tests/exception_history_transitions_are_legal.sql",
        "tests/exception_history_events_are_well_formed.sql",
        "tests/exception_history_no_assignment_after_a_move.sql",
    }
    manifest = files["manifest.yaml"]
    assert "bundle: custodial-exceptions" in manifest and "source: custodial_exceptions" in manifest
    assert manifest.index("ddl/exception_workflow.sql") < manifest.index("pipeline/exception_workflow.sql")
    assert render_bundle(_pack()) == files  # deterministic


def test_the_history_is_append_only_state_with_a_view_of_the_latest_assignment():
    ddl = render_bundle(_pack())["ddl/exception_workflow.sql"]
    assert 'CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY"' in ddl and "BASE_LOCATION = 'control/exception_history/'" in ddl
    for column in ("EVENT_ID", "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "KIND", "FROM_STATUS", "TO_STATUS", "ASSIGNEE", "ACTOR", "EVENT_AT", "NOTE"):
        assert f'"{column}"' in ddl
    for required in ("EVENT_ID", "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "KIND", "ACTOR", "EVENT_AT"):
        assert re.search(rf'"{required}"\s+(STRING|TIMESTAMP_NTZ\(6\)) NOT NULL', ddl), required
    assert re.search(r'"OWNER"\s+STRING COMMENT', ddl) and "NOT NULL" not in re.search(r'"NOTE".*', ddl).group(0)
    view = ddl[ddl.index("CREATE OR REPLACE VIEW"):]
    assert '{{ DATABASE }}."CONTROL"."EXCEPTION_ASSIGNMENTS"' in view and "WHERE \"KIND\" = 'assign'" in view
    assert 'QUALIFY ROW_NUMBER() OVER (PARTITION BY "SOURCE_ID", "EXCEPTION_ID" ORDER BY "EVENT_AT" DESC) = 1' in view


# ---------------------------------------------------------------- the SQL is the reference implementation's own rules


def test_the_allowed_moves_in_the_sql_are_exactly_the_reference_implementations():
    sql = render_bundle(_pack())["pipeline/exception_workflow.sql"]
    transition = _procedure(sql, "TRANSITION_EXCEPTION")
    edges = re.search(r'FROM VALUES (.*?) AS t \("FROM_STATUS", "TO_STATUS"\)', transition).group(1)
    expected = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert set(re.findall(r"\('(\w+)', '(\w+)'\)", edges)) == expected
    states = re.search(r'FROM VALUES (.*?) AS s \("STATE"\)', transition).group(1)
    assert tuple(re.findall(r"\('(\w+)'\)", states)) == STATES


def test_the_sql_checks_its_rules_in_the_order_the_reference_implementation_does():
    """Every shared rule appears in the RULES order; the storage's own rules come first (the source name) or last (the write)."""
    sql = render_bundle(_pack())["pipeline/exception_workflow.sql"]
    transition, assign = _raised(_procedure(sql, "TRANSITION_EXCEPTION")), _raised(_procedure(sql, "ASSIGN_EXCEPTION"))
    assert transition == ["source_invalid", "exception_not_found", "not_a_state", "actor_required", "illegal_transition", "resolution_required", "not_whitelisted", "changed_concurrently"]
    assert assign == ["source_invalid", "exception_not_found", "actor_required", "assignee_required", "not_new", "already_assigned", "changed_concurrently"]
    for order in (transition, assign):
        shared = [r for r in order if r in RULES]
        assert shared == sorted(shared, key=RULES.index)
        assert [r for r in order if r in STORAGE_RULES] == [r for r in STORAGE_RULES if r in order]


def test_every_rule_is_declared_once_with_its_own_number_in_the_valid_snowflake_range():
    sql = render_bundle(_pack())["pipeline/exception_workflow.sql"]
    numbers = [rule_number(r) for r in RULES + STORAGE_RULES]
    assert len(set(numbers)) == len(numbers) and all(-20999 <= n <= -20000 for n in numbers)
    for rule in RULES + STORAGE_RULES:
        declared = re.findall(rf"^  {rule} EXCEPTION \((-\d+), '[^']+'\);$", sql, flags=re.M)
        assert declared and set(declared) == {str(rule_number(rule))}, rule  # once per procedure that uses it, always the same number


def test_a_move_is_refused_before_anything_is_written_and_written_only_while_the_status_still_holds():
    transition = _procedure(render_bundle(_pack())["pipeline/exception_workflow.sql"], "TRANSITION_EXCEPTION")
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."TRANSITION_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "TO_STATUS" STRING, "ACTOR" STRING, "RESOLUTION" STRING)' in transition
    assert "EXECUTE AS OWNER" in transition
    begin = transition.index("BEGIN TRANSACTION;")
    assert all(transition.index(f"RAISE {rule};") < begin for rule in ("source_invalid", "exception_not_found", "not_a_state", "actor_required", "illegal_transition", "resolution_required", "not_whitelisted"))
    order = [transition.index(x) for x in ("BEGIN TRANSACTION;", 'UPDATE IDENTIFIER(:v_table) SET "STATUS" = :TO_STATUS', "IF (SQLROWCOUNT = 0)", 'INSERT INTO {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY"', "COMMIT;")]
    assert order == sorted(order)
    assert 'WHERE "EXCEPTION_ID" = :EXCEPTION_ID AND "STATUS" = :v_status;' in transition  # the optimistic guard
    assert '"RESOLVED_BY" = TRIM(:ACTOR), "RESOLVED_AT" = SYSDATE(), "UPDATED_AT" = SYSDATE()' in transition
    assert "'transition', :v_status, :TO_STATUS, NULL, TRIM(:ACTOR), SYSDATE(), TRIM(:RESOLUTION)" in transition
    assert "EXCEPTION\n  WHEN OTHER THEN\n    ROLLBACK;\n    RAISE;" in transition


def test_auto_resolve_reads_the_taxonomys_own_whitelist_and_the_owner_is_the_taxonomys_owner():
    transition = _procedure(render_bundle(_pack())["pipeline/exception_workflow.sql"], "TRANSITION_EXCEPTION")
    assert "IF (TO_STATUS = 'AUTO_RESOLVED') THEN" in transition
    assert 'FROM {{ DATABASE }}."CONTROL"."REJECTION_CODES" WHERE "CODE" = :v_code AND "AUTO_RESOLVE"' in transition
    assert 'SELECT MAX("OWNER") INTO :v_owner FROM {{ DATABASE }}."CONTROL"."REJECTION_CODES" WHERE "CODE" = :v_code' in transition


def test_the_source_name_is_checked_before_it_is_used_to_reach_a_table():
    sql = render_bundle(_pack())["pipeline/exception_workflow.sql"]
    for name in ("TRANSITION_EXCEPTION", "ASSIGN_EXCEPTION"):
        procedure = _procedure(sql, name)
        check = procedure.index("REGEXP_LIKE(SOURCE_ID, '^[A-Za-z][A-Za-z0-9_]*$')")
        assert check < procedure.index("v_table := ") < procedure.index("IDENTIFIER(:v_table)")
        assert """v_table := '{{ DATABASE }}."EXCEPTIONS"."' || UPPER(SOURCE_ID) || '"';""" in procedure


def test_an_assignment_is_only_for_a_new_exception_and_only_to_someone_new():
    assign = _procedure(render_bundle(_pack())["pipeline/exception_workflow.sql"], "ASSIGN_EXCEPTION")
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."ASSIGN_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "ASSIGNEE" STRING, "ACTOR" STRING)' in assign
    assert "IF (v_status <> 'NEW') THEN\n    RAISE not_new;" in assign
    assert '"KIND" = \'assign\' ORDER BY "EVENT_AT" DESC LIMIT 1' in assign and "IF (v_current = TRIM(ASSIGNEE)) THEN\n    RAISE already_assigned;" in assign
    assert "'assign', NULL, NULL, TRIM(:ASSIGNEE), TRIM(:ACTOR), SYSDATE(), NULL" in assign
    # the status is read again after the event is written: a close that landed in between undoes the assignment
    after = assign[assign.index("BEGIN TRANSACTION;"):]
    assert after.index("INSERT INTO") < after.index('"STATUS" = \'NEW\'') < after.index("RAISE changed_concurrently;") < after.index("COMMIT;")


# ---------------------------------------------------------------- the tests hold on real data


def test_the_invariant_tests_check_the_history_against_the_same_allowed_moves():
    files = render_bundle(_pack())
    legal = files["tests/exception_history_transitions_are_legal.sql"]
    edges = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert set(re.findall(r"\('(\w+)', '(\w+)'\)", legal)) == edges
    assert "WHERE h.\"KIND\" = 'transition' AND l.\"FROM_STATUS\" IS NULL;" in legal
    formed = files["tests/exception_history_events_are_well_formed.sql"]
    assert "\"KIND\" NOT IN ('transition', 'assign')" in formed and "TRIM(COALESCE(\"ACTOR\", '')) = ''" in formed
    late = files["tests/exception_history_no_assignment_after_a_move.sql"]
    assert "a.\"KIND\" = 'assign' AND a.\"EVENT_AT\" > t.\"EVENT_AT\";" in late


def test_the_committed_bundle_is_current_and_deploys_through_the_executor():
    pack = _pack()
    assert check_bundle(pack, RELEASES, REPO) == []
    bundles, problems = check_bundles(RELEASES, REPO)
    assert problems == [] and any(b.name == bundle_name(pack) for b in bundles)
    bundle = load_bundle(RELEASES / "custodial-exceptions", REPO)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/exception_workflow.sql", "pipeline/exception_workflow.sql")
    assert all('ASTRA_QA."CONTROL"' in script for script in executor.scripts) and "{{" not in "".join(executor.scripts)
    assert 'v_table := \'ASTRA_QA."EXCEPTIONS"."\'' in executor.scripts[1]
    results = run_tests(bundle, Target("qa"), executor)
    assert len(results) == 3 and all(r.passed for r in results)


# ---------------------------------------------------------------- the model and the workflow must agree


def test_a_pack_whose_status_codes_differ_from_the_workflows_states_is_refused(tmp_path):
    domains = tmp_path / "domains"
    shutil.copytree(DOMAINS / "custodial", domains / "custodial")
    model = domains / "custodial" / "cdm" / "1.0.yaml"
    text = model.read_text(encoding="utf-8")
    dismissed = '          - { value: DISMISSED, meaning: "closed without a change" }\n'
    assert dismissed in text
    model.write_text(text.replace(dismissed, ""), encoding="utf-8")
    packs, problems = packs_with_exceptions(domains, tmp_path)
    assert packs == [] and len(problems) == 1
    assert "Exception.STATUS codes are NEW, RESOLVED, AUTO_RESOLVED, but the exception workflow moves exceptions between NEW, RESOLVED, AUTO_RESOLVED, DISMISSED" in problems[0].message


def test_write_removes_stale_files_and_check_reports_drift(tmp_path):
    releases = tmp_path / "releases"
    pack = _pack()
    root = write_bundle(pack, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(pack, releases)
    assert not stale.exists() and check_bundle(pack, releases, tmp_path) == []
    (root / "pipeline" / "exception_workflow.sql").write_text("-- edited by hand\n", encoding="utf-8")
    problems = check_bundle(pack, releases, tmp_path)
    assert [(p.path, p.message.split(";")[0]) for p in problems] == [("releases/custodial-exceptions/pipeline/exception_workflow.sql", "stale: the exception workflow or the model changed since it was rendered")]


def test_cli_renders_and_checks(tmp_path, capsys):
    out = tmp_path / "releases"
    assert main(["--root", str(REPO), "exceptions", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 1
    assert "not rendered; run astra-data exceptions render" in capsys.readouterr().out
    assert main(["--root", str(REPO), "exceptions", "render", "--domains", str(DOMAINS), "--releases", str(out)]) == 0
    assert "rendered custodial-exceptions: exception workflow (model 1.0)" in capsys.readouterr().out
    assert main(["--root", str(REPO), "exceptions", "render", "--domains", str(DOMAINS), "--releases", str(out), "--check"]) == 0
    assert "Exceptions bundles are current for 1 domain pack" in capsys.readouterr().out
