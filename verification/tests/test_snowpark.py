"""Snowpark Connect assessment hook (S3.3.2): transformers run on an engine, effort and result are recorded and attached to the memo."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_verification.cli import main
from astra_verification.snowpark import EVIDENCE_END, EVIDENCE_START, AssessmentRun, SparkEngine, code_diff, evidence_section, load_assessment, load_runs, run_assessment, update_memo

REPO = Path(__file__).resolve().parents[2]
ASSESSMENT = REPO / "assessments" / "normalizer"

EXPECTED_POSITIONS = [
    ["12345678", "037833100", "100.00000", "227.120000", "2026-08-29"],
    ["12345678", "594918104", "-25.50000", "410.500000", "2026-08-29"],
    ["87654321", "037833100", None, "227.120000", "2026-08-29"],
]
EXPECTED_DRIP = [
    ["TX0000000001", "12345678", "037833100", "BUY", "2026-08-27", "10.0000", "-22712.00"],
    ["TX0000000002-1", "12345678", "037833100", "DIV", "2026-08-28", None, "1135.60"],
    ["TX0000000002-2", "12345678", "037833100", "BUY", "2026-08-28", "0.5000", "-1135.60"],
    ["TX0000000003", "87654321", "594918104", "SELL", "2026-08-28", "25.0000", "10262.50"],
]


class FakeEngine:
    """Answers with canned rows per transformer, as an engine would after running the code."""

    name = "snowpark-connect"

    def __init__(self, rows: dict[str, list[list[str | None]]], fail: str | None = None) -> None:
        self.rows = rows
        self.fail = fail
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        return self

    def lines(self, session, lines):
        return ("lines", tuple(lines))

    def collect(self, frame):
        name = frame  # run() below hands the transformer name back through the fake session
        if name == self.fail:
            raise RuntimeError("SnowparkConnectError: function EXPLODE is not supported in this context")
        rows = self.rows[name]
        columns = json.loads((ASSESSMENT / "expected" / f"{name}.json").read_text(encoding="utf-8"))["columns"]
        return columns, [list(r) for r in rows]

    def stop(self, session):
        self.stopped += 1


@pytest.fixture
def copy(tmp_path) -> Path:
    root = tmp_path / "normalizer"
    shutil.copytree(ASSESSMENT, root, ignore=shutil.ignore_patterns("results", "__pycache__"))
    return root


def _fake_run(monkeypatch, engine: FakeEngine):
    # the fake engine cannot execute Spark code: the transformer's run() is replaced by one that names itself
    import astra_verification.snowpark as module

    real = module._load_module

    def load(path):
        loaded = real(path)
        loaded.run = lambda session, inputs, _name=loaded.NAME: _name
        return loaded

    monkeypatch.setattr(module, "_load_module", load)


def test_the_assessment_has_two_transformers_with_recorded_changes():
    assessment = load_assessment(ASSESSMENT)
    assert [t.name for t in assessment.transformers] == ["drip_split", "position_normalizer"]
    drip, position = assessment.transformers
    assert position.inputs == {"positions": "gcus_positions.dat"} and position.effort_hours == 3.0 and len(position.changes) == 3
    assert drip.changes[2].kind == "api_gap" and "explode" in drip.changes[2].what and drip.effort_hours == 4.0
    assert all(c.why for t in assessment.transformers for c in t.changes)
    diff, added, removed = code_diff(position)
    assert diff.startswith("--- position_normalizer/original.py") and "sparkContext.textFile" in diff and added > 10 and removed > 10


def test_a_run_records_result_effort_and_changes_and_the_memo_carries_them(copy, monkeypatch):
    engine = FakeEngine({"position_normalizer": EXPECTED_POSITIONS, "drip_split": EXPECTED_DRIP})
    _fake_run(monkeypatch, engine)
    assessment = load_assessment(copy)
    ticks = iter(range(0, 1000, 3))
    run = run_assessment(assessment, engine, clock=lambda: float(next(ticks)), now=lambda: datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc))
    assert run.status == "succeeded" and run.engine == "snowpark-connect" and run.run_id.startswith("20260908T090000Z-")
    assert engine.started == 1 and engine.stopped == 1 and run.session_seconds == 3.0
    drip, position = run.results
    assert (position.transformer, position.status, position.rows, position.parity, position.mismatches) == ("position_normalizer", "succeeded", 3, True, 0)
    assert (drip.changes, drip.effort_hours, drip.lines_added > 0, drip.lines_removed > 0) == (3, 4.0, True, True)
    assert position.sample[0] == ["12345678", "037833100", "100.00000", "227.120000", "2026-08-29"]  # sorted rows, first five
    result_file = copy / "results" / f"snowpark-connect-{run.run_id}.json"
    record = json.loads(result_file.read_text(encoding="utf-8"))
    assert record["status"] == "succeeded" and record["results"][1]["parity"] is True and record["environment"]["python"]

    memo = update_memo(assessment)
    evidence = memo[memo.index(EVIDENCE_START) : memo.index(EVIDENCE_END)]
    assert "| Transformer | Engine | Run | Result | Rows | Parity with expected | Seconds | Changes for Snowpark Connect | Effort (h) | Code delta |" in evidence
    assert f"| `position_normalizer` | snowpark-connect | {run.run_id} (2026-09-08T09:00:00Z) | succeeded | 3 | yes | 3 | 3 | 3 | +" in evidence
    assert "| `position_normalizer` | local | not run | | | | | 3 | 3 | |" in evidence
    assert "**drip_split**, 4 hours recorded by platform architect:" in evidence and "- api_gap: The split (rdd.flatMap over a Python function) rewritten as explode" in evidence
    assert "- unchanged: The output schema." in evidence
    assert memo.count(EVIDENCE_START) == 1 and "## What the evidence does not say" in memo  # the rest of the memo is untouched
    assert [r.run_id for r in load_runs(assessment)] == [run.run_id]


def test_a_failed_transformer_and_a_parity_miss_are_recorded_not_hidden(copy, monkeypatch):
    wrong = [list(r) for r in EXPECTED_DRIP]
    wrong[3][6] = "102625.00"
    engine = FakeEngine({"position_normalizer": EXPECTED_POSITIONS, "drip_split": wrong}, fail="position_normalizer")
    _fake_run(monkeypatch, engine)
    assessment = load_assessment(copy)
    run = run_assessment(assessment, engine)
    drip, position = run.results
    assert position.status == "failed" and position.error == "RuntimeError: SnowparkConnectError: function EXPLODE is not supported in this context" and run.status == "failed"
    assert drip.status == "succeeded" and drip.parity is False and drip.mismatches == 1
    evidence = evidence_section(assessment, [run])
    assert "| failed: RuntimeError: SnowparkConnectError: function EXPLODE is not supported in this context |" in evidence
    assert "| no (1 mismatch) |" in evidence


def test_the_latest_run_per_engine_wins_and_older_runs_stay(copy, monkeypatch):
    engine = FakeEngine({"position_normalizer": EXPECTED_POSITIONS, "drip_split": EXPECTED_DRIP})
    _fake_run(monkeypatch, engine)
    assessment = load_assessment(copy)
    first = run_assessment(assessment, engine, now=lambda: datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc))
    second = run_assessment(assessment, engine, transformers=["drip_split"], now=lambda: datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc))
    runs = load_runs(assessment)
    assert [r.run_id for r in runs] == sorted([first.run_id, second.run_id])
    evidence = evidence_section(assessment, runs)
    assert f"| `drip_split` | snowpark-connect | {second.run_id}" in evidence and f"| `position_normalizer` | snowpark-connect | {first.run_id}" in evidence
    with pytest.raises(ValueError, match="unknown transformer; the assessment has drip_split, position_normalizer"):
        run_assessment(assessment, engine, transformers=["nope"])


def test_the_memo_must_carry_the_evidence_markers(copy, monkeypatch):
    (copy / "memo.md").write_text("# memo without markers\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs the markers"):
        update_memo(load_assessment(copy))


def test_a_transformer_without_recorded_changes_is_refused(copy):
    (copy / "transformers" / "drip_split" / "changes.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="every transformer records its changes"):
        load_assessment(copy)


def test_cli_runs_an_engine_and_rewrites_the_memo(copy, monkeypatch, capsys):
    engine = FakeEngine({"position_normalizer": EXPECTED_POSITIONS, "drip_split": EXPECTED_DRIP})
    _fake_run(monkeypatch, engine)
    import astra_verification.cli as cli

    monkeypatch.setattr(cli, "SparkEngine", lambda name: engine)
    assert main(["snowpark", "assess", str(copy), "--engine", "snowpark-connect"]) == 0
    out = capsys.readouterr().out
    assert "snowpark-connect: session started in" in out and "position_normalizer    succeeded    3 rows" in out and "parity with expected" in out
    assert "the memo's evidence rewritten" in out
    assert "| `drip_split` | snowpark-connect |" in (copy / "memo.md").read_text(encoding="utf-8")
    assert main(["snowpark", "assess", str(copy), "--engine", "snowpark-connect", "--transformer", "nope"]) == 2
    assert "unknown transformer" in capsys.readouterr().err


# ------------------------------------------------------- the real thing


def _java_available() -> bool:
    if shutil.which("java"):
        return True
    home = os.environ.get("JAVA_HOME")
    return bool(home) and (Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")).exists()


pyspark = pytest.importorskip("pyspark", reason="the local engine needs pyspark (astra-verification[spark])") if _java_available() else None


@pytest.mark.skipif(pyspark is None, reason="the local engine needs pyspark and a Java runtime")
def test_both_transformers_run_on_a_local_spark_session_with_parity(copy):
    home = os.environ.get("JAVA_HOME")
    if home:
        os.environ["PATH"] = str(Path(home) / "bin") + os.pathsep + os.environ.get("PATH", "")
    assessment = load_assessment(copy)
    run = run_assessment(assessment, SparkEngine("local"))
    assert run.status == "succeeded", [r.error for r in run.results]
    assert all(r.parity is True for r in run.results), [(r.transformer, r.mismatches, r.sample) for r in run.results]
    assert {r.transformer: r.rows for r in run.results} == {"position_normalizer": 3, "drip_split": 4}
    assert run.environment["pyspark"].startswith("3.5")
    memo = update_memo(assessment)
    assert "| `drip_split` | local |" in memo and "| yes |" in memo
