from pathlib import Path

import pytest

from astra_data.bundle import BundleError, DeployError, Target, check_bundles, deploy, load_bundle, render, run_tests

MANIFEST = """\
bundle: pershing-position
version: "2026.09"
source: pershing_position
steps:
  - ddl/bronze.sql
  - pipeline/parse.sql
tests:
  - tests/unit/*.sql
"""


class FakeExecutor:
    def __init__(self, rows_by_marker: dict[str, list[tuple]] | None = None, fail_on: str | None = None) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.rows_by_marker = rows_by_marker or {}
        self.fail_on = fail_on

    def execute_script(self, sql: str) -> None:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("SQL compilation error: object does not exist")
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return rows
        return []


def make_bundle(root: Path, manifest: str = MANIFEST) -> Path:
    bundle = root / "pershing-position"
    (bundle / "ddl").mkdir(parents=True)
    (bundle / "pipeline").mkdir()
    (bundle / "tests" / "unit").mkdir(parents=True)
    (bundle / "manifest.yaml").write_text(manifest, encoding="utf-8")
    (bundle / "ddl" / "bronze.sql").write_text('CREATE ICEBERG TABLE IF NOT EXISTS "{{ DATABASE }}".BRONZE.PERSHING_POSITION (LINE STRING);\n', encoding="utf-8")
    (bundle / "pipeline" / "parse.sql").write_text("CREATE OR REPLACE DYNAMIC TABLE {{DATABASE}}.SILVER.POSITION WAREHOUSE = {{ WAREHOUSE_MEDIUM }} AS SELECT 1;\n", encoding="utf-8")
    (bundle / "tests" / "unit" / "no_null_accounts.sql").write_text('SELECT * FROM "{{ DATABASE }}".SILVER.POSITION WHERE ACCOUNT IS NULL;\n', encoding="utf-8")
    (bundle / "tests" / "unit" / "control_total.sql").write_text("SELECT 'trailer' WHERE 1 = 0 -- CONTROL_TOTAL;\n", encoding="utf-8")
    return bundle


def test_target_names_follow_the_foundation():
    target = Target("qa")
    assert target.database == "ASTRA_QA"
    assert target.parameters()["WAREHOUSE_COMPLEX"] == "ASTRA_QA_WH_COMPLEX"
    assert Target("dev", "ENV").database == "ENV_DEV"


@pytest.mark.parametrize("environment,prefix", [("Prod", "ASTRA"), ("p", "ASTRA"), ("dev", "astra")])
def test_target_rejects_bad_names(environment, prefix):
    with pytest.raises(ValueError):
        Target(environment, prefix)


def test_render_fills_known_placeholders_and_names_unknown_ones():
    assert render("USE {{ DATABASE }}; -- {{DATABASE}} {{ WAREHOUSE_SIMPLE }}", Target("dev")) == "USE ASTRA_DEV; -- ASTRA_DEV ASTRA_DEV_WH_SIMPLE"
    with pytest.raises(KeyError) as excinfo:
        render("SELECT '{{ SCHEMA }}'", Target("dev"))
    assert excinfo.value.args[0] == "SCHEMA"


def test_load_bundle_reads_steps_in_order_and_expands_tests(tmp_path):
    bundle = load_bundle(make_bundle(tmp_path), repo_root=tmp_path)
    assert bundle.name == "pershing-position" and bundle.version == "2026.09" and bundle.source == "pershing_position"
    assert [bundle.relative(s) for s in bundle.steps] == ["ddl/bronze.sql", "pipeline/parse.sql"]
    assert [bundle.relative(t) for t in bundle.tests] == ["tests/unit/control_total.sql", "tests/unit/no_null_accounts.sql"]


