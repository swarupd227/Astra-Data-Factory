from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.run_status import (
    RUN_WINDOW_BUDGET_MINUTES,
    CustodianRunStatus,
    Dashboard,
    FileStage,
    RunInput,
    RunStatusError,
    build,
    build_dashboard,
    load_run,
    render_dashboard_markdown,
    render_status_markdown,
)

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
POSITIONS_PATTERN = "pershing/GCUS_%_POS_%.dat"
TRANSACTIONS_PATTERN = "pershing/GCUS_%_TRN_%.dat"


def _build(**kwargs):
    return build(CONFIG, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO, **kwargs)


def _write_run(tmp_path: Path, data: dict) -> Path:
    """JSON is valid YAML -- astra_core.yamlsource.load() reads it directly, no new dependency."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "run.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------- load_run: a real, working stand-in


def test_load_run_with_no_path_is_empty():
    run = load_run(None)
    assert run == RunInput(None, None, {})


def test_load_run_with_a_missing_file_is_empty(tmp_path):
    run = load_run(tmp_path / "missing.yaml")
    assert run == RunInput(None, None, {})


def test_load_run_reads_business_date_run_log_and_stages(tmp_path):
    path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "run_log": "https://internal/airflow/dags/pershing_gate/runs/2026-09-15",
        "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:12:00Z", "parsed_at": "2026-09-15T10:14:00Z"}},
    })
    run = load_run(path)
    assert run.business_date == "2026-09-15"
    assert run.run_log == "https://internal/airflow/dags/pershing_gate/runs/2026-09-15"
    assert run.stages[POSITIONS_PATTERN]["arrived_at"] == "2026-09-15T10:12:00Z"


def test_load_run_rejects_a_non_mapping_file(tmp_path):
    path = tmp_path / "run.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(RunStatusError, match="mapping"):
        load_run(path)


# ---------------------------------------------------------------- build: expected files from the real config


def test_build_with_no_run_shows_every_file_still_expected():
    status = _build()
    assert status.custodian_id == "pershing"
    assert {f.pattern for f in status.files} == {POSITIONS_PATTERN, TRANSACTIONS_PATTERN}
    assert all(f.current_stage == "expected" for f in status.files)
    assert status.cutoff_time == "06:00"
    assert status.timezone == "America/New_York"


def test_build_with_no_run_has_no_business_date_or_run_log():
    status = _build()
    assert status.business_date is None
    assert status.run_log is None


def test_build_reads_real_arrival_and_publish_times(tmp_path):
    run_path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "files": {
            POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:05:00Z", "parsed_at": "2026-09-15T10:07:00Z", "resolved_at": "2026-09-15T10:10:00Z", "published_at": "2026-09-15T10:15:00Z"},
        },
    })
    status = _build(run_path=run_path)
    positions = next(f for f in status.files if f.pattern == POSITIONS_PATTERN)
    assert positions.current_stage == "published"
    transactions = next(f for f in status.files if f.pattern == TRANSACTIONS_PATTERN)
    assert transactions.current_stage == "expected"  # not in the run file at all


def test_build_missing_config_is_a_clear_error(tmp_path):
    with pytest.raises(RunStatusError, match="not found"):
        build(tmp_path / "missing.yaml", specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- FileStage.current_stage


def test_current_stage_progresses_through_every_named_stage():
    base = dict(pattern="p", description="d")
    assert FileStage(**base, arrived_at=None, parsed_at=None, resolved_at=None, published_at=None).current_stage == "expected"
    assert FileStage(**base, arrived_at="t", parsed_at=None, resolved_at=None, published_at=None).current_stage == "arrived"
    assert FileStage(**base, arrived_at="t", parsed_at="t", resolved_at=None, published_at=None).current_stage == "parsed"
    assert FileStage(**base, arrived_at="t", parsed_at="t", resolved_at="t", published_at=None).current_stage == "resolved"
    assert FileStage(**base, arrived_at="t", parsed_at="t", resolved_at="t", published_at="t").current_stage == "published"


# ---------------------------------------------------------------- AC1: late list with missing files and cutoff


def test_missing_files_lists_only_the_unarrived_ones(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15", "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"}}})
    status = _build(run_path=run_path)
    assert [f.pattern for f in status.missing_files] == [TRANSACTIONS_PATTERN]


def test_is_late_past_cutoff_with_missing_files(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15"})  # no files arrived at all
    status = _build(run_path=run_path)
    before_cutoff = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)  # 01:00 America/New_York, before the 06:00 cutoff
    after_cutoff = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)  # 08:00 America/New_York, after
    assert status.is_late(before_cutoff) is False
    assert status.is_late(after_cutoff) is True


def test_is_late_past_cutoff_but_nothing_missing_is_not_late(tmp_path):
    run_path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "files": {
            POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"},
            TRANSACTIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"},
        },
    })
    status = _build(run_path=run_path)
    assert status.is_late(datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)) is False


def test_is_late_with_no_run_given_is_never_late():
    """No business_date means no real cutoff instant can be built (module docstring) -- honestly
    "not late," never a guess."""
    status = _build()
    assert status.is_late(datetime(2099, 1, 1, tzinfo=timezone.utc)) is False


# ---------------------------------------------------------------- AC2: timing bar against the 20-minute budget


def test_elapsed_minutes_from_latest_arrival_to_latest_publish(tmp_path):
    run_path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "files": {
            POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z", "published_at": "2026-09-15T10:12:00Z"},
            TRANSACTIONS_PATTERN: {"arrived_at": "2026-09-15T10:05:00Z", "published_at": "2026-09-15T10:18:00Z"},
        },
    })
    status = _build(run_path=run_path)
    # latest arrival 10:05, latest publish 10:18 -> 13 minutes
    assert status.elapsed_minutes == pytest.approx(13.0)
    assert status.within_budget is True


def test_elapsed_minutes_over_budget(tmp_path):
    run_path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "files": {
            POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z", "published_at": "2026-09-15T10:35:00Z"},
        },
    })
    status = _build(run_path=run_path)
    assert status.elapsed_minutes == pytest.approx(35.0)
    assert status.elapsed_minutes > RUN_WINDOW_BUDGET_MINUTES
    assert status.within_budget is False


def test_elapsed_minutes_none_while_still_in_progress(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15", "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"}}})
    status = _build(run_path=run_path)
    assert status.elapsed_minutes is None
    assert status.within_budget is None


# ---------------------------------------------------------------- AC3: run log


def test_run_log_reference_when_given(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15", "run_log": "https://internal/run/1"})
    status = _build(run_path=run_path)
    assert status.run_log == "https://internal/run/1"


def test_run_log_is_none_when_not_given():
    status = _build()
    assert status.run_log is None


# ---------------------------------------------------------------- the dashboard: many custodians


def test_build_dashboard_lists_late_custodians(tmp_path):
    late_run = _write_run(tmp_path / "late", {"business_date": "2026-09-15"})
    on_time_run = _write_run(tmp_path / "ontime", {
        "business_date": "2026-09-15",
        "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"}, TRANSACTIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"}},
    })
    late_status = _build(run_path=late_run)
    on_time_status = _build(run_path=on_time_run)

    as_of = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)  # after cutoff
    dashboard = build_dashboard((late_status, on_time_status), as_of=as_of)

    assert [s.custodian_id for s in dashboard.late] == ["pershing"]  # only late_status is late; on_time_status is not, but has the SAME custodian_id
    assert len(dashboard.statuses) == 2


def test_build_dashboard_defaults_as_of_to_now():
    dashboard = build_dashboard((_build(),))
    parsed = datetime.fromisoformat(dashboard.as_of.replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 10


def test_dashboard_to_dict_round_trips_through_json():
    dashboard = build_dashboard((_build(),), as_of=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    data = json.loads(json.dumps(dashboard.to_dict()))
    assert data["statuses"][0]["custodian_id"] == "pershing"


# ---------------------------------------------------------------- render_*


def test_render_status_markdown_shows_every_file_and_its_stage(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15", "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z"}}})
    text = render_status_markdown(_build(run_path=run_path))
    assert POSITIONS_PATTERN in text and TRANSACTIONS_PATTERN in text
    assert "arrived" in text and "expected" in text


def test_render_status_markdown_shows_missing_files():
    text = render_status_markdown(_build())
    assert "Missing" in text
    assert POSITIONS_PATTERN in text


def test_render_status_markdown_shows_the_timing_bar_over_budget(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15", "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z", "published_at": "2026-09-15T10:35:00Z"}}})
    text = render_status_markdown(_build(run_path=run_path))
    assert "OVER BUDGET" in text


def test_render_status_markdown_no_run_log_given():
    text = render_status_markdown(_build())
    assert "no run log given" in text


def test_render_dashboard_markdown_lists_late_custodians(tmp_path):
    run_path = _write_run(tmp_path, {"business_date": "2026-09-15"})  # nothing arrived -- a real cutoff, and everything missing
    status = _build(run_path=run_path)
    dashboard = build_dashboard((status,), as_of=datetime(2099, 1, 1, tzinfo=timezone.utc))
    text = render_dashboard_markdown(dashboard)
    assert "Late" in text and "pershing" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a late custodian with missing files and its own cutoff is named. AC2: elapsed time
    against the real 20-minute budget, over and within. AC3: a run log reference when given, an
    honest note when not."""
    run_path = _write_run(tmp_path, {
        "business_date": "2026-09-15",
        "run_log": "https://internal/run/42",
        "files": {POSITIONS_PATTERN: {"arrived_at": "2026-09-15T10:00:00Z", "published_at": "2026-09-15T10:12:00Z"}},
    })
    status = _build(run_path=run_path)

    assert status.missing_files and status.cutoff_time == "06:00"
    assert status.is_late(datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)) is True  # AC1

    assert status.within_budget is True  # AC2, 12 minutes < 20
    assert status.elapsed_minutes < RUN_WINDOW_BUDGET_MINUTES

    assert status.run_log == "https://internal/run/42"  # AC3
