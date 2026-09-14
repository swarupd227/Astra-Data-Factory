from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_control.diff_review import (
    DiffReviewError,
    citation_link,
    review,
    render_markdown,
    write_review,
)
from astra_knowledge.rules import Citation

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"
EXAMPLE = REPO / "control" / "examples" / "diff_review"
OLD_PERSHING = EXAMPLE / "before" / "pershing_position.yaml"
NEW_PERSHING = REPO / "configs" / "examples" / "pershing_position.yaml"
FIDELITY = EXAMPLE / "fidelity_position.yaml"


def _review(other_configs=(FIDELITY,)):
    return review(OLD_PERSHING, NEW_PERSHING, other_configs=other_configs, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- citation_link


def test_citation_link_for_a_spec_citation():
    c = Citation(kind="spec", spec_id="pershing_gcus", spec_version="2017-07-25", page=13, line=6)
    assert citation_link(c) == "specs/pershing_gcus/2017-07-25.yaml#page=13"


def test_citation_link_for_a_spec_citation_with_no_page():
    c = Citation(kind="spec", spec_id="pershing_gcus", spec_version="2017-07-25")
    assert citation_link(c) == "specs/pershing_gcus/2017-07-25.yaml"


def test_citation_link_for_a_code_citation():
    c = Citation(kind="code", file="Splitter.java", line=142)
    assert citation_link(c) == "Splitter.java:142"


def test_citation_link_for_a_code_citation_in_a_named_repository():
    c = Citation(kind="code", file="Splitter.java", line=142, repository="envestnet-loader")
    assert citation_link(c) == "envestnet-loader/Splitter.java:142"


def test_citation_link_for_a_document_citation():
    c = Citation(kind="document", document="loader-spec.pdf", page=4)
    assert citation_link(c) == "loader-spec.pdf#page=4"


# ---------------------------------------------------------------- review(): loading and errors


def test_review_rejects_a_config_that_does_not_compile(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("config_version: 0\n", encoding="utf-8")
    with pytest.raises(DiffReviewError):
        review(bad, NEW_PERSHING, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


def test_review_rejects_a_bad_specs_dir(tmp_path):
    with pytest.raises(DiffReviewError):
        review(OLD_PERSHING, NEW_PERSHING, specs_dir=tmp_path, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- AC1: changed fields and rules


def test_review_finds_the_real_quantity_sign_rule_newly_referenced():
    result = _review()
    assert result.changed is True
    rule_field = next(f for f in result.field_diffs if f.group == "rule:pershing_gcus.quantity_sign")
    assert rule_field.kind == "added"


def test_review_also_reports_the_mapping_that_gained_the_rule():
    result = _review()
    mapping_field = next(f for f in result.field_diffs if f.column == "QUANTITY")
    assert mapping_field.kind == "changed"


def test_identical_configs_have_no_differences():
    result = review(NEW_PERSHING, NEW_PERSHING, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert result.changed is False
    assert result.field_diffs == ()
    assert result.rule_impacts == ()


# ---------------------------------------------------------------- rule impact: precedence, citation, real data


def test_rule_impact_uses_the_dedicated_rule_group_kind_not_the_mapping_ones():
    """The dedicated 'rule:<id>' entry (kind=added) is authoritative over the mapping-level entry
    (kind=changed) for the SAME rule id -- proven directly against the real diff, not a synthetic
    one, since this is exactly the case the real quantity_sign example produces."""
    result = _review()
    impact = next(r for r in result.rule_impacts if r.rule_id == "pershing_gcus.quantity_sign")
    assert impact.change_kind == "added"


def test_rule_impact_carries_the_real_citation():
    result = _review()
    impact = next(r for r in result.rule_impacts if r.rule_id == "pershing_gcus.quantity_sign")
    assert impact.citation is not None
    assert impact.citation.kind == "spec"
    assert impact.citation_link == "specs/pershing_gcus/2017-07-25.yaml#page=13"


def test_affected_custodians_includes_both_pershing_and_the_illustrative_fidelity():
    result = _review()
    impact = next(r for r in result.rule_impacts if r.rule_id == "pershing_gcus.quantity_sign")
    assert impact.affected_custodians == ("fidelity", "pershing")


def test_affected_custodians_is_only_pershing_without_the_other_config():
    result = _review(other_configs=())
    impact = next(r for r in result.rule_impacts if r.rule_id == "pershing_gcus.quantity_sign")
    assert impact.affected_custodians == ("pershing",)


# ---------------------------------------------------------------- report / files


def test_render_markdown_includes_citation_and_affected_custodians():
    result = _review()
    text = render_markdown(result)
    assert "specs/pershing_gcus/2017-07-25.yaml#page=13" in text
    assert "fidelity" in text and "pershing" in text


def test_render_markdown_with_no_differences_says_so():
    result = review(NEW_PERSHING, NEW_PERSHING, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert "No differences." in render_markdown(result)


def test_write_review_writes_report_and_json(tmp_path):
    result = _review()
    report_path, data_path = write_review(result, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["changed"] is True
    assert data["rule_impacts"][0]["affected_custodians"] == ["fidelity", "pershing"]


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_real_example(tmp_path, capsys):
    import astra_control.cli as cli

    out = tmp_path / "out"
    code = cli.main([
        "diff-review", "run",
        "--old", str(OLD_PERSHING), "--new", str(NEW_PERSHING),
        "--other-config", str(FIDELITY),
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
        "--out", str(out),
    ])
    assert code == 0, capsys.readouterr()
    text = capsys.readouterr().out
    assert "quantity_sign" in text and "fidelity" in text
    assert (out / "report.md").exists()


def test_cli_run_reports_a_start_error_for_a_bad_config(tmp_path, capsys):
    import astra_control.cli as cli

    code = cli.main([
        "diff-review", "run",
        "--old", str(tmp_path / "missing.yaml"), "--new", str(NEW_PERSHING),
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_run_json_output(tmp_path, capsys):
    import astra_control.cli as cli

    out = tmp_path / "out"
    code = cli.main([
        "diff-review", "run",
        "--old", str(OLD_PERSHING), "--new", str(NEW_PERSHING),
        "--other-config", str(FIDELITY),
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
        "--out", str(out), "--json",
    ])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["changed"] is True


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S6.1.3: the diff shows changed fields, changed rules and affected custodians (AC1), and a
    citation link that opens the real spec page (AC2)."""
    result = _review()

    assert any(f.kind in ("added", "changed") for f in result.field_diffs)  # changed fields
    assert any(f.group.startswith("rule:") for f in result.field_diffs)  # rules

    impact = result.rule_impacts[0]
    assert impact.affected_custodians  # affected custodians, non-empty

    assert impact.citation_link is not None
    assert impact.citation_link.startswith("specs/") and "#page=" in impact.citation_link  # opens the spec page
