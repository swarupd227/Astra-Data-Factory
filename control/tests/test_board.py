from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from astra_control.board import (
    IN_FLIGHT_STATIONS,
    STATIONS,
    Board,
    BoardError,
    Station,
    add_custodian,
    from_dict,
    live_per_week,
    load_board,
    move,
    render_markdown,
    save_board,
    set_wip_limit,
)

STREAM = "envestnet-custodial"


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- stations


def test_stations_are_the_backlogs_own_five_in_order():
    assert tuple(s.value for s in STATIONS) == ("profile", "draft", "dry_run", "dual_run", "cutover")


def test_in_flight_is_every_station_short_of_cutover():
    assert IN_FLIGHT_STATIONS == (Station.PROFILE, Station.DRAFT, Station.DRY_RUN, Station.DUAL_RUN)
    assert Station.CUTOVER not in IN_FLIGHT_STATIONS


# ---------------------------------------------------------------- add_custodian


def test_add_custodian_enters_at_profile():
    board = add_custodian(Board(), "pershing", STREAM)
    card = board.card("pershing")
    assert card.station is Station.PROFILE
    assert card.stream == STREAM
    assert card.in_flight is True


def test_add_custodian_rejects_a_blank_custodian_id():
    with pytest.raises(BoardError, match="custodian_id"):
        add_custodian(Board(), "  ", STREAM)


def test_add_custodian_rejects_a_blank_stream():
    with pytest.raises(BoardError, match="stream"):
        add_custodian(Board(), "pershing", "  ")


def test_add_custodian_rejects_a_duplicate():
    board = add_custodian(Board(), "pershing", STREAM)
    with pytest.raises(BoardError, match="already on the board"):
        add_custodian(board, "pershing", STREAM)


def test_add_custodian_with_no_wip_limit_set_is_never_blocked():
    board = Board()
    for i in range(10):
        board = add_custodian(board, f"custodian-{i}", STREAM)
    assert board.wip_count(STREAM) == 10


def test_add_custodian_is_blocked_at_the_wip_limit():
    board = set_wip_limit(Board(), STREAM, 2)
    board = add_custodian(board, "a", STREAM)
    board = add_custodian(board, "b", STREAM)
    with pytest.raises(BoardError, match="WIP limit is 2"):
        add_custodian(board, "c", STREAM)
    assert board.wip_count(STREAM) == 2


# ---------------------------------------------------------------- move


def test_move_advances_a_custodian():
    board = add_custodian(Board(), "pershing", STREAM)
    board = move(board, "pershing", "draft")
    assert board.card("pershing").station is Station.DRAFT


def test_move_rejects_an_unknown_custodian():
    with pytest.raises(BoardError, match="not on the board"):
        move(Board(), "pershing", "draft")


def test_move_rejects_an_invalid_station():
    board = add_custodian(Board(), "pershing", STREAM)
    with pytest.raises(BoardError, match="is not a station"):
        move(board, "pershing", "qa")


def test_move_to_cutover_leaves_the_in_flight_set():
    board = add_custodian(Board(), "pershing", STREAM)
    board = move(board, "pershing", "cutover")
    assert board.card("pershing").in_flight is False
    assert board.wip_count(STREAM) == 0


def test_move_within_in_flight_stations_is_never_blocked_by_the_wip_limit():
    """A lateral move (already in flight -> still in flight) never changes the stream's in-flight
    count, so it is never blocked -- not even once the stream is already over a since-lowered
    limit (ADR 0054)."""
    board = set_wip_limit(Board(), STREAM, 5)
    for i in range(5):
        board = add_custodian(board, f"c{i}", STREAM)
    board = set_wip_limit(board, STREAM, 1)  # lowered after five are already in flight
    assert board.wip_count(STREAM) == 5  # still all five, over the new limit of 1
    board = move(board, "c0", "draft")  # lateral, already in flight -> still in flight
    assert board.card("c0").station is Station.DRAFT  # not blocked


