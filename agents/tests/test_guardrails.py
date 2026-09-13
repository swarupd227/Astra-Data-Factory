from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_agents.guardrails import (
    DEFAULT_LEVEL,
    L3_ACCEPTANCE_THRESHOLD,
    L3_MINIMUM_SAMPLE,
    Evidence,
    GuardrailsError,
    Level,
    current_change,
    current_level,
    every_current_level,
    load_changes,
    permits,
    record_change,
    render_status,
)

GOOD_EVIDENCE = Evidence(acceptance_rate=0.9, sample_size=40, window="trailing 90 days")


# ---------------------------------------------------------------- Level


def test_level_rank_orders_l0_through_l3():
    assert Level.L0.rank < Level.L1.rank < Level.L2.rank < Level.L3.rank


def test_level_has_exactly_four_members():
    assert {lvl.value for lvl in Level} == {"L0", "L1", "L2", "L3"}


# ---------------------------------------------------------------- default state


def test_current_level_with_no_log_at_all_is_l0():
    assert current_level((), "any-agent", "any-task-class") == DEFAULT_LEVEL == Level.L0


def test_load_changes_none_path_is_empty():
    assert load_changes(None) == ()


def test_load_changes_missing_file_is_empty(tmp_path):
    assert load_changes(tmp_path / "missing.yaml") == ()


def test_permits_with_no_log_denies_l3():
    assert permits((), "exception-triage", "whitelisted_exception_classes", Level.L3) is False


def test_permits_l0_is_always_granted_even_with_no_log():
    assert permits((), "any-agent", "any-task-class", Level.L0) is True


# ---------------------------------------------------------------- record_change: approval required for any level


def test_record_change_requires_a_non_blank_agent(tmp_path):
    with pytest.raises(GuardrailsError, match="agent"):
        record_change(tmp_path / "c.yaml", agent="  ", task_class="x", level="L1", approver="a@example.com", reason="why")


def test_record_change_requires_a_non_blank_task_class(tmp_path):
    with pytest.raises(GuardrailsError, match="task_class"):
        record_change(tmp_path / "c.yaml", agent="a", task_class=" ", level="L1", approver="a@example.com", reason="why")


