"""Golden datasets (S4.1.2): the legacy path replayed per business day, outputs and rejections captured, hashed, versioned, read-only."""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.golden import CommandResult, LocalFiles, LocalStore, StoreConflict, capture_day, check, coverage, dataset_hash, existing_versions, load_capture, record, rows_to_csv, verify

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden"
CAPTURE = GOLDEN / "pershing" / "capture.yaml"


class FakeLegacy:
    """The non-production Loader after a replay: rows per output, changeable between captures."""

    def __init__(self) -> None:
        self.positions = [(1, "PERSHING", "12345678", "037833100", date(2026, 8, 3), "100.00000", "227.120000", "22712.00", "GCUS_20260803_POS_001.dat", 2)]
        self.rejections = [(1, "ACCOUNT_NOT_FOUND", "GCUS_20260803_POS_001.dat", 4, "DTL99999999...", datetime(2026, 8, 3, 6, 12))]
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        if "LoaderPosition" in sql:
            return ["AccountId", "CustodianCode", "AccountNumber", "Cusip", "AsOfDate", "Quantity", "Price", "MarketValue", "SourceFile", "SourceLine"], list(self.positions)
        return ["RejectionId", "RejectCode", "SourceFile", "SourceLine", "RawRecord", "RaisedAt"], list(self.rejections)


def ok_runner(argv, cwd, env):
    return CommandResult(0, f"replayed {argv[argv.index('--business-date') + 1]}")


def failing_runner(argv, cwd, env):
    return CommandResult(2, "Loader: cannot connect")


@pytest.fixture
def capture():
    c, problems = load_capture(CAPTURE, REPO)
    assert problems == [], [p.format() for p in problems]
    return c


@pytest.fixture
def archive(tmp_path) -> Path:
    directory = tmp_path / "archive"
    directory.mkdir()
    for day in ("20260803", "20260804"):
        (directory / f"GCUS_{day}_POS_001.dat").write_bytes(b"HDR" + day.encode() + b"\nDTL...\nTRL000000001\n")
    (directory / "GCUS_20260803_TRN_001.dat").write_bytes(b"HDR20260803\nTRL000000000\n")
    (directory / "other.txt").write_bytes(b"not a delivery")
    return directory


def _clock():
    moments = iter(datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc).replace(second=s) for s in range(0, 60))
    return lambda: next(moments)


def _day(capture, day, archive, store, legacy, tmp_path, runner=ok_runner):
    return capture_day(capture, day, files=LocalFiles(archive), store=store, legacy=legacy, runner=runner, environ={"LOADER_NONPROD_CONNECTION": "x"}, work_dir=tmp_path / "work", clock=_clock())


# ------------------------------------------------------------ the file


def test_the_capture_file_says_where_the_files_are_and_how_the_day_is_replayed(capture):
    assert (capture.custodian, capture.sources, capture.business_days) == ("pershing", ("pershing_position",), ("mon", "tue", "wed", "thu", "fri"))
    assert capture.files_pattern == "GCUS_{yyyymmdd}_*.dat" and capture.connection_env == "LOADER_NONPROD_CONNECTION" and capture.rejections == "rejections"
    assert set(capture.outputs) == {"positions", "rejections"} and all("ORDER BY" in sql for sql in capture.outputs.values())
    assert capture.business_days_between(date(2026, 8, 1), date(2026, 8, 7)) == [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
    assert "Password" not in CAPTURE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda t: t.replace("ORDER BY AccountId, Cusip, SourceFile, SourceLine", ""), "legacy.outputs.positions: the query must ORDER BY"),
        (lambda t: t.replace("{file_names}", "{files}"), "legacy.outputs.rejections: unknown placeholder {files}"),
        (lambda t: t.replace("  rejections: rejections\n", "  rejections: rejects\n"), "legacy.outputs has no 'rejects' query"),
        (lambda t: t.replace("{business_date}\", \"--files\"", "{date}\", \"--files\""), "legacy.replay[4]: unknown placeholder {date}"),
        (lambda t: t.replace('pattern: "GCUS_{yyyymmdd}_*.dat"', 'pattern: "GCUS_*.dat"'), "files.pattern must carry a date token"),
        (lambda t: t.replace("connection_env: LOADER_NONPROD_CONNECTION", "connection_env: Server=x;Database=y"), "legacy.connection_env: 'Server=x;Database=y' does not match"),
    ],
)
def test_the_validator_names_what_is_wrong(tmp_path, mutate, expected):
    path = tmp_path / "capture.yaml"
    path.write_text(mutate(CAPTURE.read_text(encoding="utf-8")), encoding="utf-8")
    c, problems = load_capture(path, tmp_path)
    assert c is None and any(p.message.startswith(expected) for p in problems), [p.message for p in problems]


