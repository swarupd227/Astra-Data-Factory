"""Snowpark Connect assessment: run a Normalizer transformer on an engine, record effort and result, attach both to the memo (S3.3.2, ADR 0029).

An assessment directory (assessments/<decision>/) holds a memo, sample
inputs, expected outputs and one directory per transformer with the
legacy code (original.py), the version that runs on Snowpark Connect
(snowpark.py) and the changes the engineer recorded (changes.yaml). The
harness loads snowpark.py, hands it the sample lines as one-column
DataFrames on the chosen engine, times the run, compares the rows with
the expected output, and writes a result file; the memo's evidence
section is rewritten from the result files so the decision is scored on
what ran, not on what was hoped.

Engines: `local` is a local Spark session (a JVM on the runner), the
reference; `snowpark-connect` is Snowpark Connect for Spark, the same
DataFrame code running on Snowflake, the candidate.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import platform
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import yaml

ENGINES = ("local", "snowpark-connect")
EVIDENCE_START = "<!-- evidence:start -->"
EVIDENCE_END = "<!-- evidence:end -->"


# -- what is assessed --------------------------------------------------------


@dataclass(frozen=True)
class Change:
    kind: str
    what: str
    why: str
    effort_hours: float


@dataclass(frozen=True)
class Transformer:
    name: str
    directory: Path
    inputs: dict[str, str]  # input name -> sample file name
    engineer: str
    effort_hours: float
    changes: tuple[Change, ...]
    unchanged: tuple[str, ...]

    @property
    def original(self) -> Path:
        return self.directory / "original.py"

    @property
    def snowpark(self) -> Path:
        return self.directory / "snowpark.py"


@dataclass(frozen=True)
class Assessment:
    root: Path
    memo: Path
    transformers: tuple[Transformer, ...]

    @property
    def results_dir(self) -> Path:
        return self.root / "results"


def load_assessment(root: Path) -> Assessment:
    """The memo, and every transformer directory with a snowpark.py and a changes.yaml."""
    root = Path(root)
    memo = root / "memo.md"
    if not memo.is_file():
        raise FileNotFoundError(f"{memo}: the assessment has no memo.md")
    transformers: list[Transformer] = []
    for directory in sorted(p for p in (root / "transformers").iterdir() if p.is_dir()):
        snowpark = directory / "snowpark.py"
        changes_path = directory / "changes.yaml"
        if not snowpark.is_file():
            continue
        if not changes_path.is_file():
            raise FileNotFoundError(f"{changes_path}: every transformer records its changes, even when there are none")
        raw = yaml.safe_load(changes_path.read_text(encoding="utf-8")) or {}
        module = _load_module(snowpark)
        changes = tuple(Change(c["kind"], " ".join(str(c["what"]).split()), " ".join(str(c["why"]).split()), float(c.get("effort_hours", 0))) for c in raw.get("changes") or [])
        transformers.append(
            Transformer(
                name=getattr(module, "NAME", directory.name),
                directory=directory,
                inputs=dict(getattr(module, "INPUTS", {})),
                engineer=str(raw.get("engineer", "")),
                effort_hours=float(raw.get("effort_hours", sum(c.effort_hours for c in changes))),
                changes=changes,
                unchanged=tuple(" ".join(str(u).split()) for u in raw.get("unchanged") or []),
            )
        )
    if not transformers:
        raise FileNotFoundError(f"{root / 'transformers'}: no transformer directory with a snowpark.py")
    return Assessment(root, memo, tuple(transformers))


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"assessment_{path.parent.name}_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def code_diff(transformer: Transformer) -> tuple[str, int, int]:
    """Unified diff from the legacy transformer to the Snowpark Connect one, with lines added and removed."""
    before = transformer.original.read_text(encoding="utf-8").splitlines(keepends=True) if transformer.original.is_file() else []
    after = transformer.snowpark.read_text(encoding="utf-8").splitlines(keepends=True)
    diff = list(difflib.unified_diff(before, after, fromfile=f"{transformer.directory.name}/original.py", tofile=f"{transformer.directory.name}/snowpark.py"))
    added = sum(1 for line in diff[2:] if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff[2:] if line.startswith("-") and not line.startswith("---"))
    return "".join(diff), added, removed


# -- engines -----------------------------------------------------------------


class Engine(Protocol):
    name: str

    def start(self) -> object: ...
    def lines(self, session: object, lines: list[str]) -> object: ...
    def collect(self, frame: object) -> tuple[list[str], list[list[str | None]]]: ...
    def stop(self, session: object) -> None: ...


def _cell(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return repr(value)
    return str(value)


class SparkEngine:
    """A pyspark session: local[*] for the reference run, or Snowpark Connect for the candidate."""

    def __init__(self, name: str) -> None:
        if name not in ENGINES:
            raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
        self.name = name

    def start(self):
        if self.name == "local":
            from pyspark.sql import SparkSession

            return SparkSession.builder.master("local[1]").appName("astra-normalizer-assessment").config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1").getOrCreate()
        from snowflake import snowpark_connect  # type: ignore[import-not-found]

        snowpark_connect.start_session()
        return snowpark_connect.get_session()

    def lines(self, session, lines: list[str]):
        if self.name == "local":
            # A VALUES query keeps the local run inside the JVM: no Python worker is needed, so the reference
            # runs on any Python version the driver has. Snowpark Connect uploads the rows with createDataFrame.
            values = ", ".join("('" + line.replace("'", "''") + "')" for line in lines) or "('')"
            return session.sql(f"SELECT * FROM VALUES {values} AS t(line)")
        return session.createDataFrame([(line,) for line in lines], "line string")

    def collect(self, frame) -> tuple[list[str], list[list[str | None]]]:
        rows = frame.collect()
        return list(frame.columns), [[_cell(v) for v in row] for row in rows]

    def stop(self, session) -> None:
        if self.name == "local":
            session.stop()


# -- running -----------------------------------------------------------------


@dataclass
class TransformerResult:
    transformer: str
    status: str  # succeeded, failed
    seconds: float
    rows: int
    columns: list[str]
    sample: list[list[str | None]]
    parity: bool | None  # against expected/<name>.json; None when there is no expected file
    mismatches: int
    error: str | None
    changes: int
    effort_hours: float
    lines_added: int
    lines_removed: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class AssessmentRun:
    engine: str
    run_id: str
    started_at: str
    session_seconds: float
    environment: dict[str, str]
    results: list[TransformerResult] = field(default_factory=list)
    finished_at: str | None = None

    @property
    def status(self) -> str:
        return "succeeded" if self.results and all(r.status == "succeeded" for r in self.results) else "failed"

    def to_dict(self) -> dict:
        return {"engine": self.engine, "run_id": self.run_id, "status": self.status, "started_at": self.started_at, "finished_at": self.finished_at, "session_seconds": self.session_seconds, "environment": self.environment, "results": [r.to_dict() for r in self.results]}


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def expected_rows(assessment: Assessment, transformer: Transformer) -> list[list[str | None]] | None:
    path = assessment.root / "expected" / f"{transformer.name}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [[None if v is None else str(v) for v in row] for row in data["rows"]]


def _sorted(rows: list[list[str | None]]) -> list[list[str | None]]:
    return sorted(rows, key=lambda r: ["" if v is None else v for v in r])


def run_assessment(
    assessment: Assessment,
    engine: Engine,
    *,
    transformers: list[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sample_rows: int = 5,
) -> AssessmentRun:
    """Run every transformer (or the named ones) on the engine and write the result file. The memo is updated separately."""
    wanted = [t for t in assessment.transformers if transformers is None or t.name in set(transformers)]
    if transformers is not None and len(wanted) != len(set(transformers)):
        known = ", ".join(t.name for t in assessment.transformers)
        raise ValueError(f"unknown transformer; the assessment has {known}")
    started = now()
    run = AssessmentRun(engine.name, f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}", _stamp(started), 0.0, _environment())
    t0 = clock()
    session = engine.start()
    run.session_seconds = round(clock() - t0, 3)
    try:
        for transformer in wanted:
            run.results.append(_run_one(assessment, transformer, engine, session, clock, sample_rows))
    finally:
        engine.stop(session)
    run.finished_at = _stamp(now())
    assessment.results_dir.mkdir(parents=True, exist_ok=True)
    (assessment.results_dir / f"{engine.name}-{run.run_id}.json").write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return run


def _run_one(assessment: Assessment, transformer: Transformer, engine: Engine, session, clock, sample_rows: int) -> TransformerResult:
    diff, added, removed = code_diff(transformer)
    module = _load_module(transformer.snowpark)
    inputs = {}
    for name, sample in transformer.inputs.items():
        lines = (assessment.root / "samples" / sample).read_text(encoding="utf-8").splitlines()
        inputs[name] = engine.lines(session, lines)
    t0 = clock()
    try:
        columns, rows = engine.collect(module.run(session, inputs))
    except Exception as exc:  # the engine's own error is the evidence
        return TransformerResult(transformer.name, "failed", round(clock() - t0, 3), 0, [], [], None, 0, f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}", len(transformer.changes), transformer.effort_hours, added, removed)
    seconds = round(clock() - t0, 3)
    expected = expected_rows(assessment, transformer)
    parity: bool | None = None
    mismatches = 0
    if expected is not None:
        got, want = _sorted(rows), _sorted(expected)
        mismatches = sum(1 for a, b in zip(got, want) if a != b) + abs(len(got) - len(want))
        parity = mismatches == 0
    return TransformerResult(transformer.name, "succeeded", seconds, len(rows), columns, _sorted(rows)[:sample_rows], parity, mismatches, None, len(transformer.changes), transformer.effort_hours, added, removed)


def _environment() -> dict[str, str]:
    env = {"python": platform.python_version(), "platform": platform.platform()}
    try:
        import pyspark  # type: ignore[import-not-found]

        env["pyspark"] = pyspark.__version__
    except ImportError:
        pass
    try:
        from importlib.metadata import version

        env["snowpark_connect"] = version("snowpark-connect")
    except Exception:
        pass
    if os.environ.get("SNOWFLAKE_ACCOUNT_NAME"):
        env["snowflake_account"] = os.environ["SNOWFLAKE_ACCOUNT_NAME"]
    return env


# -- the memo ----------------------------------------------------------------


def load_runs(assessment: Assessment) -> list[AssessmentRun]:
    runs: list[AssessmentRun] = []
    if not assessment.results_dir.is_dir():
        return runs
    for path in sorted(assessment.results_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        run = AssessmentRun(data["engine"], data["run_id"], data["started_at"], data["session_seconds"], data["environment"], [TransformerResult(**r) for r in data["results"]], data.get("finished_at"))
        runs.append(run)
    return runs


def evidence_section(assessment: Assessment, runs: list[AssessmentRun]) -> str:
    """The evidence table the memo carries: per transformer and engine, the latest run, with the recorded changes and effort."""
    latest: dict[tuple[str, str], tuple[AssessmentRun, TransformerResult]] = {}
    for run in sorted(runs, key=lambda r: r.started_at):
        for result in run.results:
            latest[(result.transformer, run.engine)] = (run, result)
    out = ["Rewritten by `astra-verify snowpark assess` from `results/`; the latest run per transformer and engine. Do not edit by hand.", ""]
    out.append("| Transformer | Engine | Run | Result | Rows | Parity with expected | Seconds | Changes for Snowpark Connect | Effort (h) | Code delta |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for transformer in assessment.transformers:
        for engine in ENGINES:
            entry = latest.get((transformer.name, engine))
            if entry is None:
                out.append(f"| `{transformer.name}` | {engine} | not run | | | | | {len(transformer.changes)} | {transformer.effort_hours:g} | |")
                continue
            run, result = entry
            parity = "" if result.parity is None else ("yes" if result.parity else f"no ({result.mismatches} mismatch{'es' if result.mismatches != 1 else ''})")
            outcome = "succeeded" if result.status == "succeeded" else f"failed: {result.error}"
            out.append(f"| `{transformer.name}` | {engine} | {run.run_id} ({run.started_at}) | {outcome} | {result.rows} | {parity} | {result.seconds:g} | {result.changes} | {result.effort_hours:g} | +{result.lines_added} / -{result.lines_removed} lines |")
    out.append("")
    for transformer in assessment.transformers:
        out.append(f"**{transformer.name}**, {transformer.effort_hours:g} hours recorded by {transformer.engineer or 'the engineer'}:")
        out.append("")
        for change in transformer.changes:
            out.append(f"- {change.kind}: {change.what} ({change.effort_hours:g} h). {change.why[:1].upper() + change.why[1:]}.")
        if not transformer.changes:
            out.append("- No change was needed.")
        for item in transformer.unchanged:
            out.append(f"- unchanged: {item}.")
        out.append("")
    sessions = {run.engine: run for run in sorted(runs, key=lambda r: r.started_at)}
    if sessions:
        out.append("Session start, latest run per engine: " + "; ".join(f"{engine} {run.session_seconds:g} s ({', '.join(f'{k} {v}' for k, v in run.environment.items() if k in ('pyspark', 'snowpark_connect', 'snowflake_account'))})" for engine, run in sessions.items()) + ".")
        out.append("")
    return "\n".join(out)


def update_memo(assessment: Assessment) -> str:
    """Rewrite the memo's evidence section from the result files. Returns the new memo text."""
    text = assessment.memo.read_text(encoding="utf-8")
    if EVIDENCE_START not in text or EVIDENCE_END not in text:
        raise ValueError(f"{assessment.memo}: the memo needs the markers {EVIDENCE_START} and {EVIDENCE_END} around its evidence section")
    section = evidence_section(assessment, load_runs(assessment))
    pattern = re.compile(re.escape(EVIDENCE_START) + r".*?" + re.escape(EVIDENCE_END), re.S)
    updated = pattern.sub(lambda _: f"{EVIDENCE_START}\n{section}\n{EVIDENCE_END}", text)
    assessment.memo.write_text(updated, encoding="utf-8", newline="\n")
    return updated