def test_record_change_requires_a_non_blank_approver(tmp_path):
    with pytest.raises(GuardrailsError, match="approver"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L1", approver="  ", reason="why")


def test_record_change_requires_a_non_blank_reason(tmp_path):
    with pytest.raises(GuardrailsError, match="reason"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L1", approver="a@example.com", reason="  ")


def test_record_change_rejects_an_invalid_level(tmp_path):
    with pytest.raises(GuardrailsError, match="level"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L9", approver="a@example.com", reason="why")


def test_record_change_to_l1_needs_no_evidence(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="rule-recovery", task_class="rule_recovery", level="L1", approver="architect@example.com", reason="matches the spec's own table")
    assert current_level(load_changes(path), "rule-recovery", "rule_recovery") == Level.L1


def test_a_bad_change_writes_nothing_to_the_log(tmp_path):
    """A rejected change is refused outright — never recorded and flagged for later."""
    path = tmp_path / "c.yaml"
    with pytest.raises(GuardrailsError):
        record_change(path, agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why")
    assert not path.exists()


# ---------------------------------------------------------------- record_change: L3 needs evidence


def test_record_change_to_l3_with_no_evidence_is_rejected(tmp_path):
    with pytest.raises(GuardrailsError, match="evidence"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why")


def test_record_change_to_l3_below_the_acceptance_threshold_is_rejected(tmp_path):
    evidence = Evidence(acceptance_rate=L3_ACCEPTANCE_THRESHOLD - 0.01, sample_size=L3_MINIMUM_SAMPLE, window="trailing 90 days")
    with pytest.raises(GuardrailsError, match="acceptance rate"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why", evidence=evidence)


def test_record_change_to_l3_below_the_minimum_sample_is_rejected(tmp_path):
    evidence = Evidence(acceptance_rate=0.99, sample_size=L3_MINIMUM_SAMPLE - 1, window="trailing 90 days")
    with pytest.raises(GuardrailsError, match="decisions measured"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why", evidence=evidence)


def test_record_change_to_l3_with_a_blank_window_is_rejected(tmp_path):
    evidence = Evidence(acceptance_rate=0.99, sample_size=100, window="  ")
    with pytest.raises(GuardrailsError, match="window"):
        record_change(tmp_path / "c.yaml", agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why", evidence=evidence)


def test_record_change_to_l3_at_exactly_the_threshold_and_minimum_is_accepted(tmp_path):
    evidence = Evidence(acceptance_rate=L3_ACCEPTANCE_THRESHOLD, sample_size=L3_MINIMUM_SAMPLE, window="trailing 90 days")
    path = tmp_path / "c.yaml"
    record_change(path, agent="exception-triage", task_class="whitelisted_exception_classes", level="L3", approver="architect@example.com", reason="cleared the bar exactly", evidence=evidence)
    assert current_level(load_changes(path), "exception-triage", "whitelisted_exception_classes") == Level.L3


def test_record_change_to_l3_with_good_evidence_is_readable_back(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="exception-triage", task_class="whitelisted_exception_classes", level="L3", approver="architect@example.com", reason="Q3 review", evidence=GOOD_EVIDENCE)
    changes = load_changes(path)
    change = current_change(changes, "exception-triage", "whitelisted_exception_classes")
    assert change.evidence == GOOD_EVIDENCE
    assert change.approver == "architect@example.com"


# ---------------------------------------------------------------- current_level / current_change / permits


def test_current_level_is_the_most_recent_change_not_the_highest(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="a", task_class="x", level="L3", approver="architect@example.com", reason="promote", evidence=GOOD_EVIDENCE)
    record_change(path, agent="a", task_class="x", level="L1", approver="architect@example.com", reason="a regression was found; demote")
    assert current_level(load_changes(path), "a", "x") == Level.L1


def test_current_level_is_scoped_to_one_agent_and_task_class(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="a", task_class="x", level="L2", approver="architect@example.com", reason="why")
    record_change(path, agent="a", task_class="y", level="L3", approver="architect@example.com", reason="why", evidence=GOOD_EVIDENCE)
    changes = load_changes(path)
    assert current_level(changes, "a", "x") == Level.L2
    assert current_level(changes, "a", "y") == Level.L3
    assert current_level(changes, "b", "x") == Level.L0  # a different agent, never mentioned


def test_permits_l3_only_once_actually_promoted(tmp_path):
    path = tmp_path / "c.yaml"
    changes = load_changes(path if path.is_file() else None)
    assert permits(changes, "exception-triage", "whitelisted_exception_classes", Level.L3) is False
    record_change(path, agent="exception-triage", task_class="whitelisted_exception_classes", level="L3", approver="architect@example.com", reason="Q3 review", evidence=GOOD_EVIDENCE)
    changes = load_changes(path)
    assert permits(changes, "exception-triage", "whitelisted_exception_classes", Level.L3) is True
    assert permits(changes, "exception-triage", "whitelisted_exception_classes", Level.L1) is True  # L3 covers everything below it


def test_current_change_is_none_when_never_recorded():
    assert current_change((), "a", "x") is None


def test_every_current_level_returns_only_the_latest_per_pair(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="a", task_class="x", level="L1", approver="architect@example.com", reason="start")
    record_change(path, agent="a", task_class="x", level="L2", approver="architect@example.com", reason="promote")
    record_change(path, agent="b", task_class="y", level="L1", approver="architect@example.com", reason="start")
    latest = every_current_level(load_changes(path))
    assert len(latest) == 2
    assert latest[("a", "x")].level == Level.L2


# ---------------------------------------------------------------- render_status


def test_render_status_with_no_changes_says_so():
    text = render_status(())
    assert "No level change has ever been recorded" in text
    assert "L0" in text


def test_render_status_lists_every_pair(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="a", task_class="x", level="L1", approver="architect@example.com", reason="start")
    record_change(path, agent="b", task_class="y", level="L2", approver="architect@example.com", reason="start")
    text = render_status(load_changes(path))
    assert "| a | x | L1 |" in text
    assert "| b | y | L2 |" in text


def test_render_status_filters_by_agent_and_task_class(tmp_path):
    path = tmp_path / "c.yaml"
    record_change(path, agent="a", task_class="x", level="L1", approver="architect@example.com", reason="start")
    record_change(path, agent="b", task_class="y", level="L2", approver="architect@example.com", reason="start")
    changes = load_changes(path)
    text = render_status(changes, agent="a")
    assert "| a | x |" in text
    assert "| b | y |" not in text


# ---------------------------------------------------------------- CLI


def test_cli_set_level_l1_needs_no_evidence(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "c.yaml"
    code = cli.main(["guardrails", "set-level", "--changes", str(path), "--agent", "rule-recovery", "--task-class", "rule_recovery", "--level", "L1", "--approver", "architect@example.com", "--reason", "matches the spec's own table"])
    assert code == 0, capsys.readouterr()
    assert current_level(load_changes(path), "rule-recovery", "rule_recovery") == Level.L1


def test_cli_set_level_l3_without_evidence_is_rejected(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "c.yaml"
    code = cli.main(["guardrails", "set-level", "--changes", str(path), "--agent", "a", "--task-class", "x", "--level", "L3", "--approver", "architect@example.com", "--reason", "why"])
    assert code == 2
    assert capsys.readouterr().err
    assert not path.exists()


def test_cli_set_level_l3_with_partial_evidence_flags_is_rejected(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "c.yaml"
    code = cli.main(["guardrails", "set-level", "--changes", str(path), "--agent", "a", "--task-class", "x", "--level", "L3", "--approver", "architect@example.com", "--reason", "why", "--acceptance-rate", "0.9"])
    assert code == 2
    assert "together" in capsys.readouterr().err


def test_cli_set_level_l3_with_full_evidence_succeeds(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "c.yaml"
    code = cli.main(
        [
            "guardrails", "set-level", "--changes", str(path),
            "--agent", "exception-triage", "--task-class", "whitelisted_exception_classes",
            "--level", "L3", "--approver", "architect@example.com", "--reason", "Q3 review",
            "--acceptance-rate", "0.9", "--sample-size", "40", "--window", "trailing 90 days",
        ]
    )
    assert code == 0, capsys.readouterr()
    assert current_level(load_changes(path), "exception-triage", "whitelisted_exception_classes") == Level.L3


def test_cli_status_with_no_log_reports_nothing_recorded(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["guardrails", "status", "--changes", str(tmp_path / "missing.yaml")])
    assert code == 0
    assert "No level change has ever been recorded" in capsys.readouterr().out


def test_cli_status_json_reports_the_recorded_level(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "c.yaml"
    cli.main(["guardrails", "set-level", "--changes", str(path), "--agent", "docs-writer", "--task-class", "docs", "--level", "L2", "--approver", "architect@example.com", "--reason", "matches the spec's own table"])
    capsys.readouterr()
    code = cli.main(["guardrails", "status", "--changes", str(path), "--json"])
    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"agent": "docs-writer", "task_class": "docs", "level": "L2", "approver": "architect@example.com", "reason": "matches the spec's own table", "at": rows[0]["at"], "evidence": None}]


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S5.13.1: a level change is refused without an approver and a reason (and is never written to
    the log when refused), and a change to L3 is refused without a measured acceptance rate above a
    threshold over a stated window."""
    path = tmp_path / "c.yaml"

    with pytest.raises(GuardrailsError):
        record_change(path, agent="a", task_class="x", level="L2", approver="", reason="why")
    assert not path.exists()

    with pytest.raises(GuardrailsError):
        record_change(path, agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why")  # no evidence
    with pytest.raises(GuardrailsError):
        record_change(path, agent="a", task_class="x", level="L3", approver="architect@example.com", reason="why", evidence=Evidence(acceptance_rate=0.5, sample_size=100, window="90 days"))  # below threshold
    assert not path.exists()

    record_change(path, agent="a", task_class="x", level="L3", approver="architect@example.com", reason="cleared the bar", evidence=GOOD_EVIDENCE)
    changes = load_changes(path)
    change = current_change(changes, "a", "x")
    assert change.level == Level.L3 and change.approver == "architect@example.com" and change.evidence.acceptance_rate >= L3_ACCEPTANCE_THRESHOLD
    assert permits(changes, "a", "x", Level.L3) is True