def test_move_back_from_cutover_into_in_flight_is_blocked_at_the_limit():
    """Re-entering the in-flight set (a live custodian pulled back for rework) increases the
    stream's in-flight count, so it IS checked against the limit, unlike a lateral move."""
    board = set_wip_limit(Board(), STREAM, 1)
    board = add_custodian(board, "a", STREAM)
    board = move(board, "a", "cutover")
    board = add_custodian(board, "b", STREAM)  # fills the limit while a is live
    with pytest.raises(BoardError, match="WIP limit is 1"):
        move(board, "a", "draft")


def test_move_records_a_new_transition_with_a_timestamp():
    board = add_custodian(Board(), "pershing", STREAM, at=_at("2026-09-01T00:00:00Z"))
    board = move(board, "pershing", "draft", at=_at("2026-09-02T00:00:00Z"))
    card = board.card("pershing")
    assert [t.at for t in card.transitions] == ["2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"]


# ---------------------------------------------------------------- set_wip_limit


def test_set_wip_limit_rejects_a_blank_stream():
    with pytest.raises(BoardError, match="stream"):
        set_wip_limit(Board(), "  ", 3)


def test_set_wip_limit_rejects_a_negative_limit():
    with pytest.raises(BoardError, match="zero or more"):
        set_wip_limit(Board(), STREAM, -1)


def test_set_wip_limit_of_zero_blocks_every_addition():
    board = set_wip_limit(Board(), STREAM, 0)
    with pytest.raises(BoardError):
        add_custodian(board, "pershing", STREAM)


# ---------------------------------------------------------------- over_limit_streams


def test_over_limit_streams_is_empty_when_within_limits():
    board = set_wip_limit(Board(), STREAM, 2)
    board = add_custodian(board, "pershing", STREAM)
    assert board.over_limit_streams() == ()


def test_over_limit_streams_flags_a_stream_pushed_over_by_a_lowered_limit():
    board = set_wip_limit(Board(), STREAM, 5)
    board = add_custodian(board, "pershing", STREAM)
    board = set_wip_limit(board, STREAM, 0)
    assert board.over_limit_streams() == (STREAM,)


# ---------------------------------------------------------------- live_per_week


def test_live_per_week_counts_only_cutover_transitions():
    board = add_custodian(Board(), "pershing", STREAM, at=_at("2026-09-01T00:00:00Z"))
    board = move(board, "pershing", "cutover", at=_at("2026-09-07T00:00:00Z"))  # 2026-09-07 is a Monday, ISO week 37
    weeks = live_per_week(board)
    assert weeks == {"2026-W37": 1}


def test_live_per_week_ignores_custodians_not_yet_live():
    board = add_custodian(Board(), "pershing", STREAM)
    assert live_per_week(board) == {}


def test_live_per_week_groups_several_custodians_in_the_same_week():
    board = Board()
    board = add_custodian(board, "a", STREAM)
    board = add_custodian(board, "b", STREAM)
    board = move(board, "a", "cutover", at=_at("2026-09-08T00:00:00Z"))
    board = move(board, "b", "cutover", at=_at("2026-09-10T00:00:00Z"))
    assert live_per_week(board) == {"2026-W37": 2}


# ---------------------------------------------------------------- persistence


def test_load_board_missing_file_is_an_empty_board(tmp_path):
    board = load_board(tmp_path / "missing.yaml")
    assert board.cards == () and board.wip_limits == {}


def test_save_and_load_board_round_trips(tmp_path):
    path = tmp_path / "board.yaml"
    board = set_wip_limit(Board(), STREAM, 3)
    board = add_custodian(board, "pershing", STREAM, at=_at("2026-09-01T00:00:00Z"))
    board = move(board, "pershing", "draft", at=_at("2026-09-02T00:00:00Z"))
    save_board(board, path)
    reloaded = load_board(path)
    assert reloaded.wip_limits == {STREAM: 3}
    card = reloaded.card("pershing")
    assert card.station is Station.DRAFT
    assert [t.station for t in card.transitions] == [Station.PROFILE, Station.DRAFT]


