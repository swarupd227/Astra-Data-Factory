from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from astra_control.board import Board, Station, set_wip_limit
from astra_control.config_studio import (
    SEQUENCE,
    TIERS,
    ConfigStudioError,
    advance,
    load_promotion_requests,
    request_promotion,
    start,
)

STREAM = "envestnet-custodial"
BSA = "bsa@example.com"
STEWARD = "steward@example.com"


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _through_dry_run(board: Board, custodian_id: str = "pershing") -> Board:
    board = start(board, custodian_id, STREAM, by=BSA)
    board = advance(board, custodian_id, "draft", by=BSA)
    board = advance(board, custodian_id, "dry_run", by=BSA)
    return board


# ---------------------------------------------------------------- sequence


def test_sequence_is_profile_draft_dry_run_only():
    assert tuple(s.value for s in SEQUENCE) == ("profile", "draft", "dry_run")
    assert Station.DUAL_RUN not in SEQUENCE and Station.CUTOVER not in SEQUENCE


def test_tiers_are_the_pattern_matchers_own_three():
    assert TIERS == ("simple", "medium", "complex")


def test_start_enters_at_profile_and_records_who():
    board = start(Board(), "pershing", STREAM, by=BSA)
    card = board.card("pershing")
    assert card.station is Station.PROFILE
    assert card.moved_by == BSA


def test_advance_moves_one_step_and_records_who():
    board = start(Board(), "pershing", STREAM, by=BSA)
    board = advance(board, "pershing", "draft", by=BSA)
    card = board.card("pershing")
    assert card.station is Station.DRAFT
    assert card.moved_by == BSA


def test_advance_rejects_skipping_a_station():
    board = start(Board(), "pershing", STREAM, by=BSA)
    with pytest.raises(ConfigStudioError, match="one station at a time"):
        advance(board, "pershing", "dry_run", by=BSA)


def test_advance_rejects_a_custodian_not_on_the_board():
    with pytest.raises(ConfigStudioError, match="not on the board"):
        advance(Board(), "pershing", "draft", by=BSA)


def test_advance_rejects_moving_past_dry_run():
    board = _through_dry_run(Board())
    with pytest.raises(ConfigStudioError, match="request_promotion"):
        advance(board, "pershing", "dual_run", by=BSA)


def test_advance_rejects_an_unknown_station():
    board = start(Board(), "pershing", STREAM, by=BSA)
    with pytest.raises(ConfigStudioError, match="is not a station"):
        advance(board, "pershing", "qa", by=BSA)


def test_advance_still_respects_the_boards_own_wip_limit():
    board = set_wip_limit(Board(), STREAM, 0)
    with pytest.raises(ConfigStudioError):
        start(board, "pershing", STREAM, by=BSA)


def test_full_sequence_reaches_dry_run():
    board = _through_dry_run(Board())
    assert board.card("pershing").station is Station.DRY_RUN


# ---------------------------------------------------------------- request_promotion: sequencing


def test_request_promotion_requires_dry_run_first():
    board = start(Board(), "pershing", STREAM, by=BSA)
    with pytest.raises(ConfigStudioError, match="dry_run"):
        request_promotion(board, "unused.yaml", "pershing", tier="simple", requested_by=BSA)


def test_request_promotion_rejects_an_unknown_custodian(tmp_path):
    with pytest.raises(ConfigStudioError, match="not on the board"):
        request_promotion(Board(), tmp_path / "r.yaml", "pershing", tier="simple", requested_by=BSA)


def test_request_promotion_rejects_an_invalid_tier(tmp_path):
    board = _through_dry_run(Board())
    with pytest.raises(ConfigStudioError, match="is not a tier"):
        request_promotion(board, tmp_path / "r.yaml", "pershing", tier="enterprise", requested_by=BSA)


def test_request_promotion_rejects_a_blank_requester(tmp_path):
    board = _through_dry_run(Board())
    with pytest.raises(ConfigStudioError, match="requested_by"):
        request_promotion(board, tmp_path / "r.yaml", "pershing", tier="simple", requested_by="  ")


# ---------------------------------------------------------------- the story's own guardrail: simple = self-service


def test_simple_tier_needs_no_reviewer(tmp_path):
    board = _through_dry_run(Board())
    path = tmp_path / "requests.yaml"
    requests = request_promotion(board, path, "pershing", tier="simple", requested_by=BSA)
    assert requests[0].reviewed_by is None
    assert requests[0].self_service is True


def test_medium_tier_requires_a_reviewer(tmp_path):
    board = _through_dry_run(Board())
    path = tmp_path / "requests.yaml"
    with pytest.raises(ConfigStudioError, match="steward's review"):
        request_promotion(board, path, "pershing", tier="medium", requested_by=BSA)
    assert not path.exists()  # refused outright, nothing written


def test_complex_tier_requires_a_reviewer():
    board = _through_dry_run(Board())
    with pytest.raises(ConfigStudioError, match="steward's review"):
        request_promotion(board, "unused.yaml", "pershing", tier="complex", requested_by=BSA)


def test_medium_tier_with_a_reviewer_succeeds(tmp_path):
    board = _through_dry_run(Board())
    path = tmp_path / "requests.yaml"
    requests = request_promotion(board, path, "pershing", tier="medium", requested_by=BSA, reviewed_by=STEWARD)
    assert requests[0].reviewed_by == STEWARD
    assert requests[0].self_service is False