# --------------------------------------------------------- one business day


def test_a_day_is_captured_with_its_file_list_hashes_outputs_and_manifest(capture, archive, tmp_path):
    store = LocalStore(tmp_path / "store")
    legacy = FakeLegacy()
    result = _day(capture, date(2026, 8, 3), archive, store, legacy, tmp_path)
    assert result.status == "captured" and result.version == 1 and len(result.hash) == 64
    assert [s["name"] for s in result.sources] == ["GCUS_20260803_POS_001.dat", "GCUS_20260803_TRN_001.dat"]  # the day's files, not the other day's, not other.txt
    assert all(len(s["sha256"]) == 64 and s["bytes"] > 0 for s in result.sources)
    assert result.rows == {"positions": 1, "rejections": 1} and result.replay_exit == 0
    assert set(result.files) == {"sources.json", "replay.log", "outputs/positions.csv", "outputs/rejections.csv"}
    # the queries carried the day and the file names
    assert "AsOfDate = '2026-08-03'" in legacy.queries[0] and "SourceFile IN ('GCUS_20260803_POS_001.dat', 'GCUS_20260803_TRN_001.dat')" in legacy.queries[1]
    prefix = tmp_path / "store" / "pershing" / "2026-08-03" / "v1"
    manifest = json.loads((prefix / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["hash"] == result.hash == dataset_hash(manifest["files"]) and manifest["version"] == 1 and manifest["supersedes"] is None
    assert manifest["source_files"] == result.sources and manifest["rows"] == {"positions": 1, "rejections": 1} and manifest["rejections_output"] == "rejections"
    assert manifest["replay"][:4] == ["loader-replay", "--custodian", "pershing", "--business-date"]
    positions = (prefix / "outputs" / "positions.csv").read_text(encoding="utf-8")
    assert positions.splitlines()[0] == "AccountId,CustodianCode,AccountNumber,Cusip,AsOfDate,Quantity,Price,MarketValue,SourceFile,SourceLine"
    assert positions.splitlines()[1] == "1,PERSHING,12345678,037833100,2026-08-03,100.00000,227.120000,22712.00,GCUS_20260803_POS_001.dat,2"
    assert "2026-08-03T06:12:00" in (prefix / "outputs" / "rejections.csv").read_text(encoding="utf-8")
    assert "replayed 2026-08-03" in (prefix / "replay.log").read_text(encoding="utf-8")
    assert result.store_ref.endswith("/pershing/2026-08-03/v1")
    # written files are read-only, and the store refuses to write a key twice
    assert not (prefix / "manifest.json").stat().st_mode & stat.S_IWRITE
    with pytest.raises(StoreConflict):
        store.put("pershing/2026-08-03/v1/manifest.json", b"{}")


def test_an_identical_capture_adds_no_version_and_a_changed_one_adds_the_next(capture, archive, tmp_path):
    store = LocalStore(tmp_path / "store")
    legacy = FakeLegacy()
    first = _day(capture, date(2026, 8, 3), archive, store, legacy, tmp_path)
    again = _day(capture, date(2026, 8, 3), archive, store, legacy, tmp_path)
    assert again.status == "unchanged" and again.version == 1 and again.hash == first.hash and again.captured_at == first.captured_at
    legacy.rejections.append((2, "SECURITY_NOT_FOUND", "GCUS_20260803_POS_001.dat", 5, "DTL...", datetime(2026, 8, 3, 6, 13)))
    second = _day(capture, date(2026, 8, 3), archive, store, legacy, tmp_path)
    assert second.status == "captured" and second.version == 2 and second.hash != first.hash and second.rows["rejections"] == 2
    versions = existing_versions(store, "pershing", date(2026, 8, 3))
    assert [v for v, _ in versions] == [1, 2] and versions[1][1]["supersedes"] == first.hash
    assert (tmp_path / "store" / "pershing" / "2026-08-03" / "v1" / "manifest.json").is_file()  # the first version stays


def test_days_without_files_or_with_a_failed_replay_are_reported_not_written(capture, archive, tmp_path):
    store = LocalStore(tmp_path / "store")
    nothing = _day(capture, date(2026, 8, 5), archive, store, FakeLegacy(), tmp_path)
    assert nothing.status == "no_files" and "GCUS_20260805_*.dat" in nothing.detail and store.list("pershing") == []
    failed = _day(capture, date(2026, 8, 3), archive, store, FakeLegacy(), tmp_path, runner=failing_runner)
    assert failed.status == "replay_failed" and failed.replay_exit == 2 and store.list("pershing") == []
    assert "Loader: cannot connect" in (tmp_path / "work" / "pershing" / "2026-08-03" / "replay.log").read_text(encoding="utf-8")


def test_csv_is_deterministic_and_the_hash_covers_names_and_hashes():
    csv_bytes = rows_to_csv(["A", "B"], [(1, None), ("x,y", date(2026, 1, 2))])
    assert csv_bytes == b'A,B\n1,\n"x,y",2026-01-02\n'
    assert dataset_hash({"b": "2", "a": "1"}) == dataset_hash({"a": "1", "b": "2"}) and dataset_hash({"a": "1"}) != dataset_hash({"a": "2"})


# ------------------------------------------------------ the index and verify


def test_the_index_is_appended_never_rewritten_and_coverage_is_measured(capture, archive, tmp_path):
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN / "pershing", golden / "pershing")
    store = LocalStore(tmp_path / "store")
    legacy = FakeLegacy()
    results = [_day(capture, date(2026, 8, 3), archive, store, legacy, tmp_path), _day(capture, date(2026, 8, 4), archive, store, legacy, tmp_path), _day(capture, date(2026, 8, 5), archive, store, legacy, tmp_path)]
    assert record(golden, results) == {"pershing": 2}
    index = json.loads((golden / "pershing" / "datasets.json").read_text(encoding="utf-8"))
    assert [(d["business_date"], d["version"]) for d in index["datasets"]] == [("2026-08-03", 1), ("2026-08-04", 1)]
    assert index["datasets"][0]["source_files"] == ["GCUS_20260803_POS_001.dat", "GCUS_20260803_TRN_001.dat"] and index["datasets"][0]["rows"] == {"positions": 1, "rejections": 1}
    legacy.positions.append((2, "PERSHING", "87654321", "594918104", date(2026, 8, 4), "25.50000", "410.500000", "10467.75", "GCUS_20260804_POS_001.dat", 3))
    assert record(golden, [_day(capture, date(2026, 8, 4), archive, store, legacy, tmp_path)]) == {"pershing": 1}
    index = json.loads((golden / "pershing" / "datasets.json").read_text(encoding="utf-8"))
    assert [(d["business_date"], d["version"]) for d in index["datasets"]] == [("2026-08-03", 1), ("2026-08-04", 1), ("2026-08-04", 2)]
    cov = coverage(golden, "pershing")
    assert (cov.days, cov.versions, cov.first, cov.last, cov.within_range) == (2, 3, "2026-08-03", "2026-08-04", False) and cov.verdict == "2 of at least 30 business days captured"
    assert verify(golden, "pershing", store) == []
    # tampering is caught: a file rewritten in place, a manifest edited, a version gone
    target = tmp_path / "store" / "pershing" / "2026-08-03" / "v1" / "outputs" / "positions.csv"
    target.chmod(stat.S_IWRITE | stat.S_IREAD)
    target.write_text("AccountId\n999\n", encoding="utf-8")
    manifest = tmp_path / "store" / "pershing" / "2026-08-04" / "v2" / "manifest.json"
    manifest.chmod(stat.S_IWRITE | stat.S_IREAD)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["rows"]["positions"] = 99
    manifest.write_text(json.dumps(data), encoding="utf-8")
    gone = tmp_path / "store" / "pershing" / "2026-08-04" / "v1"
    for f in gone.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IWRITE | stat.S_IREAD)  # the store made them read-only; a deliberate deletion for the test
    shutil.rmtree(gone)
    problems = verify(golden, "pershing", store)
    messages = [p.message for p in problems]
    assert any(m.startswith("pershing/2026-08-03/v1/outputs/positions.csv: content changed since capture") for m in messages)
    assert "pershing/2026-08-04/v1: missing from" in " ".join(messages)
    assert not any("2026-08-04/v2" in m for m in messages)  # a manifest field that is not a file hash does not change the dataset hash


