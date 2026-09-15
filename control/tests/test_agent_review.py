from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.rules import Citation, HistoryEntry, Owner, Rule, render_rule

from astra_control.agent_review import (
    AgentReviewError,
    RULE_RECOVERY_AGENT,
    accept,
    edit_citation,
    edit_text,
    load_draft_rule,
    reject,
    render_diff_markdown,
    render_markdown,
    rule_recovery_item,
)

REPO = Path(__file__).resolve().parents[2]
JAVA = REPO / "agents" / "examples" / "rule_recovery" / "java" / "Splitter.java"
EVAL_EXAMPLE = REPO / "agents" / "pattern_matcher" / "eval.yaml"  # a real committed gold set, used as a private-copy write target
OWNER = Owner("Data steward, custodial", "steward@example.com")


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _draft_rule(rule_id: str = "pershing_loader.unknown_record_type_rejected") -> Rule:
    """A draft Rule Recovery would produce from the real, committed Splitter.java: lines 33-47
    are its real switch statement's default branch, which rejects an unrecognized record type
    (module docstring's own citation, grounded in the actual file, not invented)."""
    group, name = rule_id.split(".")
    return Rule(
        id=rule_id,
        text="A line whose first three characters are not HDR, DTL or TRL is rejected and never reaches the Loader.",
        class_="ingestion",
        owner=OWNER,
        status="recovered",
        citation=Citation(kind="code", file="Splitter.java", line=33, end_line=47, repository="pershing-legacy"),
        history=(HistoryEntry("recovered", "rule-recovery", _at("2026-09-15T09:00:00Z"), "Recovered from the Splitter's default switch branch."),),
        path=Path("unset"),  # set by the caller once written to a real file
        custodians=("pershing",),
        tags=("rejection-l001",),
    )


def _write_draft(tmp_path: Path, rule: Rule | None = None) -> Path:
    rule = rule or _draft_rule()
    group, name = rule.id.split(".")
    group_dir = tmp_path / group
    group_dir.mkdir(parents=True, exist_ok=True)
    path = group_dir / f"{name}.yaml"
    path.write_text(render_rule(rule), encoding="utf-8", newline="\n")
    return path


def _copy_eval(tmp_path: Path, *, agent: str | None = None) -> Path:
    """A private copy of the real, committed pattern_matcher gold set -- its real thresholds,
    never invented for this test. `agent` overrides the `agent:` field for a test that needs a
    gold set genuinely labeled for rule_recovery (no real one is committed yet); the refusal
    tests use the file's own real 'pattern_matcher' label unmodified."""
    dest = tmp_path / "eval.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = EVAL_EXAMPLE.read_text(encoding="utf-8")
    if agent:
        text = text.replace("agent: pattern_matcher", f"agent: {agent}")
    dest.write_text(text, encoding="utf-8")
    return dest


def _item(tier: str = "medium", case_input: str = "agents/examples/rule_recovery/java/Splitter.java:33-47") -> "object":
    rule = _draft_rule()
    return rule_recovery_item(rule, case_input=case_input, tier=tier)


# ---------------------------------------------------------------- loading a draft file


def test_load_draft_rule_reads_a_real_rule_shaped_file(tmp_path):
    path = _write_draft(tmp_path)
    rule = load_draft_rule(path, tmp_path)
    assert rule.id == "pershing_loader.unknown_record_type_rejected"
    assert rule.status == "recovered"
    assert rule.citation.kind == "code"


def test_load_draft_rule_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(AgentReviewError):
        load_draft_rule(tmp_path / "pershing_loader" / "missing.yaml", tmp_path)


def test_load_draft_rule_id_must_match_its_place_in_the_directory(tmp_path):
    rule = _draft_rule()
    bad_dir = tmp_path / "wrong_group"
    bad_dir.mkdir()
    path = bad_dir / "unknown_record_type_rejected.yaml"
    path.write_text(render_rule(rule), encoding="utf-8", newline="\n")
    with pytest.raises(AgentReviewError, match="must match"):
        load_draft_rule(path, tmp_path)


# ---------------------------------------------------------------- AC1: a draft beside its source evidence


def test_rule_recovery_item_carries_text_citation_and_expected():
    rule = _draft_rule()
    item = rule_recovery_item(rule, case_input="Splitter.java:33-47", tier="medium")
    assert item.agent == RULE_RECOVERY_AGENT
    assert "rejected" in item.draft_text
    assert item.citation_link == "pershing-legacy/Splitter.java:33"
    assert "Splitter.java:33" in item.citation_text
    assert item.expected == ("rule:pershing_loader.unknown_record_type_rejected",)


def test_rule_recovery_item_rejects_an_unknown_tier():
    rule = _draft_rule()
    with pytest.raises(AgentReviewError, match="not a tier"):
        rule_recovery_item(rule, case_input="x", tier="extreme")


def test_rule_recovery_item_round_trips_through_a_real_written_draft_file(tmp_path):
    path = _write_draft(tmp_path)
    rule = load_draft_rule(path, tmp_path)
    item = rule_recovery_item(rule, case_input="Splitter.java:33-47", tier="medium")
    data = json.loads(json.dumps(item.to_dict()))
    assert data["expected"] == ["rule:pershing_loader.unknown_record_type_rejected"]


def test_render_markdown_shows_draft_evidence_and_what_accepting_would_record():
    text = render_markdown(_item())
    assert "rejected" in text  # the draft's own text
    assert "Splitter.java:33" in text  # the citation
    assert "rule:pershing_loader.unknown_record_type_rejected" in text  # AC3's own preview


# ---------------------------------------------------------------- AC2: edit keeps the original for comparison


