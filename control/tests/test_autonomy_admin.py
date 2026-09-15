from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.autonomy_admin import (
    L3_ACCEPTANCE_THRESHOLD,
    L3_MINIMUM_SAMPLE,
    LEVELS,
    AutonomyAdminError,
    Evidence,
    LevelChange,
    current_level,
    every_current_level,
    load_changes,
    load_whitelist,
    load_whitelist_requests,
    render_levels_markdown,
    render_whitelist_markdown,
    render_whitelist_requests_markdown,
    request_whitelist_change,
    set_level,
    whitelisted_codes,
)

REPO = Path(__file__).resolve().parents[2]
REJECTIONS = REPO / "domains" / "custodial" / "rejections.yaml"

AGENT = "exception_triage"
TASK_CLASS = "rejection_resolution"


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _good_evidence() -> Evidence:
    return Evidence(acceptance_rate=0.97, sample_size=120, window="trailing 90 days")


# ---------------------------------------------------------------- set_level: AC2, a reason is required and logged


def test_set_level_l0_needs_no_evidence(tmp_path):
    path = tmp_path / "changes.yaml"
    updated = set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="architect@example.com", reason="Enable suggestions.", at=_at("2026-09-15T09:00:00Z"))
    assert len(updated) == 1
    assert updated[0] == LevelChange(AGENT, TASK_CLASS, "L1", "architect@example.com", "Enable suggestions.", "2026-09-15T09:00:00Z", None)
    reloaded = load_changes(path)
    assert reloaded == updated


def test_set_level_refuses_a_blank_reason(tmp_path):
    path = tmp_path / "changes.yaml"
    with pytest.raises(AutonomyAdminError, match="reason must be given"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="architect@example.com", reason="   ")
    assert not path.exists()  # refused outright, nothing written


def test_set_level_refuses_a_blank_approver(tmp_path):
    path = tmp_path / "changes.yaml"
    with pytest.raises(AutonomyAdminError, match="approver must be given"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="  ", reason="A reason.")


def test_set_level_refuses_an_unknown_level(tmp_path):
    path = tmp_path / "changes.yaml"
    with pytest.raises(AutonomyAdminError, match="not a level"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L4", approver="architect@example.com", reason="A reason.")


def test_set_level_refuses_blank_agent_or_task_class(tmp_path):
    path = tmp_path / "changes.yaml"
    with pytest.raises(AutonomyAdminError, match="agent must be given"):
        set_level(path, agent="  ", task_class=TASK_CLASS, level="L1", approver="a@example.com", reason="r")
    with pytest.raises(AutonomyAdminError, match="task_class must be given"):
        set_level(path, agent=AGENT, task_class=" ", level="L1", approver="a@example.com", reason="r")


# ---------------------------------------------------------------- AC3: L3 needs evidence above threshold


def test_set_level_l3_with_good_evidence_succeeds(tmp_path):
    path = tmp_path / "changes.yaml"
    updated = set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="Cleared threshold.", evidence=_good_evidence(), at=_at("2026-09-15T09:00:00Z"))
    assert updated[0].level == "L3"
    assert updated[0].evidence == _good_evidence()


def test_set_level_l3_with_no_evidence_is_refused(tmp_path):
    path = tmp_path / "changes.yaml"
    with pytest.raises(AutonomyAdminError, match="needs evidence"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="r")
    assert not path.exists()


def test_set_level_l3_below_sample_size_is_refused(tmp_path):
    path = tmp_path / "changes.yaml"
    bad = Evidence(acceptance_rate=0.99, sample_size=L3_MINIMUM_SAMPLE - 1, window="trailing 90 days")
    with pytest.raises(AutonomyAdminError, match="at least 20 decisions"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="r", evidence=bad)


def test_set_level_l3_below_acceptance_threshold_is_refused(tmp_path):
    path = tmp_path / "changes.yaml"
    bad = Evidence(acceptance_rate=L3_ACCEPTANCE_THRESHOLD - 0.01, sample_size=120, window="trailing 90 days")
    with pytest.raises(AutonomyAdminError, match="acceptance rate >= 80%"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="r", evidence=bad)