def test_from_dict_rejects_an_unknown_station():
    with pytest.raises(BoardError, match="is not a station"):
        from_dict({"custodians": [{"id": "pershing", "stream": STREAM, "transitions": [{"station": "qa", "at": "2026-09-01T00:00:00Z"}]}]})


# ---------------------------------------------------------------- render_markdown


def test_render_markdown_with_an_empty_board_says_so():
    text = render_markdown(Board())
    assert "No custodians on the board yet." in text


def test_render_markdown_lists_stations_and_flags_over_limit():
    board = set_wip_limit(Board(), STREAM, 1)
    board = add_custodian(board, "a", STREAM)
    board = set_wip_limit(board, STREAM, 0)
    text = render_markdown(board)
    assert STREAM in text and "OVER LIMIT" in text and "profile" in text


def test_render_markdown_includes_live_per_week():
    board = add_custodian(Board(), "pershing", STREAM, at=_at("2026-09-01T00:00:00Z"))
    board = move(board, "pershing", "cutover", at=_at("2026-09-08T00:00:00Z"))
    text = render_markdown(board)
    assert "2026-W37" in text


# ---------------------------------------------------------------- CLI


def test_cli_add_and_show(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM])
    assert code == 0, capsys.readouterr()
    code = cli.main(["board", "show", "--board", str(board_path)])
    assert code == 0
    text = capsys.readouterr().out
    assert "pershing" in text and STREAM in text


def test_cli_move(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM])
    capsys.readouterr()
    code = cli.main(["board", "move", "--board", str(board_path), "--custodian", "pershing", "--to", "draft"])
    assert code == 0
    capsys.readouterr()
    data = json.loads(_show_json(cli, board_path, capsys))
    assert data["custodians"][0]["station"] == "draft"


def _show_json(cli, board_path, capsys) -> str:
    cli.main(["board", "show", "--board", str(board_path), "--json"])
    return capsys.readouterr().out


def test_cli_set_wip_limit_then_add_is_blocked(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "set-wip-limit", "--board", str(board_path), "--stream", STREAM, "--limit", "0"])
    capsys.readouterr()
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM])
    assert code == 2
    assert "WIP limit is 0" in capsys.readouterr().err


def test_cli_move_invalid_station_reports_a_clear_error(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM])
    capsys.readouterr()
    code = cli.main(["board", "move", "--board", str(board_path), "--custodian", "pershing", "--to", "qa"])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_show_exits_one_when_a_stream_is_over_limit(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "set-wip-limit", "--board", str(board_path), "--stream", STREAM, "--limit", "5"])
    cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", STREAM])
    cli.main(["board", "set-wip-limit", "--board", str(board_path), "--stream", STREAM, "--limit", "0"])
    capsys.readouterr()
    code = cli.main(["board", "show", "--board", str(board_path)])
    assert code == 1
    assert "over limit" in capsys.readouterr().out


def test_cli_show_with_no_board_file_yet_is_empty_and_exits_zero(tmp_path, capsys):
    import astra_control.cli as cli

    code = cli.main(["board", "show", "--board", str(tmp_path / "missing.yaml")])
    assert code == 0
    assert "No custodians" in capsys.readouterr().out


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S6.1.1: the five backlog stations exist in order; a stream's WIP limit is configurable and
    exceeding it is blocked; custodians live per week is computed and shown."""
    assert tuple(s.value for s in STATIONS) == ("profile", "draft", "dry_run", "dual_run", "cutover")

    board = set_wip_limit(Board(), STREAM, 2)
    board = add_custodian(board, "a", STREAM)
    board = add_custodian(board, "b", STREAM)
    with pytest.raises(BoardError):
        add_custodian(board, "c", STREAM)  # exceeding the configured limit is blocked

    board = move(board, "a", "cutover", at=_at("2026-09-08T00:00:00Z"))
    assert live_per_week(board) == {"2026-W37": 1}
    assert "2026-W37" in render_markdown(board)  # shown, not just computed
