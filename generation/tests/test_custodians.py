from datetime import datetime, timezone
from pathlib import Path

from astra_data.bundle import Target
from astra_data.cli import main
from astra_data.custodians import custodians_from_configs, sync, sync_statements
from tests.test_validate import VALID

NOW = datetime(2026, 9, 6, 9, 30, 0, tzinfo=timezone.utc)

# The example config already carries delivery and alerts; tests build on the
# part before them so they can vary those blocks.
BASE = VALID.split("\n# When the custodian delivers")[0] + "\n"

DELIVERY = """
delivery:
  cutoff_time: "06:00"
  timezone: America/New_York
  files:
    - pattern: pershing/GCUS_%_POS_%.dat
      description: positions
    - pattern: pershing/GCUS_%_TRN_%.dat

alerts:
  late: critical
  task_failure: warning
"""


class FakeExecutor:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        return []


def write_config(root: Path, custodian: str, source: str, extra: str = "") -> Path:
    folder = root / custodian
    folder.mkdir(parents=True, exist_ok=True)
    text = BASE.replace("id: pershing_position", f"id: {source}").replace("custodian: pershing", f"custodian: {custodian}") + extra
    path = folder / f"{source}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_example_config_with_delivery_is_valid_and_folds_into_one_custodian(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY)
    custodians, problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert problems == []
    (c,) = custodians
    assert c.custodian_id == "pershing" and c.name == "Pershing"
    assert c.cutoff_time == "06:00" and c.timezone == "America/New_York"
    assert c.business_days_text == "MON,TUE,WED,THU,FRI"
    assert c.late_severity == "critical" and c.failure_severity == "warning"
    assert [f.pattern for f in c.files] == ["pershing/GCUS_%_POS_%.dat", "pershing/GCUS_%_TRN_%.dat"]
    assert c.files[0].description == "positions"
    assert c.sources == ("pershing/pershing_position.yaml",)


def test_sources_of_one_custodian_are_merged_and_must_agree(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY)
    write_config(tmp_path, "pershing", "pershing_price", DELIVERY.replace("- pattern: pershing/GCUS_%_TRN_%.dat\n", "- pattern: pershing/GCUS_%_PRC_%.dat\n"))
    custodians, problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert problems == []
    (c,) = custodians
    assert sorted(f.pattern for f in c.files) == ["pershing/GCUS_%_POS_%.dat", "pershing/GCUS_%_PRC_%.dat", "pershing/GCUS_%_TRN_%.dat"]
    assert c.sources == ("pershing/pershing_position.yaml", "pershing/pershing_price.yaml")

    # Files are folded in name order, so pershing_lot becomes the baseline and the others differ from it.
    write_config(tmp_path, "pershing", "pershing_lot", DELIVERY.replace('cutoff_time: "06:00"', 'cutoff_time: "07:30"').replace("late: critical", "late: info"))
    _, problems = custodians_from_configs([tmp_path], root=tmp_path)
    messages = [p.message for p in problems]
    assert any("delivery for custodian 'pershing' (cutoff, timezone, business days) differs from pershing/pershing_lot.yaml" in m for m in messages)
    assert any("alerts.late for custodian 'pershing' is 'critical' here and 'info' in pershing/pershing_lot.yaml" in m for m in messages)
    assert {p.path for p in problems} == {"pershing/pershing_position.yaml", "pershing/pershing_price.yaml"}


def test_configs_without_delivery_or_alerts_are_not_custodian_rows(tmp_path):
    write_config(tmp_path, "schwab", "schwab_position")
    custodians, problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert custodians == [] and problems == []


def test_alerts_without_delivery_never_go_late_but_carry_failure_severity(tmp_path):
    write_config(tmp_path, "schwab", "schwab_position", "\nalerts:\n  task_failure: critical\n")
    (c,), problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert problems == []
    assert c.business_days == () and c.business_days_text == "" and c.cutoff_time == "23:59"
    assert c.failure_severity == "critical" and c.late_severity == "error" and c.files == ()


