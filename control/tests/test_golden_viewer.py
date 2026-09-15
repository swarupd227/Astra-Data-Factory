from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_control.golden_viewer import CapturedDay, Gap, GoldenCalendar, GoldenViewerError, build, render_markdown

REPO = Path(__file__).resolve().parents[2]
CAPTURE = REPO / "golden" / "pershing" / "capture.yaml"

HASH_V1 = "a" * 64
HASH_V2 = "b" * 64


def _write_index(golden_dir: Path, custodian: str, entries: list[dict]) -> None:
    path = golden_dir / custodian / "datasets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"custodian": custodian, "datasets": entries}), encoding="utf-8")


def _entry(business_date: str, version: int = 1, hash_: str = HASH_V1) -> dict:
    return {
        "business_date": business_date,
        "version": version,
        "hash": hash_,
        "captured_at": f"{business_date}T09:00:00Z",
        "store": f"s3://golden-bucket/pershing/{business_date}/v{version}",
        "source_files": [f"GCUS_{business_date.replace('-', '')}_POS.dat"],
        "rows": {"positions": 412, "rejections": 3},
    }


# ---------------------------------------------------------------- build(): the real capture file, a private index


def test_build_with_no_index_shows_every_expected_day_as_a_gap(tmp_path):
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 4))
    assert calendar.custodian == "pershing"
    assert calendar.captured == ()
    assert [g.business_date for g in calendar.gaps] == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]  # tue-fri, all business days


def test_build_spanning_a_weekend_excludes_saturday_and_sunday(tmp_path):
    """2026-09-05/06 are Saturday/Sunday -- not in capture.yaml's own business_days -- never a gap."""
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 4), end=date(2026, 9, 7))  # fri, sat, sun, mon
    assert [g.business_date for g in calendar.gaps] == ["2026-09-04", "2026-09-07"]  # sat/sun never appear


def test_build_reads_the_real_index_when_one_exists(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01"), _entry("2026-09-03")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 4))
    assert [c.business_date for c in calendar.captured] == ["2026-09-01", "2026-09-03"]
    assert [g.business_date for g in calendar.gaps] == ["2026-09-02", "2026-09-04"]


def test_build_carries_every_version_including_superseded_ones(tmp_path):
    """AC1's own "hashes" (plural): a day captured twice shows both versions, both hashes."""
    _write_index(tmp_path, "pershing", [_entry("2026-09-01", version=1, hash_=HASH_V1), _entry("2026-09-01", version=2, hash_=HASH_V2)])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert len(calendar.captured) == 2
    assert {c.hash for c in calendar.captured} == {HASH_V1, HASH_V2}
    assert calendar.gaps == ()  # at least one version exists for this day


def test_build_captured_day_carries_hash_rows_and_source_files(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    day = calendar.captured[0]
    assert day.hash == HASH_V1
    assert day.rows == {"positions": 412, "rejections": 3}
    assert day.source_files == ("GCUS_20260901_POS.dat",)


def test_build_excludes_entries_outside_the_requested_window(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-08-15"), _entry("2026-09-01")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert [c.business_date for c in calendar.captured] == ["2026-09-01"]


def test_build_from_after_to_is_refused(tmp_path):
    with pytest.raises(GoldenViewerError, match="after"):
        build(CAPTURE, tmp_path, start=date(2026, 9, 10), end=date(2026, 9, 1))


def test_build_missing_capture_file_is_a_clear_error(tmp_path):
    with pytest.raises(GoldenViewerError):
        build(tmp_path / "missing.yaml", tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))


# ---------------------------------------------------------------- AC2: gap capture command


def test_gap_command_is_a_real_runnable_capture_command(tmp_path):
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1), store="s3://golden-bucket")
    gap = calendar.gaps[0]
    assert gap.capture_command == f"astra-verify golden capture {CAPTURE} --from 2026-09-01 --to 2026-09-01 --store s3://golden-bucket"


def test_gap_command_with_no_store_names_a_placeholder(tmp_path):
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert "<golden store" in calendar.gaps[0].capture_command


# ---------------------------------------------------------------- coverage summary, reused from astra_verification.golden


def test_coverage_reflects_the_real_index_not_just_the_requested_window(tmp_path):
    """astra_verification.golden.coverage is reused directly -- it reports the CUSTODIAN's
    whole index, not scoped to the --from/--to window given to build()."""
    _write_index(tmp_path, "pershing", [_entry(f"2026-0{m}-01") for m in range(1, 6)])  # 5 captured days, jan-may
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert calendar.coverage_days == 5
    assert calendar.coverage_versions == 5
    assert calendar.within_range is False  # 5 < MIN_DAYS (30)


def test_complete_is_true_only_when_no_gap(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01")])
    complete = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    incomplete = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 2))
    assert complete.complete is True
    assert incomplete.complete is False


# ---------------------------------------------------------------- to_dict / render_markdown


def test_to_dict_round_trips_through_json(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 2))
    data = json.loads(json.dumps(calendar.to_dict()))
    assert data["custodian"] == "pershing"
    assert len(data["captured"]) == 1
    assert len(data["gaps"]) == 1


def test_render_markdown_shows_captured_and_gaps(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 2))
    text = render_markdown(calendar)
    assert "2026-09-01" in text and "2026-09-02" in text
    assert HASH_V1[:12] in text
    assert "astra-verify golden capture" in text


def test_render_markdown_no_gaps_says_so(tmp_path):
    _write_index(tmp_path, "pershing", [_entry("2026-09-01")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert "No gaps in this window." in render_markdown(calendar)


def test_render_markdown_nothing_captured_says_so(tmp_path):
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 1))
    assert "Nothing captured in this window." in render_markdown(calendar)


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a calendar of captured days, each with its own hash. AC2: a gap is highlighted, and a
    real, runnable capture command is given for it."""
    _write_index(tmp_path, "pershing", [_entry("2026-09-01"), _entry("2026-09-03")])
    calendar = build(CAPTURE, tmp_path, start=date(2026, 9, 1), end=date(2026, 9, 4), store="s3://golden-bucket")

    assert {c.business_date for c in calendar.captured} == {"2026-09-01", "2026-09-03"}
    assert all(c.hash for c in calendar.captured)  # AC1: hashes

    assert {g.business_date for g in calendar.gaps} == {"2026-09-02", "2026-09-04"}  # AC2: gaps
    assert all(g.capture_command.startswith("astra-verify golden capture") for g in calendar.gaps)  # AC2: capture requestable
