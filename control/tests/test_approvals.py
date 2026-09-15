from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.approvals import (
    Approval,
    ApprovalError,
    Rejection,
    approve,
    load_approvals,
    load_rejections,
    reject,
    render_approvals_markdown,
    render_rejections_markdown,
)
from astra_control.autonomy_admin import set_level

REPO = Path(__file__).resolve().parents[2]
REAL_DRAFT_DIR = REPO / "work" / "spec-reader" / "pershing_gcus_full" / "2017-07-25"
REAL_EVIDENCE = REAL_DRAFT_DIR / "report.json"

AGENT = "rule_recovery"
TASK_CLASS = "rule_status"


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _copy_draft_dir(tmp_path: Path) -> Path:
    """A private copy of the real, committed spec-reader draft -- reject()'s own file write
    lands here, never in the real work/ tree."""
    dest = tmp_path / "draft"
    shutil.copytree(REAL_DRAFT_DIR, dest)
    return dest


# ---------------------------------------------------------------- AC1: approval stores user, time, evidence, agent version


def test_approve_records_who_when_evidence_and_agent_version(tmp_path):
    path = tmp_path / "approvals.yaml"
    updated = approve(
        path,
        subject="pershing_gcus.quantity_sign",
        agent=AGENT,
        task_class=TASK_CLASS,
        approved_by="steward@example.com",
        evidence_path=str(REAL_EVIDENCE),
        agent_version="claude-sonnet-5",
        comment="Matches the sample file.",
        at=_at("2026-09-15T09:00:00Z"),
    )
    assert len(updated) == 1
    a = updated[0]
    assert a.approved_by == "steward@example.com"
    assert a.at == "2026-09-15T09:00:00Z"
    assert a.evidence_path == str(REAL_EVIDENCE)
    assert a.agent_version == "claude-sonnet-5"
    reloaded = load_approvals(path)
    assert reloaded == updated


def test_approve_with_no_evidence_or_agent_version_is_honest_none(tmp_path):
    path = tmp_path / "approvals.yaml"
    updated = approve(path, subject="s", agent=AGENT, task_class=TASK_CLASS, approved_by="a@example.com", at=_at("2026-09-15T09:00:00Z"))
    assert updated[0].evidence_path is None
    assert updated[0].agent_version is None


def test_approve_refuses_an_evidence_path_that_does_not_exist(tmp_path):
    path = tmp_path / "approvals.yaml"
    with pytest.raises(ApprovalError, match="evidence file not found"):
        approve(path, subject="s", agent=AGENT, task_class=TASK_CLASS, approved_by="a@example.com", evidence_path=str(tmp_path / "nope.json"))
    assert not path.exists()


def test_approve_refuses_blank_fields(tmp_path):
    path = tmp_path / "approvals.yaml"
    with pytest.raises(ApprovalError, match="subject"):
        approve(path, subject=" ", agent=AGENT, task_class=TASK_CLASS, approved_by="a@example.com")
    with pytest.raises(ApprovalError, match="agent"):
        approve(path, subject="s", agent=" ", task_class=TASK_CLASS, approved_by="a@example.com")
    with pytest.raises(ApprovalError, match="task_class"):
        approve(path, subject="s", agent=AGENT, task_class=" ", approved_by="a@example.com")
    with pytest.raises(ApprovalError, match="approved_by"):
        approve(path, subject="s", agent=AGENT, task_class=TASK_CLASS, approved_by=" ")


# ---------------------------------------------------------------- AC1: the proposer's own autonomy level


def test_approve_records_the_real_current_autonomy_level(tmp_path):
    changes_path = tmp_path / "changes.yaml"
    set_level(changes_path, agent=AGENT, task_class=TASK_CLASS, level="L2", approver="pm@example.com", reason="Enabled.", at=_at("2026-09-14T09:00:00Z"))
    from astra_control.autonomy_admin import load_changes

    changes = load_changes(changes_path)

    path = tmp_path / "approvals.yaml"
    updated = approve(path, subject="s", agent=AGENT, task_class=TASK_CLASS, approved_by="steward@example.com", guardrails_changes=changes, at=_at("2026-09-15T09:00:00Z"))
    assert updated[0].autonomy_level == "L2"