def test_edit_text_keeps_the_original_and_shows_the_diff():
    original = _item()
    edited = edit_text(original, "A line whose record type code is not HDR, DTL or TRL is rejected before the Loader ever sees it.")

    assert edited.original is original
    assert edited.edited.draft_text != original.draft_text
    changes = edited.diff()
    assert len(changes) == 1
    assert changes[0].field == "draft_text"
    assert changes[0].before == original.draft_text


def test_edit_text_does_not_change_expected_ac3_scores_the_rule_id_not_the_wording():
    original = _item()
    edited = edit_text(original, "reworded text")
    assert edited.edited.expected == original.expected


def test_edit_citation_updates_citation_fields_and_diffs_them():
    original = _item()
    corrected = Citation(kind="code", file="Splitter.java", line=44, end_line=47, repository="pershing-legacy")
    edited = edit_citation(original, _draft_rule(), corrected)
    changes = edited.diff()
    assert {c.field for c in changes} == {"citation_text"}
    assert "44" in edited.edited.citation_text


def test_diff_with_no_changes_is_empty():
    original = _item()
    same = edit_text(original, original.draft_text)
    assert same.diff() == ()


def test_render_diff_markdown_with_no_changes_says_so():
    original = _item()
    same = edit_text(original, original.draft_text)
    assert "No changes." in render_diff_markdown(same)


def test_render_diff_markdown_shows_before_and_after():
    original = _item()
    edited = edit_text(original, "a corrected reading")
    text = render_diff_markdown(edited)
    assert "draft_text" in text
    assert "a corrected reading" in text
    assert original.draft_text in text


# ---------------------------------------------------------------- AC3: accept / reject feeds the eval set


def test_accept_appends_a_case_recording_the_draft_s_own_expected(tmp_path):
    gold_set = _copy_eval(tmp_path, agent="rule_recovery")
    item = _item(tier="medium")

    case = accept(gold_set, item, case_id="unknown_record_type_review", root=tmp_path)

    assert case.tier == "medium"
    assert case.input == item.case_input
    assert case.expected == frozenset({"rule:pershing_loader.unknown_record_type_rejected"})


def test_accept_records_the_edited_draft_s_own_expected_not_the_original(tmp_path):
    """Accepting after an edit accepts the EDITED item -- if the edit changed expected (a
    different rule id, say), that is what gets recorded, not the original draft's."""
    gold_set = _copy_eval(tmp_path, agent="rule_recovery")
    original = _item(tier="medium")
    from dataclasses import replace as dc_replace

    edited_item = dc_replace(original, expected=("rule:pershing_loader.corrected_id",))

    case = accept(gold_set, edited_item, case_id="corrected_review", root=tmp_path)
    assert case.expected == frozenset({"rule:pershing_loader.corrected_id"})


def test_reject_appends_a_case_with_empty_expected(tmp_path):
    gold_set = _copy_eval(tmp_path, agent="rule_recovery")
    item = _item(tier="medium")

    case = reject(gold_set, item, case_id="unknown_record_type_wrong", root=tmp_path)

    assert case.expected == frozenset()
    assert case.input == item.case_input


def test_accept_refuses_when_the_gold_set_is_for_a_different_agent(tmp_path):
    gold_set = _copy_eval(tmp_path)  # this file's own agent is pattern_matcher
    item = _item()  # this item's own agent is rule_recovery
    with pytest.raises(AgentReviewError, match="pattern_matcher"):
        accept(gold_set, item, case_id="x", root=tmp_path)


def test_accept_refuses_a_duplicate_case_id(tmp_path):
    gold_set = _copy_eval(tmp_path, agent="rule_recovery")
    item = _item(tier="medium")
    accept(gold_set, item, case_id="dup", root=tmp_path)
    with pytest.raises(AgentReviewError, match="already exists"):
        accept(gold_set, item, case_id="dup", root=tmp_path)


def test_accept_refuses_an_unthresholded_tier(tmp_path):
    gold_set = _copy_eval(tmp_path, agent="rule_recovery")  # this gold set has no 'complex' threshold
    item = _item(tier="complex")
    with pytest.raises(AgentReviewError, match="no threshold"):
        accept(gold_set, item, case_id="x", root=tmp_path)


def test_accept_missing_gold_set_is_a_clear_error(tmp_path):
    item = _item()
    with pytest.raises(AgentReviewError):
        accept(tmp_path / "missing.yaml", item, case_id="x", root=tmp_path)


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a real draft rule's text (reasoning) and citation (source evidence) render together.
    AC2: an edit keeps the original for comparison, and a diff names exactly what changed.
    AC3: accept and reject each append a real, schema-valid case to a real gold set -- against a
    private copy, the committed gold set itself untouched."""
    draft_path = _write_draft(tmp_path / "draft")
    rule = load_draft_rule(draft_path, tmp_path / "draft")
    item = rule_recovery_item(rule, case_input="agents/examples/rule_recovery/java/Splitter.java:33-47", tier="medium")
    assert item.draft_text and item.citation_link

    edited = edit_text(item, "A corrected reading of the same branch.")
    assert edited.original.draft_text != edited.edited.draft_text
    assert edited.diff()

    gold_set = _copy_eval(tmp_path / "gold", agent="rule_recovery")
    accepted = accept(gold_set, edited.edited, case_id="ac_check_accept", root=tmp_path / "gold")
    assert accepted.expected == frozenset(item.expected)

    rejected = reject(gold_set, item, case_id="ac_check_reject", root=tmp_path / "gold")
    assert rejected.expected == frozenset()

    # the real committed gold set is untouched
    assert EVAL_EXAMPLE.read_text(encoding="utf-8") == (REPO / "agents" / "pattern_matcher" / "eval.yaml").read_text(encoding="utf-8")