def test_missing_manifest_and_missing_step_are_problems(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(BundleError) as excinfo:
        load_bundle(tmp_path / "empty", repo_root=tmp_path)
    assert excinfo.value.problems[0].message == "bundle has no manifest.yaml"

    root = make_bundle(tmp_path)
    (root / "pipeline" / "parse.sql").unlink()
    with pytest.raises(BundleError) as excinfo:
        load_bundle(root, repo_root=tmp_path)
    assert [p.message for p in excinfo.value.problems] == ["steps[1] 'pipeline/parse.sql' does not exist in the bundle"]


def test_manifest_schema_errors_are_reported_with_lines(tmp_path):
    root = make_bundle(tmp_path, MANIFEST.replace("source: pershing_position\n", "").replace("  - ddl/bronze.sql", "  - ../escape.sql"))
    with pytest.raises(BundleError) as excinfo:
        load_bundle(root, repo_root=tmp_path)
    messages = [p.message for p in excinfo.value.problems]
    assert "top level: missing required field source" in messages
    assert any(m.startswith("steps[0]: '../escape.sql' does not match") for m in messages)


def test_unknown_placeholder_is_caught_before_any_connection(tmp_path):
    root = make_bundle(tmp_path)
    (root / "ddl" / "bronze.sql").write_text("USE {{ SCHEMA }};", encoding="utf-8")
    with pytest.raises(BundleError) as excinfo:
        load_bundle(root, repo_root=tmp_path)
    assert excinfo.value.problems[0].path == "pershing-position/ddl/bronze.sql"
    assert excinfo.value.problems[0].message.startswith("unknown placeholder {{ SCHEMA }}; known placeholders are ENVIRONMENT, PREFIX, DATABASE")


def test_directory_name_must_match_manifest(tmp_path):
    root = make_bundle(tmp_path, MANIFEST.replace("bundle: pershing-position", "bundle: other-name"))
    with pytest.raises(BundleError) as excinfo:
        load_bundle(root, repo_root=tmp_path)
    assert "must match the directory name 'pershing-position'" in excinfo.value.problems[0].message


def test_deploy_renders_every_step_then_runs_them_in_order(tmp_path):
    bundle = load_bundle(make_bundle(tmp_path), repo_root=tmp_path)
    executor = FakeExecutor()
    result = deploy(bundle, Target("qa"), executor)
    assert result.steps == ("ddl/bronze.sql", "pipeline/parse.sql") and result.target == "ASTRA_QA"
    assert executor.scripts[0].startswith('CREATE ICEBERG TABLE IF NOT EXISTS "ASTRA_QA".BRONZE.PERSHING_POSITION')
    assert "WAREHOUSE = ASTRA_QA_WH_MEDIUM" in executor.scripts[1]


def test_deploy_stops_at_the_failing_step_and_names_it(tmp_path):
    bundle = load_bundle(make_bundle(tmp_path), repo_root=tmp_path)
    executor = FakeExecutor(fail_on="DYNAMIC TABLE")
    with pytest.raises(DeployError) as excinfo:
        deploy(bundle, Target("dev"), executor)
    assert excinfo.value.step == "pipeline/parse.sql"
    assert "object does not exist" in str(excinfo.value)
    assert len(executor.scripts) == 1


def test_tests_pass_on_zero_rows_and_fail_with_a_sample(tmp_path):
    bundle = load_bundle(make_bundle(tmp_path), repo_root=tmp_path)
    executor = FakeExecutor(rows_by_marker={"ACCOUNT IS NULL": [("A1", None), ("A2", None), ("A3", None)]})
    results = run_tests(bundle, Target("dev"), executor, sample_size=2)
    by_name = {r.test: r for r in results}
    assert by_name["tests/unit/control_total.sql"].passed
    failing = by_name["tests/unit/no_null_accounts.sql"]
    assert not failing.passed and failing.failing_rows == 3
    assert failing.detail == "3 failing rows: A1, None; A2, None"
    assert '"ASTRA_DEV".SILVER.POSITION' in executor.queries[1]


def test_check_bundles_discovers_and_collects_problems(tmp_path):
    releases = tmp_path / "releases"
    make_bundle(releases)
    broken = releases / "broken-one"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("bundle: broken-one\nversion: '1'\nsource: x\nsteps:\n  - missing.sql\n", encoding="utf-8")
    (releases / "not-a-bundle").mkdir()

    bundles, problems = check_bundles(releases, repo_root=tmp_path)
    assert [b.name for b in bundles] == ["pershing-position"]
    assert [p.message for p in problems] == ["steps[0] 'missing.sql' does not exist in the bundle"]
    assert check_bundles(tmp_path / "nope") == ([], [])