def test_approve_with_no_guardrails_history_defaults_to_l0(tmp_path):
    path = tmp_path / "approvals.yaml"
    updated = approve(path, subject="s", agent=AGENT, task_class=TASK_CLASS, approved_by="steward@example.com")
    assert updated[0].autonomy_level == "L0"


def test_load_approvals_with_no_path_is_empty():
    assert load_approvals(None) == ()


def test_load_approvals_missing_file_is_empty(tmp_path):
    assert load_approvals(tmp_path / "missing.yaml") == ()


def test_approve_appends_to_existing_approvals(tmp_path):
    path = tmp_path / "approvals.yaml"
    approve(path, subject="s1", agent=AGENT, task_class=TASK_CLASS, approved_by="a@example.com", at=_at("2026-09-15T09:00:00Z"))
    updated = approve(path, subject="s2", agent=AGENT, task_class=TASK_CLASS, approved_by="b@example.com", at=_at("2026-09-15T10:00:00Z"))
    assert [a.subject for a in updated] == ["s1", "s2"]


# ---------------------------------------------------------------- AC2: rejection returns the item to the agent, with the comment


def test_reject_requires_a_comment(tmp_path):
    path = tmp_path / "rejections.yaml"
    with pytest.raises(ApprovalError, match="comment"):
        reject(path, subject="s", agent=AGENT, task_class=TASK_CLASS, rejected_by="steward@example.com", comment="")
    assert not path.exists()


def test_reject_records_the_rejection(tmp_path):
    path = tmp_path / "rejections.yaml"
    updated = reject(path, subject="pershing_gcus.quantity_sign", agent=AGENT, task_class=TASK_CLASS, rejected_by="steward@example.com", comment="Wrong sign convention.", at=_at("2026-09-15T09:00:00Z"))
    assert len(updated) == 1
    r = updated[0]
    assert r.rejected_by == "steward@example.com"
    assert r.comment == "Wrong sign convention."
    reloaded = load_rejections(path)
    assert reloaded == updated


def test_reject_writes_a_real_rejection_file_into_the_draft_s_own_directory(tmp_path):
    draft_dir = _copy_draft_dir(tmp_path)
    path = tmp_path / "rejections.yaml"
    reject(path, subject="pershing_gcus_full", agent="spec_reader", task_class="spec_draft", rejected_by="steward@example.com", comment="Missing a field.", draft_dir=draft_dir, at=_at("2026-09-15T09:00:00Z"))

    rejection_file = draft_dir / "rejection.yaml"
    assert rejection_file.is_file()
    text = rejection_file.read_text(encoding="utf-8")
    assert "Missing a field." in text
    assert "steward@example.com" in text
    # the real report files are untouched, sitting right alongside it
    assert (draft_dir / "report.json").is_file()
    assert (draft_dir / "report.md").is_file()


def test_reject_never_touches_the_real_committed_work_directory(tmp_path):
    before = {p: p.read_bytes() for p in REAL_DRAFT_DIR.iterdir() if p.is_file()}
    draft_dir = _copy_draft_dir(tmp_path)
    reject(tmp_path / "rejections.yaml", subject="x", agent="spec_reader", task_class="t", rejected_by="a@example.com", comment="c", draft_dir=draft_dir)
    after = {p: p.read_bytes() for p in REAL_DRAFT_DIR.iterdir() if p.is_file()}
    assert before == after


def test_reject_without_a_draft_dir_only_logs_the_rejection(tmp_path):
    path = tmp_path / "rejections.yaml"
    reject(path, subject="s", agent=AGENT, task_class=TASK_CLASS, rejected_by="a@example.com", comment="c")
    assert load_rejections(path)  # logged
    # nothing else to assert -- no draft_dir means no file to check


