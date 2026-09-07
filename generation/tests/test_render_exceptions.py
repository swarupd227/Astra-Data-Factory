"""S3.2.5: every rejected row reaches the exception store as NEW with the full source record, and the run ledger proves it."""

from __future__ import annotations

import re

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import compile_config
from astra_data.render import render_bundle
from tests.test_render_merge import _paired_inputs
from tests.test_validate import EXAMPLE

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


@pytest.fixture(scope="module")
def compiled(inputs):
    registry, catalog, packs = inputs
    return compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO)


def _files(compiled):
    return render_bundle(compiled)


def test_every_exception_is_written_new_and_nothing_is_written_open(compiled):
    files = _files(compiled)
    sql = files["pipeline/pershing_position_merge.sql"] + files["pipeline/pershing_position_resolve.sql"]
    assert "'OPEN'" not in sql
    inserts = re.findall(r'INSERT INTO \{\{ DATABASE \}\}\."EXCEPTIONS"\."PERSHING_POSITION".*?;\n', sql, flags=re.S)  # messages may hold a ';'
    assert len(inserts) >= 8 and all("'NEW'" in i for i in inserts)


def test_the_store_requires_the_full_source_record(compiled):
    ddl = _files(compiled)["ddl/silver_pershing_position.sql"]
    assert re.search(r'"PAYLOAD"\s+STRING NOT NULL COMMENT \'The full source record, as JSON', ddl)
    assert "'NEW when written; then RESOLVED, AUTO_RESOLVED or DISMISSED by triage'" in ddl
    assert "'Pipeline stage that raised it: parse, merge or resolution'" in ddl


def test_parse_problems_are_routed_with_the_raw_line_as_payload(compiled):
    merge = _files(compiled)["pipeline/pershing_position_merge.sql"]
    routed = re.search(r"-- 2\. Every parse problem.*?-- 3\. The mode", merge, flags=re.S).group(0)
    assert 'SELECT UUID_STRING(), p."CODE", p."LEVEL", \'parse\', \'pershing\', p."FIELD", p."MESSAGE", NULL,' in routed
    assert 'TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(\'FILE_NAME\', l."FILE_NAME", \'LINE_NUMBER\', l."LINE_NUMBER", \'RECORD\', p."RECORD", \'LINE\', l."LINE"))' in routed
    assert 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PARSE_PROBLEMS" p' in routed
    assert 'LEFT JOIN {{ DATABASE }}."BRONZE"."PERSHING_POSITION_LINES" l ON l."FILE_NAME" = p."FILE_NAME" AND l."LINE_NUMBER" = p."LINE_NUMBER"' in routed
    # file-level parse problems carry the file's metadata row instead of a line
    assert 'IFF(p."LEVEL" = \'file\',' in routed and 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILE_METADATA" m WHERE m."FILE_NAME" = :file_name' in routed
    # the lines the parse excluded count as rejected rows of the run, once per line
    assert 'rejected := rejected + (SELECT COUNT(DISTINCT p."LINE_NUMBER") FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_PARSE_PROBLEMS" p WHERE p."FILE_NAME" = :file_name AND p."LEVEL" = \'record\');' in routed
    # the parse problems are routed before the file can be rejected whole, so a rejected file's problems are kept too
    assert merge.index("Every parse problem") < merge.index("MERGE_MODE_UNKNOWN")


def test_file_level_exceptions_carry_the_file_metadata(compiled):
    merge = _files(compiled)["pipeline/pershing_position_merge.sql"]
    for code in ("MERGE_MODE_UNKNOWN", "MERGE_OUT_OF_ORDER"):
        block = merge[merge.index(f"'{code}'") :]
        assert '(SELECT TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)) FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_FILE_METADATA" m WHERE m."FILE_NAME" = :file_name)' in block[: block.index(";")]
    assert "rejected_files := rejected_files + 1;" in merge


