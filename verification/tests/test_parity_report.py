"""Parity report (S4.2.2): match rate, difference groups and trend over a dual-run cycle window, exported into release evidence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.golden import LocalStore, dataset_prefix, load_capture
from astra_verification.parity import FieldMismatch, ParityError, ParityResult, RowMismatch, load_parity
from astra_verification.parity_report import (
    Cycle,
    aggregate,
    bundle_name,
    captured_business_dates,
    export_to_releases,
    render_markdown,
    run,
    run_cycles,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden"
PARITY = GOLDEN / "pershing" / "parity.yaml"
CAPTURE = GOLDEN / "pershing" / "capture.yaml"


def _mapping():
    m, problems = load_parity(PARITY, REPO)
    assert problems == []
    return m


def _capture():
    c, problems = load_capture(CAPTURE, REPO)
    assert problems == []
    return c


def _write_index(golden_dir: Path, custodian: str, dates: list[tuple[str, int]]) -> None:
    path = golden_dir / custodian / "datasets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"custodian": custodian, "datasets": [{"business_date": d, "version": v, "source_files": []} for d, v in dates]}), encoding="utf-8")


def _copy_capture(golden_dir: Path, custodian: str = "pershing") -> None:
    directory = golden_dir / custodian
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "capture.yaml").write_text(CAPTURE.read_text(encoding="utf-8"), encoding="utf-8")


def _result(legacy_rows: int, lakehouse_rows: int, matched: int, mismatches: list[RowMismatch] | None = None) -> ParityResult:
    r = ParityResult("pershing", "2026-08-03", "positions", "POSITION", legacy_rows, lakehouse_rows, matched)
    r.mismatches = mismatches or []
    return r


# ------------------------------------------------------- captured dates


def test_captured_business_dates_filters_to_the_window(tmp_path):
    golden_dir = tmp_path / "golden"
    _write_index(golden_dir, "pershing", [("2026-08-03", 1), ("2026-08-04", 1), ("2026-08-04", 2), ("2026-08-05", 1)])
    dates = captured_business_dates(golden_dir, "pershing", date(2026, 8, 4), date(2026, 8, 5))
    assert [(d["business_date"], d["version"]) for d in dates] == [("2026-08-04", 2), ("2026-08-05", 1)]
    assert len(captured_business_dates(golden_dir, "pershing")) == 3


def test_captured_business_dates_refuses_an_empty_window(tmp_path):
    golden_dir = tmp_path / "golden"
    _write_index(golden_dir, "pershing", [("2026-08-03", 1)])
    with pytest.raises(ParityError, match="no golden datasets captured for pershing between 2026-09-01 and 2026-09-30"):
        captured_business_dates(golden_dir, "pershing", date(2026, 9, 1), date(2026, 9, 30))
    with pytest.raises(ParityError, match="no golden datasets captured for pershing;"):
        captured_business_dates(tmp_path / "empty", "pershing")


# ----------------------------------------------------------- aggregation


def test_the_report_aggregates_rows_and_orders_cycles_by_date():
    cycles = [
        Cycle("2026-08-04", _result(10, 10, 9, [RowMismatch(("a",), (FieldMismatch("Price", "1", "2"),))])),
        Cycle("2026-08-03", _result(10, 10, 10)),
    ]
    report = aggregate("pershing", "positions", "POSITION", cycles)
    assert [c.business_date for c in report.cycles] == ["2026-08-03", "2026-08-04"]  # sorted, not insertion order
    assert (report.legacy_rows, report.lakehouse_rows, report.matched) == (20, 20, 19)
    assert report.match_rate == pytest.approx(19 / 20)
    assert report.field_summary == {"Price": 1}


def test_trend_compares_the_first_and_last_cycle():
    improving = aggregate("pershing", "positions", "POSITION", [Cycle("2026-08-03", _result(100, 100, 90)), Cycle("2026-08-04", _result(100, 100, 99))])
    assert improving.trend == "improving"
    declining = aggregate("pershing", "positions", "POSITION", [Cycle("2026-08-03", _result(100, 100, 99)), Cycle("2026-08-04", _result(100, 100, 90))])
    assert declining.trend == "declining"
    flat = aggregate("pershing", "positions", "POSITION", [Cycle("2026-08-03", _result(100, 100, 99)), Cycle("2026-08-04", _result(100, 100, 99))])
    assert flat.trend == "flat"
    one_cycle = aggregate("pershing", "positions", "POSITION", [Cycle("2026-08-03", _result(100, 100, 99))])
    assert one_cycle.trend == "n/a: one cycle"


def test_an_empty_window_is_perfect_parity_and_the_target_is_met():
    report = aggregate("pershing", "positions", "POSITION", [])
    assert report.match_rate == 1.0 and report.meets_target and report.field_summary == {}


def test_render_markdown_includes_the_trend_table_and_field_groups():
    report = aggregate(
        "pershing", "positions", "POSITION",
        [Cycle("2026-08-03", _result(10, 10, 8, [RowMismatch(("a",), (FieldMismatch("Price", "1", "2"),))])), Cycle("2026-08-04", _result(10, 10, 10))],
    )
    markdown = render_markdown(report)
    assert "# Parity report: pershing" in markdown and "2 cycle(s), 2026-08-03 to 2026-08-04." in markdown
    assert "| 2026-08-03 | 10 | 10 | 8 |" in markdown and "| 2026-08-04 | 10 | 10 | 10 |" in markdown
    assert "| `Price` | 1 |" in markdown
    assert f"Overall match rate: {report.match_rate:.4%}" in markdown and "Trend: improving." in markdown


# -------------------------------------------------------------- running


class FakeExecutor:
    def __init__(self, rows_by_date: dict[str, list[tuple]]) -> None:
        self.rows_by_date = rows_by_date
        self.queries: list[str] = []

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        for d, rows in self.rows_by_date.items():
            if f"= '{d}'" in sql:
                return rows
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def store(tmp_path) -> LocalStore:
    return LocalStore(tmp_path / "store")


def _put_output(store: LocalStore, custodian: str, day: date, version: int, output: str, csv_text: str) -> None:
    store.put(f"{dataset_prefix(custodian, day, version)}/outputs/{output}.csv", csv_text.encode("utf-8"))


def test_run_cycles_runs_one_comparison_per_date(tmp_path, store):
    golden_dir = tmp_path / "golden"
    for day, price in (("2026-08-03", "227.1200"), ("2026-08-04", "410.5000")):
        _put_output(store, "pershing", date.fromisoformat(day), 1, "positions", f"AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n1,A,{day},10.00000,{price},1.00\n")
    _write_index(golden_dir, "pershing", [("2026-08-03", 1), ("2026-08-04", 1)])
    executor = FakeExecutor({"2026-08-03": [("1", "A", date(2026, 8, 3), "10.00000", "227.1200", "1.00")], "2026-08-04": [("1", "A", date(2026, 8, 4), "10.00000", "999.0000", "1.00")]})
    cycles = run_cycles(_mapping(), golden_dir, store, executor, "ASTRA_DEV", [date(2026, 8, 3), date(2026, 8, 4)])
    assert [c.business_date for c in cycles] == ["2026-08-03", "2026-08-04"]
    assert cycles[0].result.matched == 1 and cycles[1].result.matched == 0  # the second day's price is wildly off


def test_export_to_releases_writes_one_copy_per_source(tmp_path):
    report = aggregate("pershing", "positions", "POSITION", [Cycle("2026-08-03", _result(1, 1, 1))])
    capture = _capture()  # sources: [pershing_position]
    written = export_to_releases(report, capture, tmp_path / "releases")
    assert bundle_name("pershing_position") == "pershing-position-parity"
    expected = tmp_path / "releases" / "pershing-position-parity"
    assert set(written) == {expected / "report.md", expected / "report.json"}
    assert "# Parity report: pershing" in (expected / "report.md").read_text(encoding="utf-8")
    assert json.loads((expected / "report.json").read_text(encoding="utf-8"))["cycles"] == 1


def test_run_writes_the_working_report_and_exports_it(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n1,A,2026-08-03,10.00000,227.1200,1.00\n")
    _write_index(golden_dir, "pershing", [("2026-08-03", 1)])
    executor = FakeExecutor({"2026-08-03": [("1", "A", date(2026, 8, 3), "10.00000", "227.1200", "1.00")]})
    report, exported = run(_mapping(), _capture(), golden_dir, store, executor, "ASTRA_DEV", out=tmp_path / "out", releases_dir=tmp_path / "releases")
    assert report.match_rate == 1.0 and len(exported) == 2
    assert (tmp_path / "out" / "report.md").is_file() and (tmp_path / "out" / "report.json").is_file()
    assert (tmp_path / "releases" / "pershing-position-parity" / "report.md").is_file()


def test_run_without_export_writes_only_the_working_report(tmp_path, store):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n1,A,2026-08-03,10.00000,227.1200,1.00\n")
    _write_index(golden_dir, "pershing", [("2026-08-03", 1)])
    executor = FakeExecutor({"2026-08-03": [("1", "A", date(2026, 8, 3), "10.00000", "227.1200", "1.00")]})
    report, exported = run(_mapping(), _capture(), golden_dir, store, executor, "ASTRA_DEV", out=tmp_path / "out", export=False, releases_dir=tmp_path / "releases")
    assert exported == [] and not (tmp_path / "releases").exists()


# ------------------------------------------------------------------- CLI


def test_cli_runs_a_report_and_exports_into_release_evidence(tmp_path, store, monkeypatch, capsys):
    golden_dir = tmp_path / "golden"
    for day, price in (("2026-08-03", "227.1200"), ("2026-08-04", "1.0000")):
        _put_output(store, "pershing", date.fromisoformat(day), 1, "positions", f"AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n1,A,{day},10.00000,{price},1.00\n")
    _write_index(golden_dir, "pershing", [("2026-08-03", 1), ("2026-08-04", 1)])
    _copy_capture(golden_dir)
    executor = FakeExecutor({"2026-08-03": [("1", "A", date(2026, 8, 3), "10.00000", "227.1200", "1.00")], "2026-08-04": [("1", "A", date(2026, 8, 4), "10.00000", "1.0000", "1.00")]})

    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main([
        "parity", "report", "--config", str(PARITY), "--environment", "dev", "--golden", str(golden_dir),
        "--store", str(tmp_path / "store"), "--out", str(tmp_path / "out"), "--releases", "releases",
        "--root", str(tmp_path),
    ])
    (tmp_path / "releases").mkdir(exist_ok=True)  # cli resolves --releases relative to --root; ensure it exists for the assertion below
    out = capsys.readouterr().out
    assert code == 0, out
    assert "parity report of pershing: 2 cycle(s), 2026-08-03 to 2026-08-04" in out
    assert "overall match rate 100.0000%" in out and "trend: flat" in out
    assert "exported to:" in out and "pershing-position-parity" in out
    assert (tmp_path / "releases" / "pershing-position-parity" / "report.md").is_file()


def test_cli_reports_below_target_and_can_skip_export(tmp_path, store, monkeypatch, capsys):
    golden_dir = tmp_path / "golden"
    _put_output(store, "pershing", date(2026, 8, 3), 1, "positions", "AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue\n1,A,2026-08-03,10.00000,227.1200,1.00\n")
    _write_index(golden_dir, "pershing", [("2026-08-03", 1)])
    _copy_capture(golden_dir)
    executor = FakeExecutor({"2026-08-03": [("1", "A", date(2026, 8, 3), "999.00000", "227.1200", "1.00")]})  # quantity mismatch

    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main([
        "parity", "report", "--config", str(PARITY), "--environment", "dev", "--golden", str(golden_dir),
        "--store", str(tmp_path / "store"), "--out", str(tmp_path / "out"), "--no-export", "--root", str(tmp_path),
    ])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "below target" in out and "exported to:" not in out
    assert not (tmp_path / "releases").exists()


def test_cli_reports_a_bad_capture_before_touching_snowflake(tmp_path, capsys):
    (tmp_path / "golden" / "pershing").mkdir(parents=True)
    (tmp_path / "golden" / "pershing" / "parity.yaml").write_text(PARITY.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["parity", "report", "--config", str(tmp_path / "golden" / "pershing" / "parity.yaml"), "--environment", "dev", "--golden", str(tmp_path / "golden"), "--store", str(tmp_path / "store"), "--root", str(tmp_path)]) == 2
    assert "capture.yaml" in capsys.readouterr().err
