from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.exception_review import (
    AgeingByCode,
    ExceptionGroup,
    ExceptionReviewError,
    ReviewBoard,
    ReviewCard,
    accept,
    ageing_report,
    close,
    edit,
    groups_from,
    load_review_board,
    render_ageing_markdown,
    render_markdown,
    resubmit,
    save_review_board,
    sync,
)

REPO = Path(__file__).resolve().parents[2]
REAL_REPORT = REPO / "control/examples/queue/exception-triage/report.json"
REAL_EXCEPTIONS = REPO / "agents/examples/exception_triage/exceptions.csv"


def _clock():
    return datetime(2026, 9, 16, tzinfo=timezone.utc)


def _group(code="CODE", key="value:x", resolution="Do the thing.", auto_apply=False) -> ExceptionGroup:
    return ExceptionGroup(rejection_code=code, group_key=key, count=1, sample_raw_value="x", resolution=resolution, confidence=0.5, whitelisted=False, auto_apply=auto_apply, exception_ids=("EX1",))


# ---------------------------------------------------------------- groups_from, against real data


def test_groups_from_the_real_committed_report_excludes_auto_apply():
    groups = groups_from(REAL_REPORT)
    assert len(groups) == 8  # 9 suggestions in the real report, 1 is auto_apply (PRICE_STALE)
    assert all(not g.auto_apply for g in groups)
    assert "PRICE_STALE:field:price" not in {g.id for g in groups}


def test_groups_from_missing_file_is_empty(tmp_path):
    assert groups_from(tmp_path / "no-such-report.json") == ()


def test_groups_from_carries_every_real_field():
    groups = groups_from(REAL_REPORT)
    account = next(g for g in groups if g.id == "ACCOUNT_NOT_FOUND:value:ACC9999999")
    assert account.count == 2
    assert account.sample_raw_value == "ACC9999999"
    assert account.resolution.startswith("Add the account")
    assert account.exception_ids == ("EX0001", "EX0002")


def test_groups_from_unresolved_code_has_no_resolution():
    groups = groups_from(REAL_REPORT)
    unresolved = next(g for g in groups if g.rejection_code == "UNKNOWN_LOCAL_CODE")
    assert unresolved.resolution is None


# ---------------------------------------------------------------- sync: new vs suggested, never overwrites


def test_sync_maps_a_resolution_to_suggested_and_none_to_new():
    board = sync(ReviewBoard(), (_group(resolution="fix it"), _group(code="OTHER", resolution=None)))
    assert board.card("CODE", "value:x").status == "suggested"
    assert board.card("OTHER", "value:x").status == "new"


def test_sync_never_touches_an_already_tracked_card():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    resynced = sync(board, (_group(),))
    assert resynced.card("CODE", "value:x").status == "approved"  # not reset to "suggested"


# ---------------------------------------------------------------- the linear state machine, in order only


