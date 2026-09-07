"""Rendering a release bundle from a compiled config (S3.1.2)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.bundle import Target, check_bundles, deploy, load_bundle, run_tests
from astra_data.cli import main
from astra_data.compiler import compile_config
from astra_data.render import RenderError, check_bundle, render_bundle, write_bundle
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        return []


@pytest.fixture(scope="module")
def compiled():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)


def test_the_bundle_has_every_artifact_kind(compiled):
    files = render_bundle(compiled)
    assert set(files) == {
        "manifest.yaml",
        "PROVENANCE.json",
        "ddl/bronze_pershing_position.sql",
        "ddl/silver_pershing_position.sql",
        "pipeline/pershing_position_pipe.sql",
        "pipeline/pershing_position_lines.sql",
        "pipeline/pershing_position_parse.sql",
        "pipeline/pershing_position_intake.sql",
        "pipeline/pershing_position_merge.sql",
        "pipeline/pershing_position_process.sql",
        "pipeline/pershing_position_tasks.sql",
        "dq/dmf_pershing_position.sql",
        "tests/pershing_position_files_not_stuck.sql",
        "tests/pershing_position_files_parse_complete.sql",
        "tests/pershing_position_files_registered_once.sql",
        "tests/pershing_position_problem_codes_known.sql",
        "tests/pershing_position_silver_active_keys_unique.sql",
        "tests/pershing_position_exception_codes_known.sql",
        "tests/pershing_position_files_merged_once_logged.sql",
        "tests/pershing_position_detail_keys_unique_per_file.sql",
        "tests/pershing_position_detail_lines_traceable.sql",
        "docs/pershing_position.md",
        "atlan/pershing_position.json",
    }
    manifest = files["manifest.yaml"]
    assert "bundle: pershing-position" in manifest and "source: pershing_position" in manifest and re.search(r'version: "[0-9a-f]{12}"', manifest)
    assert manifest.index("ddl/bronze_pershing_position.sql") < manifest.index("ddl/silver_pershing_position.sql") < manifest.index("pipeline/pershing_position_pipe.sql") < manifest.index("pipeline/pershing_position_lines.sql") < manifest.index("pipeline/pershing_position_parse.sql") < manifest.index("pipeline/pershing_position_intake.sql") < manifest.index("pipeline/pershing_position_merge.sql") < manifest.index("pipeline/pershing_position_process.sql") < manifest.index("pipeline/pershing_position_tasks.sql") < manifest.index("dq/dmf_pershing_position.sql")
    for name, text in files.items():
        if name.endswith(".sql"):
            assert set(re.findall(r"\{\{\s*([A-Z_]+)\s*\}\}", text)) <= {"DATABASE", "WAREHOUSE_SIMPLE", "WAREHOUSE_MEDIUM", "WAREHOUSE_COMPLEX"}, name


def test_rendering_an_unchanged_config_is_byte_identical(compiled, tmp_path):
    first = render_bundle(compiled)
    second = render_bundle(compiled)
    assert first == second
    root = write_bundle(compiled, tmp_path / "a")
    twin_root = write_bundle(compiled, tmp_path / "b")
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        twin = twin_root / path.relative_to(root)
        assert path.read_bytes() == twin.read_bytes(), path.name
    assert check_bundle(compiled, tmp_path / "a", tmp_path) == []


def test_bronze_ddl_types_every_field_from_the_spec(compiled):
    ddl = render_bundle(compiled)["ddl/bronze_pershing_position.sql"]
    tables = re.findall(r'CREATE ICEBERG TABLE IF NOT EXISTS \{\{ DATABASE \}\}\."BRONZE"\."([A-Z_]+)"', ddl)
    assert tables == ["PERSHING_POSITION_RAW_LINES", "PERSHING_POSITION_FILES"]
    assert 'ALTER ICEBERG TABLE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RAW_LINES" MODIFY COLUMN "LINE" SET TAG {{ DATABASE }}."CONTROL"."PII" = \'raw_record\';' in ddl
    assert re.search(r'"FILE_CONTENT_KEY"\s+STRING', ddl) and re.search(r'"INGESTED_AT"\s+TIMESTAMP_NTZ\(6\) NOT NULL', ddl)
    assert '"STATUS"' in ddl and "'pending, merged or rejected'" in ddl
    assert "PERSHING_POSITION_DETAIL" in ddl and "pipeline/pershing_position_parse.sql" in ddl
    for table in tables:
        assert f"BASE_LOCATION = 'bronze/{table.lower()}/'" in ddl


def test_pipeline_scopes_lines_registers_files_and_runs_stages(compiled):
    files = render_bundle(compiled)
    pipe = files["pipeline/pershing_position_pipe.sql"]
    assert 'CREATE PIPE IF NOT EXISTS {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PIPE"' in pipe and "AUTO_INGEST = TRUE" in pipe
    assert 'COPY INTO {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RAW_LINES" ("FILE_NAME", "ROW_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT")' in pipe
    assert "SELECT METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, $1, METADATA$FILE_CONTENT_KEY," in pipe and "METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME" in pipe
    assert 'FROM @{{ DATABASE }}."BRONZE"."LANDING"/pershing/' in pipe  # the custodian's landing prefix
    assert "FILE_FORMAT = (FORMAT_NAME = '{{ DATABASE }}.BRONZE.RAW_LINES')" in pipe
    assert "PATTERN = '.*(pershing/GCUS..*.POS..*\\.dat|pershing/GCUS..*.TRN..*\\.dat)';" in pipe  # LIKE: % is .*, _ is .

    lines = files["pipeline/pershing_position_lines.sql"]
    assert 'CREATE OR REPLACE VIEW {{ DATABASE }}."BRONZE"."PERSHING_POSITION_LINES"' in lines
    assert 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RAW_LINES";' in lines and "RAW_LINES\"\nWHERE" not in lines

    intake = files["pipeline/pershing_position_intake.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_INTAKE"(RUN_ID STRING)' in intake
    assert 'FROM {{ DATABASE }}."CONTROL"."FILE_LOAD_LOG" l' in intake and "l.\"STATUS\" = 'LOADED'" in intake and "'pending'" in intake
    assert 'NOT EXISTS (SELECT 1 FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILES" f WHERE f."FILE_NAME" = l."FILE_NAME")' in intake

    process = files["pipeline/pershing_position_process.sql"]
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"()' in process
    assert 'CALL {{ DATABASE }}."BRONZE"."PERSHING_POSITION_INTAKE"(:run_id);' in process and "run_id STRING DEFAULT UUID_STRING()" in process
    assert process.index('"PERSHING_POSITION_INTAKE"(:run_id)') < process.index('CALL {{ DATABASE }}."BRONZE"."PERSHING_POSITION_MERGE"(:run_id);')
    assert 'l."ROW_COUNT"' in intake

    tasks = files["pipeline/pershing_position_tasks.sql"]
    assert 'CREATE OR REPLACE TASK {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"' in tasks
    assert "WAREHOUSE = {{ WAREHOUSE_MEDIUM }}" in tasks and "SCHEDULE = '10 MINUTE'" in tasks  # processing.target_lag_minutes
    assert 'CALL {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS"();' in tasks and 'ALTER TASK {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PROCESS" RESUME;' in tasks


def test_task_name_carries_the_custodian_prefix_once(compiled, tmp_path):
    from astra_data.render.names import task_name

    assert task_name(compiled) == "PERSHING_POSITION_PROCESS"
    other = compiled.__class__(**{**compiled.__dict__, "source": {**compiled.source, "id": "gcus_positions", "custodian": "pershing"}})
    assert task_name(other) == "PERSHING_GCUS_POSITIONS_PROCESS"


def test_dmfs_measure_rows_required_fields_and_merge_keys(compiled):
    dmf = render_bundle(compiled)["dq/dmf_pershing_position.sql"]
    table = '{{ DATABASE }}."BRONZE"."PERSHING_POSITION_DETAIL"'
    assert f"ALTER DYNAMIC TABLE {table} SET DATA_METRIC_SCHEDULE = 'TRIGGER_ON_CHANGES';" in dmf
    assert f"ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.ROW_COUNT ON ();" in dmf
    assert f'ALTER DYNAMIC TABLE {table} ADD DATA METRIC FUNCTION SNOWFLAKE.CORE.NULL_COUNT ON ("ACCOUNT_NUMBER");' in dmf
    assert "DUPLICATE_COUNT" not in dmf  # the merge key is composite
    assert "trailer_control_total (file, error): trailer record count equals the number of detail records" in dmf


def test_tests_check_files_problem_codes_and_keys(compiled):
    files = render_bundle(compiled)
    assert "WHERE \"STATUS\" = 'pending' AND \"FIRST_SEEN_AT\" < DATEADD('hour', -24, SYSDATE());" in files["tests/pershing_position_files_not_stuck.sql"]
    assert 'LEFT JOIN {{ DATABASE }}."CONTROL"."REJECTION_CODES" r ON r."CODE" = p."CODE"' in files["tests/pershing_position_problem_codes_known.sql"]
    assert 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PARSE_PROBLEMS" p' in files["tests/pershing_position_problem_codes_known.sql"]
    assert 'WHERE "FILE_PROBLEMS" > 0 OR "EXCLUDED_ROWS" > 0;' in files["tests/pershing_position_files_parse_complete.sql"]
    keys = files["tests/pershing_position_detail_keys_unique_per_file.sql"]
    assert 'GROUP BY "FILE_NAME", "ACCOUNT_NUMBER", "CUSIP"' in keys and "HAVING COUNT(*) > 1;" in keys


def test_docs_describe_layout_mappings_rules_and_delivery(compiled):
    doc = render_bundle(compiled)["docs/pershing_position.md"]
    assert doc.startswith("# pershing_position\n")
    assert "| Source Spec | `pershing_gcus` version `2017-07-25`, in force from 2017-07-25 |" in doc
    assert "### detail → `BRONZE.PERSHING_POSITION_DETAIL` (dynamic table, target lag 10 minutes)" in doc
    assert "| `QUANTITY` | detail.quantity | 23-40 | 9(13)V9(5) | NUMBER(18,5) |  | page 13, line 2 |" in doc
    assert "| `POSITION.QUANTITY` (NUMBER(28,8)) | detail.quantity (decimal) | signed_implied_decimal(13, 5) | pershing_gcus.quantity_sign |" in doc
    assert "| `pershing_gcus.quantity_sign` | normalisation | confirmed | spec pershing_gcus 2017-07-25 page 13 line 6 |" in doc
    assert "| `trailer_control_total` | file | error | trailer record count equals the number of detail records |" in doc
    assert "Cutoff 06:00 America/New_York on mon, tue, wed, thu, fri." in doc and "- `pershing/GCUS_%_POS_%.dat`: Positions" in doc
    assert "Merge: `refresh_flag` in the header says refresh or update (R = refresh, U = update); scope remote_id; keys account_number, cusip." in doc
    assert "Task `BRONZE.PERSHING_POSITION_PROCESS`" in doc


def test_atlan_payload_carries_assets_lineage_and_glossary_terms(compiled):
    payload = json.loads(render_bundle(compiled)["atlan/pershing_position.json"])
    entities = payload["entities"]
    by_type = {}
    for e in entities:
        by_type.setdefault(e["typeName"], []).append(e)
    tables = {e["attributes"]["qualifiedName"] for e in by_type["Table"]}
    assert "{{ ATLAN_CONNECTION }}/{{ DATABASE }}/BRONZE/PERSHING_POSITION_DETAIL" in tables and "{{ ATLAN_CONNECTION }}/{{ DATABASE }}/SILVER/POSITION" in tables
    assert "{{ ATLAN_CONNECTION }}/{{ DATABASE }}/BRONZE/PERSHING_POSITION_RAW_LINES" in tables
    silver = next(e for e in by_type["Table"] if e["attributes"]["name"] == "POSITION")
    assert silver["attributes"]["meanings"][0]["termName"] == "Position" and silver["attributes"]["certificateStatus"] == "VERIFIED"
    pii = [c for c in by_type["Column"] if c.get("classifications")]
    assert any(c["attributes"]["name"] == "ACCOUNT_NUMBER" and c["classifications"][0]["attributes"]["category"] == "account_number" for c in pii)
    process = next(p for p in by_type["Process"] if "->" in p["attributes"]["name"])
    merge_process = next(p for p in by_type["Process"] if p["attributes"]["name"].endswith("into Silver"))
    assert merge_process["attributes"]["outputs"][0]["uniqueAttributes"]["qualifiedName"].endswith("/SILVER/PERSHING_POSITION_DETAIL")
    assert process["attributes"]["inputs"][0]["uniqueAttributes"]["qualifiedName"].endswith("/BRONZE/PERSHING_POSITION_DETAIL")
    assert process["attributes"]["outputs"][0]["uniqueAttributes"]["qualifiedName"].endswith("/SILVER/POSITION")
    assert "POSITION.QUANTITY <- detail.quantity via signed_implied_decimal(13, 5)" in process["attributes"]["description"]


def test_provenance_records_inputs_and_every_file_digest(compiled):
    files = render_bundle(compiled)
    provenance = json.loads(files["PROVENANCE.json"])
    assert provenance["bundle"]["name"] == "pershing-position" and provenance["bundle"]["version"] in files["manifest.yaml"]
    assert provenance["inputs"]["spec"]["path"] == "specs/pershing_gcus/2017-07-25.yaml" and provenance["inputs"]["pattern"] == "fixed_width_multi_record"
    assert provenance["inputs"]["rules"][0]["id"] == "pershing_gcus.quantity_sign"
    assert set(provenance["files"]) == set(files) - {"PROVENANCE.json"}
    assert all(len(d) == 64 for d in provenance["files"].values())
    assert "rendered_at" not in provenance


def test_the_written_bundle_passes_the_bundle_contract_and_deploys(compiled, tmp_path):
    root = write_bundle(compiled, tmp_path / "releases")
    bundles, problems = check_bundles(tmp_path / "releases", tmp_path)
    assert problems == [] and [b.name for b in bundles] == ["pershing-position"]
    bundle = load_bundle(root, tmp_path)
    assert [bundle.relative(s) for s in bundle.steps] == [
        "ddl/bronze_pershing_position.sql",
        "ddl/silver_pershing_position.sql",
        "pipeline/pershing_position_pipe.sql",
        "pipeline/pershing_position_lines.sql",
        "pipeline/pershing_position_parse.sql",
        "pipeline/pershing_position_intake.sql",
        "pipeline/pershing_position_merge.sql",
        "pipeline/pershing_position_process.sql",
        "pipeline/pershing_position_tasks.sql",
        "dq/dmf_pershing_position.sql",
    ]
    executor = FakeExecutor()
    result = deploy(bundle, Target("dev"), executor)
    assert len(result.steps) == 10 and "{{" not in "".join(executor.scripts) and 'ASTRA_DEV."BRONZE"' in executor.scripts[0] and "ASTRA_DEV_WH_MEDIUM" in executor.scripts[4] and "ASTRA_DEV_WH_MEDIUM" in executor.scripts[8]
    assert "FROM @ASTRA_DEV.\"BRONZE\".\"LANDING\"/pershing/" in executor.scripts[2] and "FORMAT_NAME = 'ASTRA_DEV.BRONZE.RAW_LINES'" in executor.scripts[2]
    results = run_tests(bundle, Target("dev"), executor)
    assert len(results) == 9 and all(r.passed for r in results)


def test_write_removes_stale_files_and_check_reports_drift(compiled, tmp_path):
    releases = tmp_path / "releases"
    root = write_bundle(compiled, releases)
    stale = root / "tests" / "old.sql"
    stale.write_text("SELECT 1;", encoding="utf-8")
    write_bundle(compiled, releases)
    assert not stale.exists()
    (root / "docs" / "pershing_position.md").write_text("edited\n", encoding="utf-8")
    (root / "atlan" / "pershing_position.json").unlink()
    problems = check_bundle(compiled, releases, tmp_path)
    assert sorted((p.path, p.message.split(";")[0]) for p in problems) == [
        ("releases/pershing-position/atlan/pershing_position.json", "not rendered for pershing_position"),
        ("releases/pershing-position/docs/pershing_position.md", "stale: the config or its inputs changed since it was rendered"),
    ]


def test_a_source_without_delivery_files_cannot_be_rendered(compiled):
    without = compiled.__class__(**{**compiled.__dict__, "delivery": None})
    with pytest.raises(RenderError) as excinfo:
        render_bundle(without)
    assert [p.message for p in excinfo.value.problems] == ["delivery.files is needed to render the source: it says which landed files belong to this source"]

    scattered = compiled.__class__(**{**compiled.__dict__, "delivery": {**compiled.delivery, "files": [{"pattern": "pershing/GCUS_%.dat"}, {"pattern": "schwab/POS_%.dat"}]}})
    with pytest.raises(RenderError) as excinfo:
        render_bundle(scattered)
    assert excinfo.value.problems[0].message.startswith("delivery.files patterns must all sit under one custodian folder of the landing prefix")
    rootless = compiled.__class__(**{**compiled.__dict__, "delivery": {**compiled.delivery, "files": [{"pattern": "GCUS_%.dat"}]}})
    with pytest.raises(RenderError):
        render_bundle(rootless)


def test_like_patterns_become_snowflake_regular_expressions():
    from astra_data.render.names import like_to_regex

    assert like_to_regex("pershing/GCUS_%_POS_%.dat") == "pershing/GCUS..*.POS..*\\.dat"
    assert like_to_regex("a+b(c).txt") == "a\\+b\\(c\\)\\.txt"


def _args(root: Path, out: Path) -> list[str]:
    return ["--root", str(root), "render", "--specs", str(root / "specs"), "--rules", str(root / "rules"), "--domains", str(root / "domains"), "--out", str(out)]


def test_cli_render_writes_and_checks(tmp_path, capsys):
    root = tmp_path
    for name in ("specs", "rules", "domains", "configs"):
        shutil.copytree(REPO / name, root / name)
    out = root / "releases"
    assert main([*_args(root, out), "--check", str(root / "configs")]) == 1
    assert "not rendered for pershing_position" in capsys.readouterr().out
    assert main([*_args(root, out), str(root / "configs")]) == 0
    assert "rendered pershing-position: 23 files -> releases/pershing-position" in capsys.readouterr().out
    assert main([*_args(root, out), "--check", str(root / "configs")]) == 0
    assert "release bundles are current for 1 config" in capsys.readouterr().out
    assert main(["--root", str(root), "bundles", "check", str(out)]) == 0

    (root / "configs" / "examples" / "pershing_position.yaml").write_text(VALID.replace("pattern: fixed_width_multi_record", "pattern: cobol_copybook"), encoding="utf-8")
    assert main(["--format", "github", *_args(root, out), str(root / "configs")]) == 1
    out_text = capsys.readouterr().out
    assert "pattern 'cobol_copybook' is not in the pattern library" in out_text and "nothing rendered" in out_text