def test_check_validates_capture_files_and_indexes(tmp_path):
    captures, problems = check(GOLDEN, REPO)
    assert problems == [] and [c.custodian for c in captures] == ["pershing"]
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN / "pershing", golden / "pershing")
    (golden / "pershing" / "datasets.json").write_text(json.dumps({"custodian": "pershing", "datasets": [
        {"business_date": "2026-08-01", "version": 1, "hash": "ab" * 32, "captured_at": "2026-09-08T10:00:00Z", "store": "x", "source_files": []},
        {"business_date": "2026-08-03", "version": 1, "hash": "nope", "captured_at": "2026-09-08T10:00:00Z", "store": "x", "source_files": []},
        {"business_date": "2026-08-03", "version": 1, "hash": "ab" * 32, "captured_at": "2026-09-08T10:00:00Z", "store": "x", "source_files": []},
        {"business_date": "2026-08-04"},
    ]}), encoding="utf-8")
    _, problems = check(golden, tmp_path)
    messages = [p.message for p in problems]
    assert "datasets[0]: 2026-08-01 is not a business day of pershing" in messages and "datasets[1]: hash is not a sha256" in messages
    assert "datasets[2]: 2026-08-03 v1 is listed twice" in messages and messages[-1].startswith("datasets[3] lacks one of")