def test_the_run_ledger_is_opened_by_process_and_written_by_every_stage(compiled):
    files = _files(compiled)
    ddl = files["ddl/bronze_pershing_position.sql"]
    ledger = ddl[ddl.index('"PERSHING_POSITION_RUNS"') :]
    for column in ("RUN_ID", "STARTED_AT", "FINISHED_AT", "FILES_REGISTERED", "FILES_MERGED", "FILES_REJECTED", "ROWS_MERGED", "ROWS_PROJECTED", "ROWS_REJECTED", "EXCEPTIONS_FILE", "EXCEPTIONS_RECORD", "EXCEPTIONS_FIELD"):
        assert f'"{column}"' in ledger
    process = files["pipeline/pershing_position_process.sql"]
    assert 'INSERT INTO {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RUNS" ("RUN_ID", "STARTED_AT", "FILES_REGISTERED"' in process
    assert process.index("INSERT INTO") < process.index('"PERSHING_POSITION_INTAKE"(:run_id)')
    assert '"EXCEPTIONS_RECORD" = (SELECT COUNT(*) FROM {{ DATABASE }}."EXCEPTIONS"."PERSHING_POSITION" e WHERE e."RUN_ID" = :run_id AND e."LEVEL" = \'record\')' in process
    assert '"FINISHED_AT" = SYSDATE()' in process and process.index('"FINISHED_AT"') > process.index('"PERSHING_POSITION_RESOLVE"(:run_id)')
    assert 'UPDATE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RUNS" SET "FILES_REGISTERED" = :registered WHERE "RUN_ID" = :RUN_ID;' in files["pipeline/pershing_position_intake.sql"]
    merge = files["pipeline/pershing_position_merge.sql"]
    assert 'SET "FILES_MERGED" = :merged, "FILES_REJECTED" = :rejected_files, "ROWS_MERGED" = :rows_merged, "ROWS_REJECTED" = "ROWS_REJECTED" + :rejected WHERE "RUN_ID" = :RUN_ID;' in merge
    assert merge.count("rejected := rejected + SQLROWCOUNT;") == 2  # blank keys, duplicate keys
    assert "rows_merged := rows_merged + inserted + updated;" in merge
    resolve = files["pipeline/pershing_position_resolve.sql"]
    assert 'held := (SELECT COUNT(*) FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RESOLVED" s WHERE ' in resolve
    assert 'SET "ROWS_PROJECTED" = :projected, "ROWS_REJECTED" = "ROWS_REJECTED" + :held WHERE "RUN_ID" = :RUN_ID;' in resolve


def test_paired_rows_are_rejected_once_each(tmp_path, inputs):
    compiled = _paired_inputs(tmp_path, inputs)
    merge = render_bundle(compiled)[f"pipeline/{compiled.id}_merge.sql"]
    # a second record for the same keys is a duplicate, not also an unpaired record: the partner check ranks first records only
    unpaired = merge[merge.index("'PAIR_INCOMPLETE'") :]
    assert 'x."NTH" = 1 AND (' in unpaired[: unpaired.index(";")]
    assert merge.count("rejected := rejected + SQLROWCOUNT;") == 2 + 3 * 2  # key checks + (blank, duplicate, incomplete) per paired record


def test_rendered_tests_compare_the_ledger_with_the_store(compiled):
    files = _files(compiled)
    equal = files["tests/pershing_position_rejected_rows_equal_exceptions.sql"]
    assert 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RUNS" r' in equal
    assert 'COUNT(DISTINCT "SOURCE_FILE" || \'#\' || "SOURCE_LINE"::STRING) AS "REJECTED_IN_STORE"' in equal
    assert "WHERE \"LEVEL\" = 'record'" in equal
    assert 'WHERE r."FINISHED_AT" IS NOT NULL AND COALESCE(e."REJECTED_IN_STORE", 0) <> r."ROWS_REJECTED";' in equal
    payload = files["tests/pershing_position_exceptions_have_payload_and_start_new.sql"]
    assert "WHERE \"PAYLOAD\" IS NULL OR \"STATUS\" NOT IN ('NEW', 'RESOLVED', 'AUTO_RESOLVED', 'DISMISSED');" in payload
    docs = files["docs/pershing_position.md"]
    assert "## Exceptions" in docs and "state `NEW`" in docs and "BRONZE.PERSHING_POSITION_RUNS" in docs
    atlan = files["atlan/pershing_position.json"]
    assert "PERSHING_POSITION_RUNS" in atlan