def test_the_full_lifecycle_in_order():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    assert board.card("CODE", "value:x").status == "approved"
    board = resubmit(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    assert board.card("CODE", "value:x").status == "resubmitted"
    board = close(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    assert board.card("CODE", "value:x").status == "closed"


def test_accept_refuses_a_new_group_with_no_resolution():
    board = sync(ReviewBoard(), (_group(resolution=None),))
    with pytest.raises(ExceptionReviewError, match="no taxonomy resolution"):
        accept(board, "CODE", "value:x", by="ops@example.com")


def test_accept_refuses_an_already_approved_group():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    with pytest.raises(ExceptionReviewError, match="only a suggested exception group can be accepted"):
        accept(board, "CODE", "value:x", by="ops@example.com")


def test_resubmit_refuses_before_accept():
    board = sync(ReviewBoard(), (_group(),))
    with pytest.raises(ExceptionReviewError, match="only an approved exception group can be resubmitted"):
        resubmit(board, "CODE", "value:x", by="ops@example.com")


def test_close_refuses_before_resubmit():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    with pytest.raises(ExceptionReviewError, match="only a resubmitted exception group can be closed"):
        close(board, "CODE", "value:x", by="ops@example.com")


def test_every_transition_refuses_a_blank_by():
    board = sync(ReviewBoard(), (_group(),))
    with pytest.raises(ExceptionReviewError, match="--by"):
        accept(board, "CODE", "value:x", by="  ")


def test_transitions_refuse_an_untracked_group():
    with pytest.raises(ExceptionReviewError, match="not tracked"):
        accept(ReviewBoard(), "NOPE", "value:x", by="ops@example.com")


def test_resubmit_notes_no_live_rerun_honestly():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    board = resubmit(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    note = board.card("CODE", "value:x").transitions[-1].note
    assert "no live re-run" in note


# ---------------------------------------------------------------- edit: only while approved, keeps the original


def test_edit_only_while_approved():
    board = sync(ReviewBoard(), (_group(),))
    with pytest.raises(ExceptionReviewError, match="only an approved exception group's resolution can be edited"):
        edit(board, "CODE", "value:x", "new text", by="ops@example.com")


def test_edit_keeps_the_original_resolution_for_comparison():
    board = sync(ReviewBoard(), (_group(resolution="original text"),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    board = edit(board, "CODE", "value:x", "edited text", by="ops@example.com", at=_clock())
    card = board.card("CODE", "value:x")
    assert card.resolution == "edited text"
    assert card.original_resolution == "original text"
    assert card.edited is True


def test_edit_refuses_blank_resolution():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    with pytest.raises(ExceptionReviewError, match="blank"):
        edit(board, "CODE", "value:x", "   ", by="ops@example.com")


def test_unedited_card_is_not_flagged_edited():
    board = sync(ReviewBoard(), (_group(resolution="same"),))
    assert board.card("CODE", "value:x").edited is False


# ---------------------------------------------------------------- persistence round trip


def test_save_and_load_review_board_round_trips(tmp_path):
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    path = tmp_path / "board.yaml"
    save_review_board(board, path)
    reloaded = load_review_board(path)
    assert reloaded.card("CODE", "value:x").status == "approved"
    assert reloaded.card("CODE", "value:x").transitions[0].by == "ops@example.com"


def test_load_review_board_missing_file_is_empty(tmp_path):
    assert load_review_board(tmp_path / "no-such-board.yaml") == ReviewBoard()


# ---------------------------------------------------------------- render_markdown, grouped by cause (AC1)


def test_render_markdown_groups_by_rejection_code():
    board = sync(ReviewBoard(), (_group(code="A", key="k1"), _group(code="A", key="k2"), _group(code="B", key="k3")))
    text = render_markdown(board)
    assert text.index("## A") < text.index("## B")
    assert "k1" in text and "k2" in text and "k3" in text


def test_render_markdown_with_no_cards_says_so():
    assert "No exception groups tracked yet." in render_markdown(ReviewBoard())


def test_render_markdown_flags_an_edited_resolution():
    board = sync(ReviewBoard(), (_group(),))
    board = accept(board, "CODE", "value:x", by="ops@example.com", at=_clock())
    board = edit(board, "CODE", "value:x", "changed", by="ops@example.com", at=_clock())
    assert "*(edited)*" in render_markdown(board)


# ---------------------------------------------------------------- ageing report, against the real exceptions.csv


def test_ageing_report_against_the_real_committed_exceptions_csv():
    report = ageing_report(REAL_EXCEPTIONS, clock=_clock)
    by_code = {r.rejection_code: r for r in report}
    assert by_code["ACCOUNT_NOT_FOUND"].open_count == 3
    assert by_code["ACCOUNT_NOT_FOUND"].oldest_raised_at == "2026-09-10T09:00:00Z"
    assert by_code["ACCOUNT_NOT_FOUND"].age_days == 5


def test_ageing_report_sorted_oldest_first():
    report = ageing_report(REAL_EXCEPTIONS, clock=_clock)
    ages = [r.age_days for r in report]
    assert ages == sorted(ages, reverse=True)


def test_ageing_report_missing_file_is_empty(tmp_path):
    assert ageing_report(tmp_path / "no-such.csv") == ()


def test_ageing_report_excludes_non_new_status(tmp_path):
    csv_path = tmp_path / "exceptions.csv"
    csv_path.write_text(
        "exception_id,rejection_code,level,entity,custodian_id,field_name,raw_value,message,record_key,raised_at,status\n"
        "EX1,CODE_A,record,,pershing,,,msg,,2026-09-01T00:00:00Z,NEW\n"
        "EX2,CODE_A,record,,pershing,,,msg,,2026-09-01T00:00:00Z,RESOLVED\n",
        encoding="utf-8",
    )
    report = ageing_report(csv_path, clock=_clock)
    assert report == (AgeingByCode(rejection_code="CODE_A", open_count=1, oldest_raised_at="2026-09-01T00:00:00Z", age_days=15),)


def test_render_ageing_markdown_with_no_open_exceptions_says_so():
    assert "No open exceptions." in render_ageing_markdown(())


def test_render_ageing_markdown_shows_every_code():
    report = ageing_report(REAL_EXCEPTIONS, clock=_clock)
    text = render_ageing_markdown(report)
    for r in report:
        assert r.rejection_code in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: grouped by cause; moves new -> suggested -> approved -> resubmitted -> closed.
    AC2: resubmission is tracked honestly (no live re-run capability exists here).
    AC3: an ageing report by code, from real per-exception timestamps."""
    groups = groups_from(REAL_REPORT)
    board = sync(ReviewBoard(), groups)
    assert render_markdown(board)  # AC1: grouped by cause

    suggested = next(g for g in groups if g.resolution is not None)
    board = accept(board, suggested.rejection_code, suggested.group_key, by="ops@example.com", at=_clock())
    board = edit(board, suggested.rejection_code, suggested.group_key, "clarified resolution", by="ops@example.com", at=_clock())
    board = resubmit(board, suggested.rejection_code, suggested.group_key, by="ops@example.com", at=_clock())
    board = close(board, suggested.rejection_code, suggested.group_key, by="ops@example.com", at=_clock())
    card = board.card(suggested.rejection_code, suggested.group_key)
    assert [t.status for t in card.transitions] == ["approved", "approved", "resubmitted", "closed"]  # AC1: the real chain
    assert "no live re-run" in card.transitions[-2].note  # AC2: resubmission honestly tracked

    ageing = ageing_report(REAL_EXCEPTIONS, clock=_clock)
    assert ageing  # AC3: an ageing report by code
