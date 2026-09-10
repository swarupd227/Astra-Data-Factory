"""Replay a config change against N days of history (S4.1.3): a difference report grouped by rule and field."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import compile_config
from astra_verification.cli import main
from astra_verification.golden import INDEX_FILE
from astra_verification.replay import (
    CanonicalSnapshot,
    ReplayError,
    business_days_from_golden,
    config_diff,
    diff_rows,
    exception_delta,
    old_config_from_git,
    replay,
)
from astra_verification.replay import test_delta as compute_test_delta
from astra_verification.dryrun import DryRunReport

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
CDM_DDL = REPO / "domains" / "custodial" / "cdm" / "rendered" / "1.0" / "ddl.sql"
REFERENCE_BUNDLE = REPO / "releases" / "custodial-reference-data"
VALID = CONFIG.read_text(encoding="utf-8")

METADATA_COLUMNS = ["FILE_NAME", "LINE_COUNT", "HEADER_COUNT", "DETAIL_COUNT", "TRAILER_COUNT", "EXCLUDED_ROWS", "FIELD_PROBLEMS", "FILE_PROBLEMS", "HEADER_FILE_DATE", "HEADER_REMOTE_ID", "HEADER_REFRESH_FLAG", "TRAILER_DETAIL_COUNT", "FIRST_LINE_AT", "LAST_LINE_AT"]
POSITION_COLUMNS = ["CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "AS_OF_DATE", "CUSTODIAN_SECURITY_ID", "POSITION_TYPE", "QUANTITY", "PRICE", "MARKET_VALUE", "COST_BASIS", "ACCRUED_INTEREST", "CURRENCY", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "LOADED_AT", "UPDATED_AT"]


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


def _compile(inputs, tmp_path, text, subdir="config"):
    registry, catalog, packs = inputs
    directory = tmp_path / subdir
    directory.mkdir(exist_ok=True)
    path = directory / "pershing_position.yaml"
    path.write_text(text, encoding="utf-8")
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)


# --------------------------------------------------------- config diff


def test_a_mapping_constant_change_is_one_field_diff(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    new = _compile(inputs, tmp_path, VALID.replace("constant: USD", "constant: CAD", 1), "new")
    diff = config_diff(old, new)
    assert [(f.group, f.column, f.kind, f.before["constant"], f.after["constant"]) for f in diff.fields] == [("constant: CURRENCY", "CURRENCY", "changed", "USD", "CAD")]
    assert not diff.other


def test_a_mapping_transform_change_keeps_the_rule_as_the_group(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    new = _compile(inputs, tmp_path, VALID.replace("signed_implied_decimal(13, 5)", "signed_implied_decimal(13, 4)"), "new")
    diff = config_diff(old, new)
    assert len(diff.fields) == 1
    f = diff.fields[0]
    assert (f.group, f.column, f.kind) == ("pershing_gcus.quantity_sign", "QUANTITY", "changed")
    assert f.before["transform"]["arguments"] == [13, 5] and f.after["transform"]["arguments"] == [13, 4]


def test_dq_rules_added_and_removed_are_reported(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    removed = VALID.replace(
        "  - id: cusip_present\n"
        "    kind: not_null\n"
        "    level: record\n"
        "    check: every detail record names a security\n"
        "    field: cusip\n"
        "    severity: warning\n"
        "    owner: steward@example.com\n",
        "",
    )
    added = removed.replace(
        "  - id: price_not_negative",
        "  - id: quantity_present\n"
        "    kind: not_null\n"
        "    level: record\n"
        "    check: every detail record has a quantity\n"
        "    field: quantity\n"
        "    severity: warning\n"
        "    owner: steward@example.com\n"
        "  - id: price_not_negative",
    )
    new = _compile(inputs, tmp_path, added, "new")
    diff = config_diff(old, new)
    by_group = {f.group: f.kind for f in diff.fields}
    assert by_group["dq:cusip_present"] == "removed" and by_group["dq:quantity_present"] == "added"


def test_resolution_change_is_reported_by_part(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    new = _compile(inputs, tmp_path, VALID.replace("lookback_days: 5", "lookback_days: 10"), "new")
    diff = config_diff(old, new)
    assert [(f.group, f.kind, f.before["lookback_days"], f.after["lookback_days"]) for f in diff.fields] == [("resolution:price", "changed", 5, 10)]


def test_other_config_differences_are_noted_separately(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    new = _compile(inputs, tmp_path, VALID.replace("effective_from: 2026-09-01", "effective_from: 2026-10-01"), "new")
    diff = config_diff(old, new)
    assert diff.fields == [] and any(o.startswith("effective_from:") for o in diff.other) and diff.changed


def test_an_unchanged_config_has_no_diff(inputs, tmp_path):
    old = _compile(inputs, tmp_path, VALID, "old")
    new = _compile(inputs, tmp_path, VALID, "new")
    diff = config_diff(old, new)
    assert diff.fields == [] and diff.other == [] and not diff.changed


# ----------------------------------------------------------- row diff


def _new_compiled(inputs, tmp_path):
    return _compile(inputs, tmp_path, VALID, "new")


def test_added_removed_and_changed_rows_are_grouped_by_attribution(inputs, tmp_path):
    new = _new_compiled(inputs, tmp_path)
    key1 = ("PERSHING", "12345678", "037833100", "2026-08-03")
    key2 = ("PERSHING", "87654321", "594918104", "2026-08-03")
    key3 = ("PERSHING", "11111111", "037833100", "2026-08-04")
    old = CanonicalSnapshot("POSITION", ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "AS_OF_DATE"), {
        key1: {"QUANTITY": "100.00000", "CURRENCY": "USD"},
        key2: {"QUANTITY": "25.00000", "CURRENCY": "USD", "MARKET_VALUE": "10267.50"},  # MARKET_VALUE has no mapping
    })
    new_snap = CanonicalSnapshot("POSITION", old.key, {
        key1: {"QUANTITY": "100.00000", "CURRENCY": "CAD"},  # currency changed: a constant mapping
        key3: {"QUANTITY": "10.00000", "CURRENCY": "USD", "MARKET_VALUE": "2271.20"},  # key2 gone (removed), key3 new (added)
    })
    row_diffs, groups = diff_rows(old, new_snap, new)
    kinds = {d.key: d.kind for d in row_diffs}
    assert kinds == {key1: "changed", key2: "removed", key3: "added"}
    changed = next(d for d in row_diffs if d.kind == "changed")
    assert changed.columns == {"CURRENCY": ("USD", "CAD")}
    # key1's CURRENCY changed; key2 and key3's CURRENCY is also attributed here, since an added or removed row
    # attributes every one of its columns, CURRENCY included
    assert groups["constant: CURRENCY"].keys == {key1, key2, key3}
    changed_sample = next(s for s in groups["constant: CURRENCY"].samples if s["key"] == list(key1))
    assert changed_sample == {"key": list(key1), "column": "CURRENCY", "before": "USD", "after": "CAD"}
    # removed and added rows attribute every one of their columns, not only the one column that happens to differ elsewhere;
    # QUANTITY is governed by a rule, so its group is the rule id, not a bare "mapping: ..." label
    assert groups["pershing_gcus.quantity_sign"].keys == {key2, key3}
    assert groups["unmapped"].keys == {key2, key3}  # MARKET_VALUE has no mapping in this config


def test_identical_snapshots_produce_no_row_diff(inputs, tmp_path):
    new = _new_compiled(inputs, tmp_path)
    key = ("PERSHING", "12345678", "037833100", "2026-08-03")
    snap = CanonicalSnapshot("POSITION", ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "AS_OF_DATE"), {key: {"QUANTITY": "100.00000", "CURRENCY": "USD"}})
    row_diffs, groups = diff_rows(snap, snap, new)
    assert row_diffs == [] and groups == {}


# ----------------------------------------------- exception and test delta


def _report(exceptions=(), tests=()) -> DryRunReport:
    return DryRunReport("c", "pershing_position", "v1", "sbx", "task", "2026-09-10T00:00:00Z", [], exceptions=list(exceptions), tests=list(tests), run_id="r1")


def test_exception_delta_lists_only_codes_that_changed():
    old = _report(exceptions=[{"code": "ACCOUNT_NOT_FOUND", "level": "record", "stage": "resolution", "rows": 2}, {"code": "RECORD_TYPE_UNKNOWN", "level": "record", "stage": "parse", "rows": 1}])
    new = _report(exceptions=[{"code": "ACCOUNT_NOT_FOUND", "level": "record", "stage": "resolution", "rows": 0}, {"code": "RECORD_TYPE_UNKNOWN", "level": "record", "stage": "parse", "rows": 1}])
    delta = exception_delta(old, new)
    assert delta == [{"code": "ACCOUNT_NOT_FOUND", "level": "record", "stage": "resolution", "old_rows": 2, "new_rows": 0, "delta": -2}]


def test_test_delta_lists_only_tests_that_changed_pass_fail():
    old = _report(tests=[{"test": "a", "passed": True}, {"test": "b", "passed": False}])
    new = _report(tests=[{"test": "a", "passed": True}, {"test": "b", "passed": True}])
    assert compute_test_delta(old, new) == [{"test": "b", "old_passed": False, "new_passed": True}]


# --------------------------------------------------- business days and git


def _write_index(golden_dir: Path, custodian: str, entries: list[dict]) -> None:
    path = golden_dir / custodian / INDEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"custodian": custodian, "datasets": entries}, indent=2), encoding="utf-8")


def test_business_days_from_golden_takes_the_most_recent_n_with_the_latest_version(tmp_path):
    golden = tmp_path / "golden"
    _write_index(
        golden,
        "pershing",
        [
            {"business_date": "2026-08-03", "version": 1, "source_files": ["a.dat"]},
            {"business_date": "2026-08-04", "version": 1, "source_files": ["b.dat"]},
            {"business_date": "2026-08-04", "version": 2, "source_files": ["b.dat", "b2.dat"]},
            {"business_date": "2026-08-05", "version": 1, "source_files": ["c.dat"]},
        ],
    )
    chosen = business_days_from_golden(golden, "pershing", 2)
    assert [(e["business_date"], e["version"]) for e in chosen] == [("2026-08-04", 2), ("2026-08-05", 1)]
    assert business_days_from_golden(golden, "pershing", 0) == business_days_from_golden(golden, "pershing", 10)


def test_business_days_from_golden_refuses_an_uncaptured_custodian(tmp_path):
    with pytest.raises(ReplayError, match="no golden datasets captured for pershing"):
        business_days_from_golden(tmp_path / "golden", "pershing", 30)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


def test_old_config_from_git_reads_the_committed_version(tmp_path):
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    config = repo / "configs" / "pershing_position.yaml"
    config.write_text("id: v1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "configs/pershing_position.yaml")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "v1")
    config.write_text("id: v2\n", encoding="utf-8")
    old_path = old_config_from_git(config, "HEAD", repo, tmp_path / "work")
    assert old_path.read_text(encoding="utf-8") == "id: v1\n"
    assert config.read_text(encoding="utf-8") == "id: v2\n"  # the working file (the draft) is untouched


def test_old_config_from_git_names_the_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    with pytest.raises(ReplayError, match="git show"):
        old_config_from_git(repo / "configs" / "nope.yaml", "HEAD", repo, tmp_path / "work")


# ---------------------------------------------------------------- end to end


class FakeExecutor:
    """Answers both sandboxes of a replay (task ids ending -old / -new -> databases ending _OLD / _NEW)."""

    def __init__(self, old_currency: str = "USD", new_currency: str = "USD") -> None:
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self.currency = {"OLD": old_currency, "NEW": new_currency}

    def _side(self, sql: str) -> str:
        return "OLD" if "_OLD" in sql else "NEW"

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        if 'PROCESS"()' in sql:
            return [(f"run-{self._side(sql).lower()}",)]
        if '"TABLE_NAME" = \'PERSHING_POSITION_FILE_METADATA\'' in sql:
            return [(c,) for c in METADATA_COLUMNS]
        if '"TABLE_NAME" = \'POSITION\'' in sql:
            return [(c,) for c in POSITION_COLUMNS]
        if 'SELECT * FROM' in sql and '"SILVER"."POSITION" WHERE "CUSTODIAN_ID"' in sql:
            currency = self.currency[self._side(sql)]
            row1 = ["PERSHING", "12345678", "037833100", date(2026, 8, 3), None, "LONG", "100.00000", "227.120000", None, None, None, currency, "pershing", "pershing/GCUS_20260803_POS_001.dat", 2, "abc123", datetime(2026, 9, 10), None]
            row2 = ["PERSHING", "87654321", "594918104", date(2026, 8, 4), None, "LONG", "25.00000", "410.500000", None, None, None, currency, "pershing", "pershing/GCUS_20260804_POS_001.dat", 2, "abc123", datetime(2026, 9, 10), None]
            return [tuple(row1), tuple(row2)]
        if '"PERSHING_POSITION_FILE_METADATA" ORDER BY "FILE_NAME"' in sql:
            return [("pershing/GCUS_20260803_POS_001.dat", 5, 1, 2, 1, 0, 0, 0, "2026-08-03", "RMT0000001", "R", 2, "2026-09-10 06:00:00", "2026-09-10 06:00:00"), ("pershing/GCUS_20260804_POS_001.dat", 5, 1, 2, 1, 0, 0, 0, "2026-08-04", "RMT0000001", "R", 2, "2026-09-10 06:00:00", "2026-09-10 06:00:00")]
        if '"RETIRED_AT" IS NULL' in sql:
            return [(4,)]
        if 'COUNT(*) FROM' in sql and '"SILVER"."POSITION"' in sql:
            return [(2,)]
        if '"BRONZE"."PERSHING_POSITION_RUNS" WHERE "RUN_ID"' in sql:
            return [(2, 2, 0, 4, 2, 0, 0, 0, 0)]
        if '"BRONZE"."PERSHING_POSITION_FILES" ORDER BY' in sql:
            return [("pershing/GCUS_20260803_POS_001.dat", "merged", 5), ("pershing/GCUS_20260804_POS_001.dat", "merged", 5)]
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def archive(tmp_path) -> Path:
    directory = tmp_path / "archive"
    directory.mkdir()
    header = "HDR20260803RMT0000001R".ljust(120)
    detail = ("DTL" + "12345678".ljust(10) + "037833100" + "000000000010000000+" + "000000227120000" + "20260803EQ".ljust(70)).ljust(120)
    (directory / "GCUS_20260803_POS_001.dat").write_text("\n".join([header, detail, "TRL000000001".ljust(120)]) + "\n", encoding="ascii")
    (directory / "GCUS_20260804_POS_001.dat").write_text("\n".join([header.replace("20260803", "20260804"), detail, "TRL000000001".ljust(120)]) + "\n", encoding="ascii")
    return directory


@pytest.fixture
def golden(tmp_path, archive) -> Path:
    golden_dir = tmp_path / "golden"
    text = (REPO / "golden" / "pershing" / "capture.yaml").read_text(encoding="utf-8")
    text = text.replace("location: s3://astra-dev-landing-123456789012/archive/pershing", f"location: {archive.as_posix()}")
    (golden_dir / "pershing").mkdir(parents=True)
    (golden_dir / "pershing" / "capture.yaml").write_text(text, encoding="utf-8")
    _write_index(
        golden_dir,
        "pershing",
        [
            {"business_date": "2026-08-03", "version": 1, "hash": "a" * 64, "captured_at": "2026-08-04T06:00:00Z", "store": "local", "source_files": ["GCUS_20260803_POS_001.dat"], "rows": {}},
            {"business_date": "2026-08-04", "version": 1, "hash": "b" * 64, "captured_at": "2026-08-05T06:00:00Z", "store": "local", "source_files": ["GCUS_20260804_POS_001.dat"], "rows": {}},
        ],
    )
    return golden_dir


def _clock():
    ticks = iter(range(0, 100000, 3))
    return lambda: float(next(ticks))


def test_replay_finds_the_row_difference_the_config_change_produces(tmp_path, golden):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir(); new_dir.mkdir()
    old_config = old_dir / "pershing_position.yaml"
    old_config.write_text(VALID, encoding="utf-8")
    new_config = new_dir / "pershing_position.yaml"
    new_config.write_text(VALID.replace("constant: USD", "constant: CAD", 1), encoding="utf-8")
    executor = FakeExecutor(old_currency="USD", new_currency="CAD")

    report = replay(
        old_config, new_config, "pershing", "dev", executor,
        repo=REPO, golden_dir=golden, days=2, work_dir=tmp_path / "work", out=tmp_path / "out",
        extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, task_id="replay-t1", monotonic=_clock(),
    )

    assert report.business_days == ["2026-08-03", "2026-08-04"] and sorted(report.sample_files) == ["GCUS_20260803_POS_001.dat", "GCUS_20260804_POS_001.dat"]
    assert report.old_run.status == "ran" and report.new_run.status == "ran" and report.old_run.sandbox.endswith("_OLD") and report.new_run.sandbox.endswith("_NEW")
    assert report.config_diff.changed and [(f.group, f.kind) for f in report.config_diff.fields] == [("constant: CURRENCY", "changed")]
    assert report.rows_compared == 2 and report.rows_added == 0 and report.rows_removed == 0 and report.rows_changed == 2
    assert report.groups["constant: CURRENCY"].keys and len(report.groups["constant: CURRENCY"].keys) == 2
    sample = report.groups["constant: CURRENCY"].samples[0]
    assert sample["column"] == "CURRENCY" and sample["before"] == "USD" and sample["after"] == "CAD"
    assert report.exception_delta == [] and report.test_delta == []
    assert not report.auto_promotable  # a real difference was found

    markdown = (tmp_path / "out" / "replay.md").read_text(encoding="utf-8")
    assert "SME review needed" in markdown and "`constant: CURRENCY` | changed" in markdown
    data = json.loads((tmp_path / "out" / "replay.json").read_text(encoding="utf-8"))
    assert data["auto_promotable"] is False and data["rows_changed"] == 2


def test_replaying_a_config_against_itself_is_eligible_for_promotion(tmp_path, golden):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = config_dir / "pershing_position.yaml"
    config.write_text(VALID, encoding="utf-8")
    executor = FakeExecutor(old_currency="USD", new_currency="USD")

    report = replay(
        config, config, "pershing", "dev", executor,
        repo=REPO, golden_dir=golden, days=2, work_dir=tmp_path / "work", out=tmp_path / "out",
        extra_bundles=[REFERENCE_BUNDLE], cdm_ddl=CDM_DDL, task_id="replay-t2", monotonic=_clock(),
    )

    assert not report.config_diff.changed
    assert report.row_diffs == [] and report.exception_delta == [] and report.test_delta == []
    assert report.rows_compared == 2
    assert report.auto_promotable
    markdown = (tmp_path / "out" / "replay.md").read_text(encoding="utf-8")
    assert "Eligible for promotion without SME review: no differences." in markdown


def test_replay_refuses_when_the_custodian_has_no_golden_datasets(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config = config_dir / "pershing_position.yaml"
    config.write_text(VALID, encoding="utf-8")
    with pytest.raises(ReplayError, match="no golden datasets captured"):
        replay(config, config, "pershing", "dev", FakeExecutor(), repo=REPO, golden_dir=tmp_path / "golden", work_dir=tmp_path / "work", out=tmp_path / "out")


# ---------------------------------------------------------------------- CLI


def test_cli_replays_and_returns_1_when_sme_review_is_needed(tmp_path, golden, monkeypatch, capsys):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir(); new_dir.mkdir()
    old_config = old_dir / "pershing_position.yaml"
    old_config.write_text(VALID, encoding="utf-8")
    new_config = new_dir / "pershing_position.yaml"
    new_config.write_text(VALID.replace("constant: USD", "constant: CAD", 1), encoding="utf-8")
    executor = FakeExecutor(old_currency="USD", new_currency="CAD")

    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "_executor", lambda: executor)
    code = main([
        "replay", "--new", str(new_config), "--old", str(old_config), "--custodian", "pershing",
        "--days", "2", "--environment", "dev", "--repo", str(REPO), "--golden", str(golden),
        "--bundle", str(REFERENCE_BUNDLE), "--cdm-ddl", str(CDM_DDL), "--task", "replay-cli1",
        "--work", str(tmp_path / "work"), "--out", str(tmp_path / "out"),
    ])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "replay of pershing: 2 business day(s) (2026-08-03 to 2026-08-04)" in out
    assert "config: 1 field/rule/dq/resolution difference(s)" in out
    assert "data: 2 row(s) compared, 0 added, 0 removed, 2 changed" in out
    assert "SME review needed" in out


def test_cli_requires_exactly_one_of_old_and_old_ref(capsys):
    assert main(["replay", "--new", "x.yaml", "--old", "y.yaml", "--old-ref", "HEAD", "--custodian", "pershing", "--environment", "dev"]) == 2
    assert "exactly one of --old" in capsys.readouterr().err