def test_a_blank_reviewed_by_is_treated_as_missing_for_medium_tier(tmp_path):
    board = _through_dry_run(Board())
    with pytest.raises(ConfigStudioError, match="steward's review"):
        request_promotion(board, tmp_path / "r.yaml", "pershing", tier="medium", requested_by=BSA, reviewed_by="   ")


# ---------------------------------------------------------------- persistence: append-only, who+when


def test_request_promotion_appends_and_is_readable_back(tmp_path):
    board = _through_dry_run(Board())
    path = tmp_path / "requests.yaml"
    request_promotion(board, path, "pershing", tier="simple", requested_by=BSA, note="ready to go", at=_at("2026-09-14T09:00:00Z"))
    requests = load_promotion_requests(path)
    assert len(requests) == 1
    r = requests[0]
    assert r.custodian_id == "pershing" and r.tier == "simple" and r.requested_by == BSA and r.note == "ready to go" and r.at == "2026-09-14T09:00:00Z"


def test_request_promotion_appends_to_an_existing_log(tmp_path):
    board = Board()
    board = _through_dry_run(board, "pershing")
    board = _through_dry_run(board, "fidelity")
    path = tmp_path / "requests.yaml"
    request_promotion(board, path, "pershing", tier="simple", requested_by=BSA)
    request_promotion(board, path, "fidelity", tier="simple", requested_by=BSA)
    requests = load_promotion_requests(path)
    assert [r.custodian_id for r in requests] == ["pershing", "fidelity"]


def test_load_promotion_requests_none_path_is_empty():
    assert load_promotion_requests(None) == ()


def test_load_promotion_requests_missing_file_is_empty(tmp_path):
    assert load_promotion_requests(tmp_path / "missing.yaml") == ()


def test_a_note_with_special_characters_round_trips_safely(tmp_path):
    """The hand-written YAML log quotes free text with json.dumps, not a bare f-string wrap, so a
    quote or colon inside a note can't break the file (unlike a naive '\"{note}\"' wrap would)."""
    board = _through_dry_run(Board())
    path = tmp_path / "requests.yaml"
    tricky = 'contains "quotes", a colon: and a backslash \\'
    request_promotion(board, path, "pershing", tier="simple", requested_by=BSA, note=tricky)
    requests = load_promotion_requests(path)
    assert requests[0].note == tricky


# ---------------------------------------------------------------- CLI


def test_cli_start_advance_and_request_promotion(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    requests_path = tmp_path / "requests.yaml"

    assert cli.main(["config-studio", "start", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM, "--by", BSA]) == 0
    assert cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "draft", "--by", BSA]) == 0
    assert cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "dry_run", "--by", BSA]) == 0
    capsys.readouterr()

    code = cli.main(["config-studio", "request-promotion", "--board", str(board_path), "--requests", str(requests_path), "--custodian", "pershing", "--tier", "simple", "--requested-by", BSA])
    assert code == 0, capsys.readouterr()
    text = capsys.readouterr().out
    assert "self-service, no engineer" in text

    code = cli.main(["config-studio", "show-requests", "--requests", str(requests_path), "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["custodian_id"] == "pershing" and data[0]["self_service"] is True


def test_cli_request_promotion_for_medium_tier_without_reviewer_is_rejected(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    requests_path = tmp_path / "requests.yaml"
    cli.main(["config-studio", "start", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM, "--by", BSA])
    cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "draft", "--by", BSA])
    cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "dry_run", "--by", BSA])
    capsys.readouterr()

    code = cli.main(["config-studio", "request-promotion", "--board", str(board_path), "--requests", str(requests_path), "--custodian", "pershing", "--tier", "medium", "--requested-by", BSA])
    assert code == 2
    assert "steward's review" in capsys.readouterr().err
    assert not requests_path.exists()


def test_cli_advance_skipping_a_station_is_rejected(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["config-studio", "start", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM, "--by", BSA])
    capsys.readouterr()
    code = cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "dry_run", "--by", BSA])
    assert code == 2
    assert "one station at a time" in capsys.readouterr().err


def test_cli_show_requests_with_no_log_says_so(capsys):
    import astra_control.cli as cli

    code = cli.main(["config-studio", "show-requests"])
    assert code == 0
    assert "No promotion requests recorded yet." in capsys.readouterr().out


def test_cli_board_add_and_move_accept_an_optional_by(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM, "--by", BSA])
    capsys.readouterr()
    code = cli.main(["board", "show", "--board", str(board_path)])
    assert code == 0
    assert BSA in capsys.readouterr().out


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S6.1.2: a simple custodian goes sample -> promotion request with no engineer involved, and
    every step (profile, draft, dry_run, the promotion request itself) is recorded with who and
    when."""
    path = tmp_path / "requests.yaml"

    board = start(Board(), "pershing", STREAM, by=BSA, at=_at("2026-09-14T09:00:00Z"))
    board = advance(board, "pershing", "draft", by=BSA, at=_at("2026-09-14T09:05:00Z"))
    board = advance(board, "pershing", "dry_run", by=BSA, at=_at("2026-09-14T09:10:00Z"))

    for t in board.card("pershing").transitions:
        assert t.by == BSA and t.at  # who and when, every step

    requests = request_promotion(board, path, "pershing", tier="simple", requested_by=BSA, at=_at("2026-09-14T09:15:00Z"))
    assert requests[0].reviewed_by is None  # no engineer involved
    assert requests[0].requested_by == BSA and requests[0].at == "2026-09-14T09:15:00Z"  # who and when