# ------------------------------------------------------------------ the CLI


def test_cli_captures_a_range_records_the_index_and_verifies(tmp_path, archive, capsys, monkeypatch):
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN / "pershing", golden / "pershing")
    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "golden_runner", ok_runner)
    monkeypatch.setenv("LOADER_NONPROD_CONNECTION", "Server=loader;Database=LoaderDB")
    legacy = FakeLegacy()
    parser = cli.build_parser()
    args = parser.parse_args(["golden", "capture", str(golden / "pershing"), "--from", "2026-08-01", "--to", "2026-08-05", "--store", str(tmp_path / "store"), "--files", str(archive), "--work", str(tmp_path / "work"), "--root", str(tmp_path)])
    args.legacy = legacy
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "2026-08-03  captured       v1" in out and "2026-08-04  captured       v1" in out and "2026-08-05  no_files" in out
    assert "pershing: 2 new version(s) captured, 0 unchanged, 1 without files, 0 replay failure(s); index golden/pershing/datasets.json updated; 2 of at least 30 business days captured" in out
    assert (golden / "pershing" / "datasets.json").is_file()

    assert main(["golden", "verify", str(golden / "pershing"), "--store", str(tmp_path / "store"), "--root", str(tmp_path)]) == 0
    assert "2 version(s) over 2 business day(s) verify against" in capsys.readouterr().out
    assert main(["golden", "status", str(golden)]) == 1
    assert "pershing: 2 of at least 30 business days captured (2026-08-03 to 2026-08-04, 2 version(s))" in capsys.readouterr().out
    assert main(["golden", "check", str(golden), "--root", str(tmp_path)]) == 0
    assert "checked 1 capture file: no problems" in capsys.readouterr().out


def test_cli_refuses_to_capture_without_the_secret(tmp_path, archive, capsys, monkeypatch):
    monkeypatch.delenv("LOADER_NONPROD_CONNECTION", raising=False)
    assert main(["golden", "capture", str(GOLDEN / "pershing"), "--from", "2026-08-03", "--to", "2026-08-03", "--store", str(tmp_path / "store"), "--files", str(archive), "--root", str(REPO)]) == 2
    assert "LOADER_NONPROD_CONNECTION not set" in capsys.readouterr().err


def test_the_committed_golden_directory_checks_clean_and_has_no_index_yet(capsys):
    assert main(["golden", "check", str(GOLDEN), "--root", str(REPO)]) == 0
    assert "pershing: 2 outputs (positions, rejections)" in capsys.readouterr().out
    assert main(["golden", "status", str(GOLDEN)]) == 1
    assert "pershing: 0 of at least 30 business days captured" in capsys.readouterr().out