def test_invalid_configs_are_reported_and_skipped(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY.replace("timezone: America/New_York", "timezone: Mars/Olympus"))
    custodians, problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert custodians == []
    assert any("delivery.timezone 'Mars/Olympus' is not a known IANA timezone" in p.message for p in problems)


def test_duplicate_patterns_in_one_config_are_rejected(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY.replace("pershing/GCUS_%_TRN_%.dat", "pershing/GCUS_%_POS_%.dat"))
    _, problems = custodians_from_configs([tmp_path], root=tmp_path)
    assert any("delivery.files[1].pattern 'pershing/GCUS_%_POS_%.dat' is listed more than once" in p.message for p in problems)


def test_sync_statements_are_one_transaction_that_upserts_disables_and_replaces_files(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY)
    custodians, _ = custodians_from_configs([tmp_path], root=tmp_path)
    statements = sync_statements(custodians, Target("qa"), clock=lambda: NOW)

    assert statements[0] == "BEGIN TRANSACTION" and statements[-1] == "COMMIT"
    merge = statements[1]
    assert merge.startswith('MERGE INTO "ASTRA_QA"."CONTROL"."CUSTODIANS" t')
    assert "('pershing', 'Pershing', '06:00', 'America/New_York', 'MON,TUE,WED,THU,FRI', 'critical', 'warning', 'pershing/pershing_position.yaml')" in merge
    assert "WHEN MATCHED THEN UPDATE SET" in merge and "ENABLED = TRUE" in merge and "'2026-09-06 09:30:00'::TIMESTAMP_NTZ" in merge
    assert statements[2] == 'UPDATE "ASTRA_QA"."CONTROL"."CUSTODIANS" SET ENABLED = FALSE, UPDATED_AT = \'2026-09-06 09:30:00\'::TIMESTAMP_NTZ WHERE ENABLED AND CUSTODIAN_ID NOT IN (\'pershing\')'
    assert statements[3] == 'DELETE FROM "ASTRA_QA"."CONTROL"."CUSTODIAN_FILES"'
    assert statements[4] == (
        'INSERT INTO "ASTRA_QA"."CONTROL"."CUSTODIAN_FILES" (CUSTODIAN_ID, FILE_PATTERN, DESCRIPTION) VALUES '
        "('pershing', 'pershing/GCUS_%_POS_%.dat', 'positions'), ('pershing', 'pershing/GCUS_%_TRN_%.dat', '')"
    )


def test_sync_with_no_custodians_disables_everything():
    statements = sync_statements([], Target("dev"), clock=lambda: NOW)
    assert statements[1].startswith('UPDATE "ASTRA_DEV"."CONTROL"."CUSTODIANS" SET ENABLED = FALSE') and statements[1].endswith("WHERE ENABLED")
    assert statements[2] == 'DELETE FROM "ASTRA_DEV"."CONTROL"."CUSTODIAN_FILES"'
    assert len(statements) == 4


def test_sync_executes_the_transaction_as_one_script(tmp_path):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY)
    custodians, _ = custodians_from_configs([tmp_path], root=tmp_path)
    executor = FakeExecutor()
    assert sync(executor, custodians, Target("dev")) == 1
    (script,) = executor.scripts
    assert script.startswith("BEGIN TRANSACTION;\n") and script.rstrip().endswith("COMMIT;")


def test_cli_render_prints_the_sql_and_fails_on_problems(tmp_path, capsys):
    write_config(tmp_path, "pershing", "pershing_position", DELIVERY)
    assert main(["--root", str(tmp_path), "custodians", "render", "--environment", "dev", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "custodian pershing: cutoff 06:00 America/New_York on MON,TUE,WED,THU,FRI; 2 expected file(s); late=critical, task_failure=warning" in out
    assert 'MERGE INTO "ASTRA_DEV"."CONTROL"."CUSTODIANS"' in out

    write_config(tmp_path, "pershing", "pershing_price", DELIVERY.replace('cutoff_time: "06:00"', 'cutoff_time: "09:00"'))
    assert main(["--root", str(tmp_path), "--format", "github", "custodians", "render", "--environment", "dev", str(tmp_path)]) == 1
    assert "::error file=pershing/pershing_price.yaml,title=Config validation::delivery for custodian 'pershing'" in capsys.readouterr().out
