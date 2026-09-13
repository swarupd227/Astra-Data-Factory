from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_core.yamlsource import load
from astra_knowledge.registry import load_spec_file
from astra_agents.test_generator import (
    TestGeneratorError,
    default_value,
    generate,
    load_config,
    load_spec,
    render_markdown,
    render_record,
    render_synthetic_file,
    run,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
GCUS = REPO / "specs" / "pershing_gcus" / "2017-07-25.yaml"
REAL_CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"


def _spec():
    spec, problems = load_spec_file(GCUS, REPO)
    assert problems == [], problems
    return spec


ALL_KINDS_CONFIG_TEXT = """
config_version: 0
source: { id: dqgen_test, custodian: pershing, file_type: position, tier: medium }
spec: { id: pershing_gcus, version: "2017-07-25" }
target_profile: snowflake_iceberg
domain_pack: custodial
effective_from: 2026-01-01
owner: { name: Data steward, email: steward@example.com }
dq_rules:
  - id: detail_control_total
    kind: control_total
    level: file
    check: 'trailer detail_count equals the count of detail rows'
    trailer_field: detail_count
    aggregate: count
    severity: error
  - id: quantity_sign_accepted_values
    kind: accepted_values
    level: record
    check: 'quantity_sign is one of the declared codes'
    field: quantity_sign
    values: ["+", "-", " "]
    severity: error
  - id: header_file_date_not_future
    kind: condition
    level: file
    check: 'file_date is never in the future'
    field: file_date
    condition: 'FILE_DATE <= CURRENT_DATE()'
    severity: error
  - id: as_of_date_not_future
    kind: condition
    level: record
    check: 'as_of_date is never in the future'
    field: as_of_date
    condition: 'AS_OF_DATE <= CURRENT_DATE()'
    severity: error
  - id: key_unique
    kind: unique
    level: record
    check: '(account_number, cusip) identifies one row'
    fields: [account_number, cusip]
    severity: error
  - id: unrecognized_condition
    kind: condition
    level: record
    check: 'a condition this agent does not know how to synthesize'
    condition: 'SOME_ARBITRARY_EXPRESSION(X, Y) = 1'
    severity: warning
"""


def _all_kinds_config(tmp_path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(ALL_KINDS_CONFIG_TEXT, encoding="utf-8")
    return path


# ---------------------------------------------------------------- default_value / render_record


def test_default_value_is_exactly_field_length_for_every_field():
    spec = _spec()
    for record in spec.records:
        for f in record.fields:
            assert len(default_value(f)) == f.length, f.name


def test_default_value_uses_the_first_declared_code_for_a_code_field():
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    record_type = detail.field("record_type")
    assert default_value(record_type).strip() == "DTL"  # the record's own match marker


def test_default_value_for_a_date_field_is_a_fixed_synthetic_date():
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    as_of = detail.field("as_of_date")
    assert default_value(as_of) == "20260101"


def test_default_value_for_an_unsigned_numeric_field_is_all_zero_digits():
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    quantity = detail.field("quantity")
    assert default_value(quantity) == "0" * quantity.length


def test_render_record_produces_a_line_of_exactly_the_record_length():
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    line = render_record(detail)
    assert len(line) == spec.record_length
    assert line.startswith("DTL")


def test_render_record_applies_an_override_and_nothing_else_changes():
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    baseline = render_record(detail)
    overridden = render_record(detail, {"cusip": " " * 9})
    assert overridden != baseline
    # every other field's slice is unchanged
    for f in detail.fields:
        if f.name == "cusip":
            continue
        start, end = f.start - 1, f.start - 1 + f.length
        assert overridden[start:end] == baseline[start:end]


# ---------------------------------------------------------------- render_synthetic_file


def test_render_synthetic_file_every_line_is_the_record_length():
    spec = _spec()
    text = render_synthetic_file(spec)
    lines = text.splitlines()
    assert len(lines) == 3  # header, one detail, trailer
    assert all(len(line) == spec.record_length for line in lines)


def test_render_synthetic_file_trailer_count_matches_the_real_detail_count_by_default():
    spec = _spec()
    text = render_synthetic_file(spec, detail_lines=[render_record(next(r for r in spec.records if r.type == "detail"))] * 3)
    trailer_line = text.splitlines()[-1]
    assert trailer_line[3:12] == "000000003"


def test_render_synthetic_file_header_overrides_apply_only_to_the_header_line():
    spec = _spec()
    text = render_synthetic_file(spec, header_overrides={"file_date": "20991231"})
    header_line, detail_line, _ = text.splitlines()
    assert header_line[3:11] == "20991231"
    assert "99991231" not in detail_line  # untouched


def test_render_synthetic_file_never_reads_any_other_file(monkeypatch):
    """"Contains no real records" is architectural: this proves the renderer opens nothing at
    all beyond what is already in memory (the parsed spec)."""
    import builtins

    spec = _spec()
    original_open = builtins.open

    def _guarded_open(*args, **kwargs):
        raise AssertionError("render_synthetic_file must not open any file")

    monkeypatch.setattr(builtins, "open", _guarded_open)
    try:
        render_synthetic_file(spec)
    finally:
        monkeypatch.setattr(builtins, "open", original_open)


# ---------------------------------------------------------------- generate() branch kinds


def test_control_total_branch_produces_a_genuine_mismatch(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "detail_control_total")
    lines = case.file_content.splitlines()
    detail_count = sum(1 for line in lines if line.startswith("DTL"))
    trailer_count = int(lines[-1][3:12])
    assert detail_count != trailer_count


def test_accepted_values_branch_uses_a_code_outside_the_declared_set(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "quantity_sign_accepted_values")
    detail_line = next(line for line in case.file_content.splitlines() if line.startswith("DTL"))
    sign = detail_line[40:41]  # quantity_sign is position 41, length 1
    assert sign not in ("+", "-", " ")


def test_unique_branch_produces_two_identical_key_lines(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "key_unique")
    details = [line for line in case.file_content.splitlines() if line.startswith("DTL")]
    assert len(details) == 2 and details[0] == details[1]


def test_condition_future_date_branch_at_file_level_edits_the_header(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "header_file_date_not_future")
    header_line = case.file_content.splitlines()[0]
    assert header_line[3:11] == "20991231"


def test_condition_future_date_branch_at_record_level_edits_the_detail(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "as_of_date_not_future")
    detail_line = next(line for line in case.file_content.splitlines() if line.startswith("DTL"))
    assert detail_line[56:64] == "20991231"  # as_of_date is position 57, length 8


def test_unrecognized_condition_is_not_covered(tmp_path):
    """This agent cannot parse arbitrary SQL; a condition it does not recognize is reported as
    not covered rather than guessed at."""
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    assert "unrecognized_condition" in draft.uncovered_rule_ids
    assert draft.ok is False


def test_range_below_min_of_zero_on_an_unsigned_field_has_no_file_but_still_covered():
    """price_not_negative (min: 0) cannot be represented as a negative raw digit string in an
    unsigned picture; the SQL assertion still covers it, but there is no edge file to render."""
    config = load(REAL_CONFIG.read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    case = next(c for c in draft.cases if c.rule_id == "price_not_negative")
    assert case.has_file is False
    assert case.sql and "WHERE" in case.sql
    assert "price_not_negative" in draft.covered_rule_ids


# ---------------------------------------------------------------- coverage / ok


def test_coverage_is_100_percent_for_the_real_committed_config():
    config = load(REAL_CONFIG.read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    assert draft.coverage == 1.0
    assert draft.ok is True
    assert len(draft.rule_ids) == 3


def test_coverage_reflects_an_uncovered_kind_honestly(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    draft = generate(config, _spec())
    assert draft.coverage == pytest.approx(5 / 6)
    assert draft.ok is False


# ---------------------------------------------------------------- loaders / run()


def test_load_config_reads_the_real_committed_config():
    config = load_config(REAL_CONFIG)
    assert config["source"]["id"] == "pershing_position"


def test_load_config_rejects_an_invalid_config(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("config_version: 0\nsource: { id: x }\n", encoding="utf-8")
    with pytest.raises(TestGeneratorError):
        load_config(path)


def test_load_config_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(TestGeneratorError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_load_spec_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(TestGeneratorError, match="not found"):
        load_spec(tmp_path / "missing.yaml")


def test_run_reads_both_files_from_disk():
    draft = run(REAL_CONFIG, GCUS)
    assert draft.config_id == "pershing_position" and draft.spec_id == "pershing_gcus"


# ---------------------------------------------------------------- report and files


def test_render_markdown_reports_coverage_and_uncovered_rules(tmp_path):
    config = load(_all_kinds_config(tmp_path).read_text(encoding="utf-8"))
    text = render_markdown(generate(config, _spec()))
    assert "5 covered by at least one generated branch (83%)" in text
    assert "## Not covered" in text and "unrecognized_condition" in text


def test_write_draft_writes_sql_and_dat_files_in_the_backlogs_own_layout(tmp_path):
    draft = run(REAL_CONFIG, GCUS)
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    assert (tmp_path / "out" / "tests" / "unit" / "trailer_control_total_mismatch.sql").exists()
    assert (tmp_path / "out" / "tests" / "edge" / "trailer_control_total_mismatch.dat").exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["coverage"] == 1.0


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_real_config_and_spec(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["test-generator", "run", "--config", str(REAL_CONFIG), "--spec", str(GCUS), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing_position" / "report.md").exists()
    assert "3 covered (100%)" in capsys.readouterr().out


def test_cli_exits_nonzero_when_a_rule_is_not_covered(tmp_path):
    import astra_agents.cli as cli

    code = cli.main(["test-generator", "run", "--config", str(_all_kinds_config(tmp_path)), "--spec", str(GCUS), "--out", str(tmp_path / "out")])
    assert code == 1


def test_cli_reports_a_start_error_for_a_missing_config(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["test-generator", "run", "--config", str(tmp_path / "missing.yaml"), "--spec", str(GCUS), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S5.7.1: branch coverage of the real committed config's rules is 100% (every dq_rule this
    agent knows how to synthesize a branch for is covered, honestly reported when it cannot be),
    and every synthetic file it writes contains nothing but this agent's own fixed, programmatic
    vocabulary of fake values -- never anything read from a real sample or reference data."""
    draft = run(REAL_CONFIG, GCUS)
    assert draft.coverage == 1.0

    for case in draft.cases:
        if not case.has_file:
            continue
        for line in case.file_content.splitlines():
            assert "SYNTH" in line or line.startswith(("HDR", "TRL")) or set(line.strip()) <= set("0123456789 ")
