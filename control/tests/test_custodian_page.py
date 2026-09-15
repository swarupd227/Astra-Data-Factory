from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from astra_control.custodian_page import (
    CustodianPageError,
    build,
    load_arrivals,
    render_markdown,
)
from astra_control.board import Board, add_custodian, move, save_board

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
ARRIVALS = REPO / "control" / "examples" / "arrivals.yaml"
PARITY_REPORT = REPO / "agents" / "examples" / "gate_evidence_compiler" / "parity_report.json"
EXCEPTION_REPORT = REPO / "control" / "examples" / "queue" / "exception-triage" / "report.json"


def _build(**kwargs):
    return build(CONFIG, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO, **kwargs)


# ---------------------------------------------------------------- family, tier, config version


def test_family_and_tier_come_from_the_real_config():
    page = _build()
    assert page.custodian_id == "pershing"
    assert page.family == "pershing_gcus"
    assert page.tier == "medium"


def test_config_version_is_effective_from_plus_a_real_content_hash():
    page = _build()
    assert page.effective_from == "2026-09-01"
    assert len(page.config_sha256) == 64  # a real sha256 hex digest, not a placeholder


def test_build_rejects_a_config_that_does_not_compile(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("config_version: 0\n", encoding="utf-8")
    with pytest.raises(CustodianPageError):
        build(bad, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


def test_build_rejects_a_missing_config():
    with pytest.raises(CustodianPageError, match="not found"):
        build(Path("does/not/exist.yaml"), specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- current station


def test_station_is_none_when_no_board_given():
    page = _build()
    assert page.station is None


def test_station_is_read_straight_off_the_board(tmp_path):
    board_path = tmp_path / "board.yaml"
    board = add_custodian(Board(), "pershing", "envestnet-custodial")
    board = move(board, "pershing", "draft")
    save_board(board, board_path)
    page = _build(board_path=board_path)
    assert page.station == "draft"


def test_station_is_none_when_this_custodian_is_not_on_the_given_board(tmp_path):
    board_path = tmp_path / "board.yaml"
    board = add_custodian(Board(), "fidelity", "envestnet-custodial")
    save_board(board, board_path)
    page = _build(board_path=board_path)
    assert page.station is None


# ---------------------------------------------------------------- files today, cutoff, arrivals


def test_expected_files_come_from_the_real_delivery_block():
    page = _build()
    assert len(page.expected_files) == 2
    assert page.cutoff_time == "06:00" and page.timezone == "America/New_York"


def test_files_default_to_not_arrived():
    page = _build()
    assert all(not f.arrived for f in page.expected_files)
    assert page.all_arrived is False


def test_arrivals_mark_the_matching_pattern_as_arrived():
    arrivals = load_arrivals(ARRIVALS)
    page = _build(arrivals=arrivals)
    position_file = next(f for f in page.expected_files if "POS" in f.pattern)
    transaction_file = next(f for f in page.expected_files if "TRN" in f.pattern)
    assert position_file.arrived is True and position_file.arrived_at == "2026-09-15T05:41:00Z"
    assert transaction_file.arrived is False
    assert page.all_arrived is False


def test_load_arrivals_none_path_is_empty():
    assert load_arrivals(None) == {}


def test_load_arrivals_missing_file_is_empty(tmp_path):
    assert load_arrivals(tmp_path / "missing.yaml") == {}


def test_load_arrivals_reads_the_real_committed_example():
    arrivals = load_arrivals(ARRIVALS)
    assert arrivals == {"pershing/GCUS_%_POS_%.dat": "2026-09-15T05:41:00Z"}


def test_load_arrivals_rejects_a_non_mapping(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(CustodianPageError):
        load_arrivals(path)


# ---------------------------------------------------------------- parity trend


def test_parity_is_none_when_no_report_given():
    assert _build().parity is None


def test_parity_reads_the_real_committed_report():
    page = _build(parity_report=PARITY_REPORT)
    assert page.parity["match_rate"] == 0.998
    assert page.parity["trend"] == "improving"
    assert page.parity["meets_target"] is True


def test_parity_report_missing_file_is_none_not_an_error(tmp_path):
    page = _build(parity_report=tmp_path / "missing.json")
    assert page.parity is None


# ---------------------------------------------------------------- open exceptions


def test_open_exceptions_is_zero_when_no_report_given():
    assert _build().open_exceptions == 0


def test_open_exceptions_reuses_astra_control_queues_own_count():
    from astra_control.queue import exceptions_from

    page = _build(exception_report=EXCEPTION_REPORT)
    assert page.open_exceptions == len(exceptions_from(EXCEPTION_REPORT))
    assert page.open_exceptions == 8


# ---------------------------------------------------------------- cost


def test_cost_is_none_without_a_finops_source():
    assert _build().cost is None


def test_cost_is_shown_when_the_caller_gives_one():
    assert _build(cost=4.82).cost == 4.82


# ---------------------------------------------------------------- AC2: links


def test_links_spec_and_config_are_real_paths_that_exist():
    page = _build()
    assert (REPO / page.links["spec"]).is_file()
    assert Path(page.links["config"]).is_file() or (REPO / page.links["config"]).is_file()


def test_links_note_the_still_unbuilt_exceptions_screen_honestly():
    page = _build()
    assert "S6.2.5" in page.links["exceptions_note"]


def test_parity_viewer_note_points_at_the_real_command_once_a_report_is_given():
    """S6.3.7 shipped a real parity-viewer command -- the note now points at it, not at a story
    number that no longer describes an unbuilt screen."""
    page = _build(parity_report=PARITY_REPORT)
    assert "astra-control parity-viewer trend" in page.links["parity_viewer_note"]
    assert str(PARITY_REPORT) in page.links["parity_viewer_note"]


def test_parity_viewer_note_with_no_report_given():
    page = _build()
    assert page.links["parity_viewer_note"] == "no parity report given"


def test_links_point_at_the_real_report_when_one_is_given():
    page = _build(parity_report=PARITY_REPORT, exception_report=EXCEPTION_REPORT)
    assert page.links["parity_viewer"] == str(PARITY_REPORT)
    assert page.links["exceptions"] == str(EXCEPTION_REPORT)


# ---------------------------------------------------------------- AC1: a real, if modest, performance proxy


def test_build_completes_well_under_the_two_second_budget():
    started = time.perf_counter()
    _build(parity_report=PARITY_REPORT, exception_report=EXCEPTION_REPORT, arrivals=load_arrivals(ARRIVALS))
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0


# ---------------------------------------------------------------- render_markdown / to_dict


def test_render_markdown_includes_every_section():
    page = _build(parity_report=PARITY_REPORT, exception_report=EXCEPTION_REPORT, cost=4.82)
    text = render_markdown(page)
    assert "pershing" in text and "medium" in text
    assert "Files today" in text and "06:00" in text
    assert "99.8%" in text
    assert "Open exceptions: 8" in text
    assert "$4.82" in text


def test_render_markdown_says_no_data_honestly():
    text = render_markdown(_build())
    assert "No parity report given." in text
    assert "no data" in text


def test_to_dict_round_trips_through_json():
    page = _build(parity_report=PARITY_REPORT, cost=4.82)
    data = json.loads(json.dumps(page.to_dict()))
    assert data["custodian_id"] == "pershing"
    assert data["parity"]["trend"] == "improving"


# ---------------------------------------------------------------- CLI


def test_cli_show_against_real_fixtures(capsys):
    import astra_control.cli as cli

    code = cli.main([
        "custodian-page", "show", "--config", str(CONFIG),
        "--parity-report", str(PARITY_REPORT), "--exception-report", str(EXCEPTION_REPORT),
        "--arrivals", str(ARRIVALS), "--cost", "4.82",
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
    ])
    assert code == 0, capsys.readouterr()
    text = capsys.readouterr().out
    assert "pershing" in text and "99.8%" in text and "$4.82" in text


def test_cli_show_json(capsys):
    import astra_control.cli as cli

    code = cli.main(["custodian-page", "show", "--config", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["family"] == "pershing_gcus"


def test_cli_show_reports_a_start_error_for_a_missing_config(tmp_path, capsys):
    import astra_control.cli as cli

    code = cli.main(["custodian-page", "show", "--config", str(tmp_path / "missing.yaml"), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS)])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_show_as_auditor_succeeds_reads_always_allowed(capsys):
    import astra_control.cli as cli

    code = cli.main(["custodian-page", "show", "--config", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--role", "auditor"])
    assert code == 0, capsys.readouterr()


def test_cli_show_invalid_role_is_refused(capsys):
    import astra_control.cli as cli

    code = cli.main(["custodian-page", "show", "--config", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--role", "wizard"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S6.3.3: family, tier, config version, current station, files today, parity trend, open
    exceptions and cost are all in one place (AC of the story text itself); links to spec, config
    diff, parity viewer and exceptions are present (AC2); expected vs arrived is shown with the
    cutoff time (AC3); and the whole assembly is well within a 2-second budget (AC1)."""
    started = time.perf_counter()
    page = _build(parity_report=PARITY_REPORT, exception_report=EXCEPTION_REPORT, arrivals=load_arrivals(ARRIVALS), cost=4.82)
    assert time.perf_counter() - started < 2.0

    assert page.family and page.tier and page.effective_from and page.config_sha256
    assert page.parity is not None
    assert page.open_exceptions == 8
    assert page.cost == 4.82

    for link in ("spec", "config", "config_diff", "parity_viewer", "exceptions"):
        assert link in page.links

    assert page.cutoff_time == "06:00"
    assert any(f.arrived for f in page.expected_files) and any(not f.arrived for f in page.expected_files)
