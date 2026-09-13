from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_knowledge.registry import load_spec_file
from astra_agents.dq_generator import (
    DEFAULT_SEVERITY,
    DqGeneratorError,
    DqRule,
    Targets,
    generate,
    generate_control_total_rules,
    generate_date_rules,
    generate_key_rules,
    generate_pairing_rules,
    generate_sign_field_rules,
    load_spec,
    load_targets,
    render_dq_rules_yaml,
    render_markdown,
    run,
    validate_dq_rules,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
GCUS = REPO / "specs" / "pershing_gcus" / "2017-07-25.yaml"
SPLIT = REPO / "specs" / "split_position_example" / "2026-01-01.yaml"


def _spec(path: Path):
    spec, problems = load_spec_file(path, REPO)
    assert problems == [], problems
    return spec


# ---------------------------------------------------------------- control_total


def test_control_total_generated_for_a_single_detail_record_spec():
    rules = generate_control_total_rules(_spec(GCUS))
    assert len(rules) == 1
    rule = rules[0]
    assert rule.kind == "control_total" and rule.level == "file"
    assert rule.trailer_field == "detail_count" and rule.aggregate == "count"
    assert rule.record is None  # unambiguous: only one detail record type
    assert rule.citation == {"page": 40, "line": 5}


def test_control_total_skipped_for_a_multi_detail_record_spec():
    """split_position_example's trailer count covers two detail record types (holding and
    valuation combined); this agent does not guess how to split it, so it generates nothing
    rather than a rule that would be wrong for one of the two."""
    assert generate_control_total_rules(_spec(SPLIT)) == []


def test_control_total_skipped_when_no_trailer_field_looks_like_a_count():
    from astra_knowledge.registry import Citation, Field, Record, SourceSpec
    from datetime import date

    trailer = Record(type="trailer", fields=(Field(name="record_type", citation=Citation(page=1), type="code", position=(1, 3)),))
    detail = Record(type="detail", fields=(Field(name="x", citation=Citation(page=1), type="string", position=(1, 1)),))
    spec = SourceSpec(id="x", version="v1", effective_from=date(2026, 1, 1), file_type="position", custodians=("demo",), records=(detail, trailer), document={}, file={"format": "fixed_width", "record_length": 4}, path=Path("x"))
    assert generate_control_total_rules(spec) == []


# ---------------------------------------------------------------- sign_field


def test_sign_field_generated_from_declared_codes():
    rules = generate_sign_field_rules(_spec(GCUS))
    assert len(rules) == 1
    rule = rules[0]
    assert rule.kind == "accepted_values" and rule.level == "record"
    assert rule.field == "quantity_sign"
    assert set(rule.values) == {"+", "-", " "}
    assert rule.record is None  # only one detail record type in GCUS


def test_sign_field_names_the_record_when_the_spec_has_several_detail_types():
    rules = generate_sign_field_rules(_spec(SPLIT))
    assert {r.record for r in rules} == {"holding", "valuation"}
    assert {r.field for r in rules} == {"quantity_sign", "value_sign"}


# ---------------------------------------------------------------- date


def test_date_rules_generated_for_every_date_typed_field():
    rules = generate_date_rules(_spec(GCUS))
    assert {r.field for r in rules} == {"file_date", "as_of_date"}
    as_of = next(r for r in rules if r.field == "as_of_date")
    assert as_of.kind == "condition" and as_of.condition == "AS_OF_DATE <= CURRENT_DATE()"
    assert as_of.level == "record"
    header_date = next(r for r in rules if r.field == "file_date")
    assert header_date.level == "file"  # header, not a detail record


# ---------------------------------------------------------------- key


def test_key_rule_from_merge_keys():
    rules = generate_key_rules(_spec(GCUS))
    assert len(rules) == 1
    assert rules[0].kind == "unique" and rules[0].level == "record"
    assert rules[0].fields == ("account_number", "cusip")


def test_key_rule_from_pairing_keys():
    rules = generate_key_rules(_spec(SPLIT))
    assert len(rules) == 1
    assert rules[0].level == "pair" and rules[0].record == "position"
    assert rules[0].fields == ("account_number", "cusip")


# ---------------------------------------------------------------- pairing


def test_pairing_rule_checks_a_field_only_the_second_record_contributes():
    rules = generate_pairing_rules(_spec(SPLIT))
    assert len(rules) == 1
    rule = rules[0]
    assert rule.kind == "not_null" and rule.level == "pair"
    assert rule.field == "price"  # the first non-key, non-filler field of the valuation record
    assert "holding" in rule.check and "valuation" in rule.check


def test_pairing_rules_empty_for_a_spec_with_no_pairing():
    assert generate_pairing_rules(_spec(GCUS)) == []


# ---------------------------------------------------------------- targets / severity


def test_targets_default_severity_is_error_when_none_given():
    assert Targets().for_category("date") == DEFAULT_SEVERITY == "error"


def test_load_targets_reads_a_severity_override(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text("severity: { date: warning, pairing: info }\n", encoding="utf-8")
    targets = load_targets(path)
    assert targets.for_category("date") == "warning"
    assert targets.for_category("pairing") == "info"
    assert targets.for_category("key") == "error"  # not overridden: platform default


def test_load_targets_rejects_an_unknown_category(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text("severity: { not_a_category: error }\n", encoding="utf-8")
    with pytest.raises(DqGeneratorError, match="not_a_category"):
        load_targets(path)


def test_load_targets_rejects_an_unknown_severity(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text("severity: { date: extreme }\n", encoding="utf-8")
    with pytest.raises(DqGeneratorError, match="extreme"):
        load_targets(path)


def test_load_targets_none_path_returns_the_platform_default():
    assert load_targets(None).severity == {}


def test_generate_applies_a_clients_own_target_never_a_fabricated_one():
    """The guardrail this story asks for: a client's own severity, when given, wins; nothing is
    invented when it is not."""
    targets = Targets(severity={"date": "warning"})
    draft = generate(_spec(GCUS), targets=targets)
    dates = [r for r in draft.rules if r.category == "date"]
    assert dates and all(r.severity == "warning" for r in dates)
    others = [r for r in draft.rules if r.category != "date"]
    assert all(r.severity == "error" for r in others)


# ---------------------------------------------------------------- validation


def test_validate_dq_rules_passes_for_generated_rules():
    spec = _spec(GCUS)
    draft = generate(spec)
    assert draft.valid is True, [p.format() for p in draft.problems]


def test_validate_dq_rules_catches_a_malformed_rule_independently():
    """Proves the re-validation is real, not just internal bookkeeping: an invalid kind is
    caught even though this agent's own code would never normally produce one."""
    spec = _spec(GCUS)
    bad = DqRule(category="key", id="bad", kind="not_a_real_kind", level="record", check="x")
    problems = validate_dq_rules((bad,), spec)
    assert problems and "not_a_real_kind" in problems[0].message


# ---------------------------------------------------------------- generate() / run()


def test_generate_produces_all_five_categories_of_rule_structure_for_gcus():
    draft = generate(_spec(GCUS))
    by_category = draft.by_category
    assert len(by_category["control_total"]) == 1
    assert len(by_category["sign_field"]) == 1
    assert len(by_category["date"]) == 2
    assert len(by_category["key"]) == 1
    assert len(by_category["pairing"]) == 0  # GCUS has no pairing block; proven separately against split_position_example
    assert draft.valid is True


def test_run_reads_the_spec_from_disk():
    draft = run(GCUS)
    assert draft.spec_id == "pershing_gcus" and len(draft.rules) == 5


def test_run_raises_a_clear_error_for_a_missing_spec(tmp_path):
    with pytest.raises(DqGeneratorError, match="not found"):
        run(tmp_path / "missing.yaml")


def test_load_spec_reads_the_real_registry_spec():
    assert load_spec(GCUS).id == "pershing_gcus"


# ---------------------------------------------------------------- report and files


def test_render_markdown_lists_every_category():
    text = render_markdown(generate(_spec(GCUS)))
    assert "## control_total (1)" in text
    assert "## pairing (0)" in text
    assert "None generated." in text


def test_render_dq_rules_yaml_is_pasteable_config_shape():
    text = render_dq_rules_yaml(generate(_spec(GCUS)))
    assert text.startswith("# ") and "dq_rules:" in text
    assert "kind: control_total" in text
    assert "trailer_field: detail_count" in text


def test_write_draft_writes_report_json_and_dq_rules_yaml(tmp_path):
    draft = generate(_spec(GCUS))
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    assert (tmp_path / "out" / "dq_rules.yaml").exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["valid"] is True and len(data["rules"]) == 5


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_real_gcus_spec(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["dq-generator", "run", "--spec", str(GCUS), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing_gcus" / "2017-07-25" / "report.md").exists()
    text = capsys.readouterr().out
    assert "5 rule(s)" in text and "control_total: 1" in text and "pairing: 0" in text


def test_cli_run_with_a_targets_file(tmp_path, capsys):
    import astra_agents.cli as cli

    targets_path = tmp_path / "targets.yaml"
    targets_path.write_text("severity: { key: warning }\n", encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["dq-generator", "run", "--spec", str(GCUS), "--targets", str(targets_path), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    data = json.loads((out / "pershing_gcus" / "2017-07-25" / "report.json").read_text(encoding="utf-8"))
    key_rule = next(r for r in data["rules"] if r["category"] == "key")
    assert key_rule["severity"] == "warning"


def test_cli_reports_a_start_error_for_a_missing_spec(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["dq-generator", "run", "--spec", str(tmp_path / "missing.yaml"), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S5.6.1: control-total, sign-field, date and key rules are generated for GCUS; pairing
    rules are generated too, proven against split_position_example since GCUS itself has no
    pairing block to exercise that category against. Severity defaults to error and only changes
    when a client's own target says so -- never a fabricated number."""
    gcus = generate(_spec(GCUS))
    assert len(gcus.by_category["control_total"]) == 1
    assert len(gcus.by_category["sign_field"]) == 1
    assert len(gcus.by_category["date"]) == 2
    assert len(gcus.by_category["key"]) == 1

    split = generate(_spec(SPLIT))
    assert len(split.by_category["pairing"]) == 1

    assert all(r.severity == "error" for r in gcus.rules)  # platform default, not invented
    overridden = generate(_spec(GCUS), targets=Targets(severity={"sign_field": "warning"}))
    assert next(r for r in overridden.rules if r.category == "sign_field").severity == "warning"