def test_reject_refuses_a_draft_dir_that_does_not_exist(tmp_path):
    with pytest.raises(ApprovalError, match="draft directory not found"):
        reject(tmp_path / "rejections.yaml", subject="s", agent=AGENT, task_class=TASK_CLASS, rejected_by="a@example.com", comment="c", draft_dir=tmp_path / "nope")


def test_reject_refuses_an_evidence_path_that_does_not_exist(tmp_path):
    with pytest.raises(ApprovalError, match="evidence file not found"):
        reject(tmp_path / "rejections.yaml", subject="s", agent=AGENT, task_class=TASK_CLASS, rejected_by="a@example.com", comment="c", evidence_path=str(tmp_path / "nope.json"))


def test_reject_records_the_real_current_autonomy_level(tmp_path):
    changes_path = tmp_path / "changes.yaml"
    set_level(changes_path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="pm@example.com", reason="r", at=_at("2026-09-14T09:00:00Z"))
    from astra_control.autonomy_admin import load_changes

    updated = reject(tmp_path / "rejections.yaml", subject="s", agent=AGENT, task_class=TASK_CLASS, rejected_by="a@example.com", comment="c", guardrails_changes=load_changes(changes_path))
    assert updated[0].autonomy_level == "L1"


def test_load_rejections_with_no_path_is_empty():
    assert load_rejections(None) == ()


def test_load_rejections_missing_file_is_empty(tmp_path):
    assert load_rejections(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- render_*


def test_render_approvals_markdown_shows_every_field():
    approvals = (Approval("s", AGENT, TASK_CLASS, "L2", "a@example.com", "2026-09-15T09:00:00Z", "path/to/evidence.json", "claude-sonnet-5", "note"),)
    text = render_approvals_markdown(approvals)
    assert "L2" in text and "claude-sonnet-5" in text and "path/to/evidence.json" in text


def test_render_approvals_markdown_with_none_says_so():
    assert "No approvals recorded yet." in render_approvals_markdown(())


def test_render_rejections_markdown_shows_the_comment():
    rejections = (Rejection("s", AGENT, TASK_CLASS, "L0", "a@example.com", "2026-09-15T09:00:00Z", None, None, "Wrong."),)
    text = render_rejections_markdown(rejections)
    assert "Wrong." in text


def test_render_rejections_markdown_with_none_says_so():
    assert "No rejections recorded yet." in render_rejections_markdown(())


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: approval stores user, time, evidence seen, agent version, and the proposer's real
    autonomy level. AC2: rejection is refused without a comment, and returns the item to the
    agent as a real file in its own draft directory."""
    changes_path = tmp_path / "changes.yaml"
    set_level(changes_path, agent=AGENT, task_class=TASK_CLASS, level="L2", approver="pm@example.com", reason="r", at=_at("2026-09-14T09:00:00Z"))
    from astra_control.autonomy_admin import load_changes

    changes = load_changes(changes_path)

    approvals_path = tmp_path / "approvals.yaml"
    updated = approve(
        approvals_path,
        subject="pershing_gcus.quantity_sign",
        agent=AGENT,
        task_class=TASK_CLASS,
        approved_by="steward@example.com",
        guardrails_changes=changes,
        evidence_path=str(REAL_EVIDENCE),
        agent_version="claude-sonnet-5",
        at=_at("2026-09-15T09:00:00Z"),
    )
    a = updated[0]
    assert a.approved_by and a.at and a.evidence_path and a.agent_version and a.autonomy_level == "L2"

    with pytest.raises(ApprovalError, match="comment"):
        reject(tmp_path / "rejections.yaml", subject="x", agent=AGENT, task_class=TASK_CLASS, rejected_by="steward@example.com", comment="")

    draft_dir = _copy_draft_dir(tmp_path)
    reject(tmp_path / "rejections.yaml", subject="pershing_gcus_full", agent="spec_reader", task_class="spec_draft", rejected_by="steward@example.com", comment="Sent back for a fix.", draft_dir=draft_dir, at=_at("2026-09-15T09:05:00Z"))
    assert "Sent back for a fix." in (draft_dir / "rejection.yaml").read_text(encoding="utf-8")