def test_set_level_l3_with_blank_window_is_refused(tmp_path):
    path = tmp_path / "changes.yaml"
    bad = Evidence(acceptance_rate=0.9, sample_size=120, window="  ")
    with pytest.raises(AutonomyAdminError, match="evidence.window"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="r", evidence=bad)


def test_set_level_l0_to_l2_never_needs_evidence(tmp_path):
    path = tmp_path / "changes.yaml"
    for level in ("L0", "L1", "L2"):
        set_level(path, agent=AGENT, task_class=f"{TASK_CLASS}_{level}", level=level, approver="architect@example.com", reason="r", at=_at("2026-09-15T09:00:00Z"))
    assert len(load_changes(path)) == 3


# ---------------------------------------------------------------- current level, history


def test_current_level_defaults_to_l0_with_no_history():
    assert current_level((), AGENT, TASK_CLASS) == "L0"


def test_current_level_is_the_most_recent_entry(tmp_path):
    path = tmp_path / "changes.yaml"
    set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="a@example.com", reason="r1", at=_at("2026-09-10T09:00:00Z"))
    set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L2", approver="a@example.com", reason="r2", at=_at("2026-09-12T09:00:00Z"))
    changes = load_changes(path)
    assert current_level(changes, AGENT, TASK_CLASS) == "L2"
    assert current_level(changes, "other_agent", TASK_CLASS) == "L0"


def test_every_current_level_keys_by_agent_and_task_class(tmp_path):
    path = tmp_path / "changes.yaml"
    set_level(path, agent="exception_triage", task_class="a", level="L1", approver="x@example.com", reason="r", at=_at("2026-09-10T09:00:00Z"))
    set_level(path, agent="drift_watcher", task_class="b", level="L2", approver="x@example.com", reason="r", at=_at("2026-09-11T09:00:00Z"))
    latest = every_current_level(load_changes(path))
    assert latest[("exception_triage", "a")].level == "L1"
    assert latest[("drift_watcher", "b")].level == "L2"


def test_set_level_appends_to_existing_history(tmp_path):
    path = tmp_path / "changes.yaml"
    set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="a@example.com", reason="r1", at=_at("2026-09-10T09:00:00Z"))
    updated = set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L2", approver="b@example.com", reason="r2", at=_at("2026-09-12T09:00:00Z"))
    assert len(updated) == 2
    assert [c.level for c in updated] == ["L1", "L2"]


def test_load_changes_with_no_path_is_empty():
    assert load_changes(None) == ()


