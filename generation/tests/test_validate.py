from pathlib import Path

import pytest

from astra_data.validate import Problem, validate_config_file, validate_paths

EXAMPLE = Path(__file__).resolve().parents[2] / "configs" / "examples" / "pershing_position.yaml"

VALID = EXAMPLE.read_text(encoding="utf-8")


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def problems_for(tmp_path: Path, text: str, name: str = "pershing_position.yaml") -> list[Problem]:
    return validate_config_file(write(tmp_path, name, text), root=tmp_path)


def test_the_example_config_is_valid():
    assert validate_config_file(EXAMPLE, root=EXAMPLE.parents[2]) == []


def test_missing_required_field_names_it_with_the_parent_line(tmp_path):
    text = VALID.replace("owner:\n  name: Data steward, custodial\n  email: steward@example.com\n", "")
    problems = problems_for(tmp_path, text)
    assert [p.message for p in problems] == ["top level: missing required field owner"]
    assert problems[0].path == "pershing_position.yaml"
    # The top-level mapping starts where its first key is, after the comment block.
    assert problems[0].line == text.splitlines().index("config_version: 0") + 1


def test_unknown_field_points_at_the_offending_line(tmp_path):
    text = VALID.replace("target_profile: snowflake_iceberg", "target_profil: snowflake_iceberg\ntarget_profile: snowflake_iceberg")
    problems = problems_for(tmp_path, text)
    assert len(problems) == 1
    assert "unknown field target_profil; allowed fields are" in problems[0].message
    assert text.splitlines()[problems[0].line - 1].startswith("target_profil:")


def test_enum_violation_lists_the_allowed_values(tmp_path):
    text = VALID.replace("severity: error", "severity: maybe")
    problems = problems_for(tmp_path, text)
    assert problems == [Problem("pershing_position.yaml", problems[0].line, "dq_rules[0].severity: 'maybe' is not one of info, warning, error")]
    assert text.splitlines()[problems[0].line - 1].strip() == "severity: maybe"


def test_identifier_pattern_gets_a_plain_explanation(tmp_path):
    text = VALID.replace("custodian: pershing", "custodian: Pershing")
    problems = problems_for(tmp_path, text)
    assert problems[0].message == "source.custodian: 'Pershing' must be lower-case letters, digits and underscores, starting with a letter"


def test_dates_are_checked_for_shape_and_calendar(tmp_path):
    shape = problems_for(tmp_path, VALID.replace("effective_from: 2026-09-01", "effective_from: 1 Sep 2026"))
    assert shape[0].message == "effective_from: '1 Sep 2026' must be a date written as YYYY-MM-DD"
    calendar = problems_for(tmp_path, VALID.replace("effective_from: 2026-09-01", "effective_from: 2026-02-30"))
    assert calendar[0].message == "effective_from '2026-02-30' is not a valid calendar date"


def test_wrong_type_is_reported_in_words(tmp_path):
    text = VALID.replace("tier: medium", "tier: 2")
    problems = problems_for(tmp_path, text)
    assert problems[0].message == "source.tier: 2 is not one of simple, medium, complex"


def test_mapping_rule_must_be_listed_under_rules(tmp_path):
    unlisted = problems_for(tmp_path, VALID.replace("rule: pershing_gcus.quantity_sign", "rule: pershing_gcus.quantity_flip"))
    assert unlisted[0].message == "mappings[1].rule 'pershing_gcus.quantity_flip' is not listed under rules; a config lists every rule it uses"
    shape = problems_for(tmp_path, VALID.replace("  - pershing_gcus.quantity_sign\n", "  - quantity_sign\n").replace("rule: pershing_gcus.quantity_sign", "rule: quantity_sign"))
    assert [p.message for p in shape] == [
        "mappings[1].rule: 'quantity_sign' must be a rule catalog id written as <group>.<name> (rules/<group>/<name>.yaml)",
        "rules[0]: 'quantity_sign' must be a rule catalog id written as <group>.<name> (rules/<group>/<name>.yaml)",
    ]


def test_a_config_may_not_reference_an_unknown_or_rejected_catalog_rule(tmp_path):
    import shutil
    from datetime import datetime, timezone

    from astra_knowledge.rules import Catalog, set_status

    rules_dir = tmp_path / "rules"
    shutil.copytree(EXAMPLE.parents[2] / "rules", rules_dir)
    catalog, problems = Catalog.load(rules_dir, tmp_path)
    assert problems == []
    assert problems_for_catalog(tmp_path, VALID, catalog) == []

    unknown = problems_for_catalog(tmp_path, VALID.replace("  - pershing_gcus.quantity_sign\n", "  - pershing_gcus.quantity_sign\n  - pershing_gcus.nowhere\n"), catalog)
    assert [p.message for p in unknown] == ["rules[1] 'pershing_gcus.nowhere' is not in the rule catalog (rules/<group>/<name>.yaml)"]

    set_status(catalog.get("pershing_gcus.quantity_sign"), "rejected", "steward@example.com", "Wrong.", at=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc))
    catalog, _ = Catalog.load(rules_dir, tmp_path)
    rejected = problems_for_catalog(tmp_path, VALID, catalog)
    assert [p.message for p in rejected] == ["rules[0] 'pershing_gcus.quantity_sign' was rejected by steward@example.com on 2026-09-08; a config may not use a rejected rule"]
    assert VALID.splitlines()[rejected[0].line - 1].strip() == "- pershing_gcus.quantity_sign"


def problems_for_catalog(tmp_path: Path, text: str, catalog) -> list[Problem]:
    return validate_config_file(write(tmp_path, "pershing_position.yaml", text), root=tmp_path, catalog=catalog)


def test_duplicate_ids_and_targets_are_rejected(tmp_path):
    dup_rule = VALID.replace("  - pershing_gcus.quantity_sign\n", "  - pershing_gcus.quantity_sign\n  - pershing_gcus.quantity_sign\n")
    problems = problems_for(tmp_path, dup_rule)
    assert any("rules[1] 'pershing_gcus.quantity_sign' is listed more than once" in p.message for p in problems)

    dup_target = VALID.replace("  - target: position.quantity", "  - target: position.account_number\n    source: X\n  - target: position.quantity")
    problems = problems_for(tmp_path, dup_target)
    assert any("mappings[1].target 'position.account_number' is mapped more than once" in p.message for p in problems)


def test_file_name_must_match_source_id(tmp_path):
    problems = problems_for(tmp_path, VALID, name="pershing_positions.yaml")
    assert problems[0].message == "source.id 'pershing_position' must match the file name; rename the file to pershing_position.yaml or change the id"


def test_duplicate_yaml_keys_are_an_error_not_a_silent_override(tmp_path):
    text = VALID.replace("owner:\n", "domain_pack: insurance\nowner:\n")
    problems = problems_for(tmp_path, text)
    assert problems[0].message.startswith("invalid YAML: duplicate key 'domain_pack'")
    assert problems[0].line == text.splitlines().index("domain_pack: insurance") + 1


def test_syntax_error_reports_the_line(tmp_path):
    text = VALID.replace("  email: steward@example.com", "  email: steward@example.com\n   badly: indented")
    problems = problems_for(tmp_path, text)
    assert problems[0].message.startswith("invalid YAML:")
    assert problems[0].line is not None


def test_unknown_config_version_is_the_only_message(tmp_path):
    problems = problems_for(tmp_path, VALID.replace("config_version: 0", "config_version: 7"))
    assert problems == [Problem("pershing_position.yaml", 4, "config_version must be one of 0; found 7")]


def test_validate_paths_walks_directories_and_reports_missing_paths(tmp_path):
    (tmp_path / "pershing").mkdir()
    write(tmp_path / "pershing", "pershing_position.yaml", VALID)
    write(tmp_path / "pershing", "_draft.yaml", "not: validated")
    count, problems = validate_paths([tmp_path, tmp_path / "nowhere"], root=tmp_path)
    assert count == 1
    assert [p.message for p in problems] == ["no such file or directory"]


def test_github_format_is_a_workflow_annotation():
    p = Problem("configs/x.yaml", 12, "rules[0].status: 'maybe' is not one of a, b")
    assert p.format("github", title="Config validation") == "::error file=configs/x.yaml,line=12,title=Config validation::rules[0].status: 'maybe' is not one of a, b"
    assert Problem("configs/x.yaml", None, "gone").format("github", title="Config validation") == "::error file=configs/x.yaml,title=Config validation::gone"
    assert p.format("text") == "configs/x.yaml:12: rules[0].status: 'maybe' is not one of a, b"


@pytest.mark.parametrize("text", ["- a list\n- at top level\n", "just a string\n"])
def test_non_mapping_documents_are_rejected(tmp_path, text):
    problems = problems_for(tmp_path, text)
    assert problems[0].message == "the file must contain a mapping (key: value pairs) at the top level"