def test_load_changes_missing_file_is_empty(tmp_path):
    assert load_changes(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- the self-healing whitelist: real taxonomy


def test_load_whitelist_reads_the_real_taxonomy():
    taxonomy = load_whitelist(REJECTIONS, REPO)
    assert taxonomy.domain == "custodial"
    assert len(taxonomy.codes) > 50


def test_whitelisted_codes_are_the_real_price_codes():
    taxonomy = load_whitelist(REJECTIONS, REPO)
    assert whitelisted_codes(taxonomy) == ("PRICE_MISSING", "PRICE_STALE")


def test_load_whitelist_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(AutonomyAdminError):
        load_whitelist(tmp_path / "missing.yaml")


# ---------------------------------------------------------------- whitelist change requests: logged, never applied


def test_request_whitelist_change_is_logged(tmp_path):
    path = tmp_path / "whitelist-requests.yaml"
    updated = request_whitelist_change(path, code="FIELD_CODE_UNKNOWN", auto_resolve=True, requested_by="architect@example.com", reason="High volume, low risk.", at=_at("2026-09-15T09:00:00Z"))
    assert len(updated) == 1
    assert updated[0].code == "FIELD_CODE_UNKNOWN"
    assert updated[0].auto_resolve is True
    reloaded = load_whitelist_requests(path)
    assert reloaded == updated


def test_request_whitelist_change_never_writes_to_the_real_taxonomy(tmp_path):
    before = REJECTIONS.read_bytes()
    request_whitelist_change(tmp_path / "whitelist-requests.yaml", code="FIELD_CODE_UNKNOWN", auto_resolve=True, requested_by="a@example.com", reason="r")
    assert REJECTIONS.read_bytes() == before


def test_request_whitelist_change_validates_against_a_real_taxonomy_when_given(tmp_path):
    taxonomy = load_whitelist(REJECTIONS, REPO)
    with pytest.raises(AutonomyAdminError, match="not a code"):
        request_whitelist_change(tmp_path / "requests.yaml", code="NOT_A_REAL_CODE", auto_resolve=True, requested_by="a@example.com", reason="r", taxonomy=taxonomy)


def test_request_whitelist_change_refuses_a_no_op_against_a_real_taxonomy(tmp_path):
    taxonomy = load_whitelist(REJECTIONS, REPO)
    with pytest.raises(AutonomyAdminError, match="already"):
        request_whitelist_change(tmp_path / "requests.yaml", code="PRICE_MISSING", auto_resolve=True, requested_by="a@example.com", reason="r", taxonomy=taxonomy)


def test_request_whitelist_change_without_a_taxonomy_is_not_validated(tmp_path):
    """Still logged, honestly, without checking against the real taxonomy -- a steward reviewing
    the log does that check (module docstring)."""
    updated = request_whitelist_change(tmp_path / "requests.yaml", code="NOT_A_REAL_CODE", auto_resolve=True, requested_by="a@example.com", reason="r")
    assert updated[0].code == "NOT_A_REAL_CODE"


def test_request_whitelist_change_refuses_blank_reason(tmp_path):
    with pytest.raises(AutonomyAdminError, match="reason"):
        request_whitelist_change(tmp_path / "requests.yaml", code="X", auto_resolve=True, requested_by="a@example.com", reason=" ")


# ---------------------------------------------------------------- render_*


def test_render_levels_markdown_with_no_history_says_l0():
    assert "L0 by default" in render_levels_markdown(())


def test_render_levels_markdown_shows_every_agent_task_class():
    changes = (LevelChange(AGENT, TASK_CLASS, "L2", "a@example.com", "r", "2026-09-15T09:00:00Z"),)
    text = render_levels_markdown(changes)
    assert AGENT in text and TASK_CLASS in text and "L2" in text


def test_render_whitelist_markdown_shows_the_real_whitelisted_codes():
    text = render_whitelist_markdown(load_whitelist(REJECTIONS, REPO))
    assert "PRICE_MISSING" in text and "PRICE_STALE" in text


def test_render_whitelist_requests_markdown_with_none_says_so():
    assert "No requests recorded yet." in render_whitelist_requests_markdown(())


def test_render_whitelist_requests_markdown_shows_every_request(tmp_path):
    updated = request_whitelist_change(tmp_path / "requests.yaml", code="FIELD_CODE_UNKNOWN", auto_resolve=True, requested_by="architect@example.com", reason="High volume.")
    text = render_whitelist_requests_markdown(updated)
    assert "FIELD_CODE_UNKNOWN" in text and "architect@example.com" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC2: a level change needs a reason and is logged. AC3: L3 is refused unless the measured
    acceptance rate already clears the threshold, with real evidence, never on request alone."""
    path = tmp_path / "changes.yaml"

    with pytest.raises(AutonomyAdminError, match="reason"):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L1", approver="architect@example.com", reason="")

    with pytest.raises(AutonomyAdminError):
        set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="Too early.", evidence=Evidence(0.5, 5, "trailing 7 days"))

    updated = set_level(path, agent=AGENT, task_class=TASK_CLASS, level="L3", approver="architect@example.com", reason="Cleared threshold.", evidence=_good_evidence(), at=_at("2026-09-15T09:00:00Z"))
    assert current_level(updated, AGENT, TASK_CLASS) == "L3"
    assert updated[-1].reason == "Cleared threshold."

    # whitelist: real taxonomy read, a change request logged, the real file never touched
    taxonomy = load_whitelist(REJECTIONS, REPO)
    assert "PRICE_MISSING" in whitelisted_codes(taxonomy)
    before = REJECTIONS.read_bytes()
    request_whitelist_change(tmp_path / "wl.yaml", code="FIELD_CODE_UNKNOWN", auto_resolve=True, requested_by="architect@example.com", reason="High volume, low risk.", taxonomy=taxonomy)
    assert REJECTIONS.read_bytes() == before
