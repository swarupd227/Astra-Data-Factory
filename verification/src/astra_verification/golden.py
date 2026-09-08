"""Golden datasets: the legacy path replayed on historical files, its outputs and rejections captured as hashed, versioned, read-only datasets (S4.1.2, ADR 0031).

A capture file (`golden/<custodian>/capture.yaml`) says where a
custodian's historical files are, how a business day's files are replayed
through Splitter/Loader in non-production, and which queries read the
outputs and the rejections back. `astra-verify golden capture` walks the
business days of a range and, for each: lists and hashes the day's files,
runs the replay, reads every output back in a fixed order, and writes the
dataset to the golden store as one version: the source file list with
hashes, one CSV per output, the replay log and a manifest whose hash is
the hash of everything in it. A day captured again with identical content
adds no version; different content adds the next version and never
touches the previous one. The store refuses to overwrite, and the golden
bucket's object lock makes what was written read-only. The repository
keeps an index per custodian with every version's hash and reference, so
a parity run names the oracle it used.
"""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

SCHEMA = "golden-capture-v0.schema.json"
CAPTURE_FILE = "capture.yaml"
INDEX_FILE = "datasets.json"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_DAYS = 30
MAX_DAYS = 60
QUERY_PLACEHOLDERS = frozenset({"business_date", "yyyymmdd", "file_names"})
REPLAY_PLACEHOLDERS = frozenset({"files", "file_list", "business_date", "yyyymmdd", "work_dir"})


# -- the capture file --------------------------------------------------------


@dataclass(frozen=True)
class Capture:
    custodian: str
    description: str
    sources: tuple[str, ...]
    business_days: tuple[str, ...]
    files_location: str
    files_pattern: str
    replay: tuple[str, ...]
    connection_env: str
    outputs: dict[str, str]
    rejections: str
    path: Path

    def is_business_day(self, day: date) -> bool:
        return DAY_NAMES[day.weekday()] in self.business_days

    def business_days_between(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        current = start
        while current <= end:
            if self.is_business_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days


def load_capture(path: Path, root: Path | None = None) -> tuple[Capture | None, list[Problem]]:
    path = Path(path)
    display = display_path(path, root)
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    if data.get("capture_version") != 0:
        line = line_of(data, ["capture_version"]) if "capture_version" in data else 1
        return None, [Problem(display, line, f"capture_version must be 0; found {data.get('capture_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_verification.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)
    legacy = data["legacy"]
    for i, arg in enumerate(legacy["replay"]):
        for name in re.findall(r"\{([a-z_]+)\}", arg):
            if name not in REPLAY_PLACEHOLDERS:
                problems.append(Problem(display, line_of(data, ["legacy", "replay", i]), f"legacy.replay[{i}]: unknown placeholder {{{name}}}; placeholders are {', '.join('{' + p + '}' for p in sorted(REPLAY_PLACEHOLDERS))}"))
    for name, sql in legacy["outputs"].items():
        if not re.search(r"\bORDER\s+BY\b", sql, re.IGNORECASE):
            problems.append(Problem(display, line_of(data, ["legacy", "outputs", name]), f"legacy.outputs.{name}: the query must ORDER BY, so that a capture is reproducible and its hash means something"))
        for placeholder in re.findall(r"\{([a-z_]+)\}", sql):
            if placeholder not in QUERY_PLACEHOLDERS:
                problems.append(Problem(display, line_of(data, ["legacy", "outputs", name]), f"legacy.outputs.{name}: unknown placeholder {{{placeholder}}}; placeholders are {', '.join('{' + p + '}' for p in sorted(QUERY_PLACEHOLDERS))}"))
    rejections = legacy.get("rejections", "rejections")
    if rejections not in legacy["outputs"]:
        problems.append(Problem(display, line_of(data, ["legacy", "outputs"]), f"legacy.outputs has no '{rejections}' query; the rejections are what parity with the Loader is measured on"))
    if "=" in legacy["connection_env"] or ";" in legacy["connection_env"]:
        problems.append(Problem(display, line_of(data, ["legacy", "connection_env"]), "legacy.connection_env must name an environment variable, not hold the connection string"))
    if "{yyyymmdd}" not in data["files"]["pattern"] and "{yyyy}" not in data["files"]["pattern"]:
        problems.append(Problem(display, line_of(data, ["files", "pattern"]), "files.pattern must carry a date token ({yyyymmdd}, or {yyyy} with {mm} and {dd}) so each business day's files are its own"))
    if problems:
        return None, problems
    return (
        Capture(
            custodian=data["custodian"],
            description=" ".join(str(data.get("description", "")).split()),
            sources=tuple(data["sources"]),
            business_days=tuple(data.get("business_days") or ["mon", "tue", "wed", "thu", "fri"]),
            files_location=data["files"]["location"],
            files_pattern=data["files"]["pattern"],
            replay=tuple(legacy["replay"]),
            connection_env=legacy["connection_env"],
            outputs=dict(legacy["outputs"]),
            rejections=rejections,
            path=path,
        ),
        [],
    )


def discover(golden_dir: Path) -> list[Path]:
    return sorted(p for p in Path(golden_dir).glob(f"*/{CAPTURE_FILE}") if p.is_file())


# -- where things are --------------------------------------------------------


class FileSource(Protocol):
    """The historical files of a custodian."""

    def list(self, pattern: str) -> list[str]: ...
    def read(self, name: str) -> bytes: ...
    def path(self, name: str, work_dir: Path) -> Path: ...


class LocalFiles:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def list(self, pattern: str) -> list[str]:
        return sorted(p.name for p in self.directory.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, pattern))

    def read(self, name: str) -> bytes:
        return (self.directory / name).read_bytes()

    def path(self, name: str, work_dir: Path) -> Path:
        return self.directory / name


class S3Files:
    """Historical files under s3://bucket/prefix; needs boto3 (astra-verification[golden])."""

    def __init__(self, uri: str) -> None:
        self.bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        self.prefix = prefix.strip("/")
        import boto3  # type: ignore[import-not-found]

        self.client = boto3.client("s3")

    def list(self, pattern: str) -> list[str]:
        names: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{self.prefix}/" if self.prefix else ""):
            for obj in page.get("Contents", []):
                name = obj["Key"].rsplit("/", 1)[-1]
                if fnmatch.fnmatch(name, pattern):
                    names.append(name)
        return sorted(names)

    def read(self, name: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}/{name}" if self.prefix else name)["Body"].read()

    def path(self, name: str, work_dir: Path) -> Path:
        local = Path(work_dir) / "files" / name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.read(name))
        return local


def file_source(location: str) -> FileSource:
    return S3Files(location) if location.startswith("s3://") else LocalFiles(Path(location))


class ObjectStore(Protocol):
    """The golden store: write once, read many."""

    uri: str

    def exists(self, key: str) -> bool: ...
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...


class StoreConflict(RuntimeError):
    """A key exists; the store never overwrites."""


class LocalStore:
    """A directory; every file written is made read-only. For tests and for a capture before the bucket exists."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.uri = self.root.resolve().as_uri()

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path.exists():
            raise StoreConflict(f"{key} exists in {self.uri}; golden datasets are never overwritten")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())


class S3Store:
    """s3://bucket/prefix with conditional writes; the bucket's object lock keeps what was written. Needs boto3."""

    def __init__(self, uri: str) -> None:
        self.uri = uri.rstrip("/")
        self.bucket, _, prefix = self.uri.removeprefix("s3://").partition("/")
        self.prefix = prefix.strip("/")
        import boto3  # type: ignore[import-not-found]

        self.client = boto3.client("s3")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except self.client.exceptions.ClientError:
            return False

    def put(self, key: str, data: bytes) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, IfNoneMatch="*")
        except self.client.exceptions.ClientError as exc:
            if "PreconditionFailed" in str(exc) or "412" in str(exc):
                raise StoreConflict(f"{key} exists in {self.uri}; golden datasets are never overwritten") from exc
            raise

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        full = self._key(prefix)
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"].removeprefix(f"{self.prefix}/" if self.prefix else ""))
        return sorted(keys)


def object_store(uri: str) -> ObjectStore:
    return S3Store(uri) if uri.startswith("s3://") else LocalStore(Path(uri))


class LegacyStore(Protocol):
    """The non-production Loader database the replay writes to."""

    def query(self, sql: str) -> tuple[list[str], list[tuple]]: ...


class OdbcLegacyStore:
    """SQL Server through pyodbc (astra-verification[legacy])."""

    def __init__(self, connection_string: str) -> None:
        import pyodbc  # type: ignore[import-not-found]

        self.connection = pyodbc.connect(connection_string)

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        cursor = self.connection.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description]
        return columns, [tuple(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str


Runner = Callable[[list[str], Path, dict[str, str]], CommandResult]


def subprocess_runner(argv: list[str], cwd: Path, env: dict[str, str]) -> CommandResult:
    completed = subprocess.run(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    return CommandResult(completed.returncode, completed.stdout)


# -- datasets ----------------------------------------------------------------


def dataset_prefix(custodian: str, day: date, version: int | None = None) -> str:
    base = f"{custodian}/{day.isoformat()}"
    return base if version is None else f"{base}/v{version}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows_to_csv(columns: list[str], rows: list[tuple]) -> bytes:
    """Deterministic CSV: header, then the rows in the order the query returned them; None is empty, everything else str()."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else v.isoformat() if isinstance(v, (date, datetime)) else str(v) for v in row])
    return buffer.getvalue().encode("utf-8")


def dataset_hash(files: dict[str, str]) -> str:
    """The hash of a dataset: over its file names and their hashes, in name order."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\n")
    return digest.hexdigest()


@dataclass
class DayCapture:
    custodian: str
    business_date: date
    version: int
    hash: str
    status: str  # captured, unchanged, no_files, replay_failed
    sources: list[dict] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # dataset file -> sha256
    store_ref: str = ""
    captured_at: str = ""
    replay_exit: int | None = None
    detail: str = ""

    def to_index_entry(self) -> dict:
        return {
            "business_date": self.business_date.isoformat(),
            "version": self.version,
            "hash": self.hash,
            "captured_at": self.captured_at,
            "store": self.store_ref,
            "source_files": [s["name"] for s in self.sources],
            "rows": self.rows,
        }


def existing_versions(store: ObjectStore, custodian: str, day: date) -> list[tuple[int, dict]]:
    """Every version's manifest for the day, oldest first."""
    versions: list[tuple[int, dict]] = []
    for key in store.list(dataset_prefix(custodian, day)):
        match = re.fullmatch(re.escape(dataset_prefix(custodian, day)) + r"/v(\d+)/manifest\.json", key)
        if match:
            versions.append((int(match.group(1)), json.loads(store.get(key).decode("utf-8"))))
    return sorted(versions, key=lambda v: v[0])


def capture_day(
    capture: Capture,
    day: date,
    *,
    files: FileSource,
    store: ObjectStore,
    legacy: LegacyStore,
    runner: Runner,
    environ: dict[str, str],
    work_dir: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> DayCapture:
    """One business day: files hashed, replayed, outputs read back and written as the next version if anything changed."""
    pattern = capture.files_pattern.format(yyyymmdd=day.strftime("%Y%m%d"), yyyy=day.strftime("%Y"), mm=day.strftime("%m"), dd=day.strftime("%d"))
    names = files.list(pattern)
    if not names:
        return DayCapture(capture.custodian, day, 0, "", "no_files", detail=f"no file matches {pattern} in {capture.files_location}")
    day_dir = Path(work_dir) / capture.custodian / day.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    paths = []
    for name in names:
        data = files.read(name)
        sources.append({"name": name, "sha256": _sha(data), "bytes": len(data)})
        paths.append(files.path(name, day_dir))
    file_list = day_dir / "files.txt"
    file_list.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    values = {"files": " ".join(str(p) for p in paths), "file_list": str(file_list), "business_date": day.isoformat(), "yyyymmdd": day.strftime("%Y%m%d"), "work_dir": str(day_dir)}
    argv = [a.format(**values) for a in capture.replay]
    started = clock()
    result = runner(argv, day_dir, environ)
    log = f"# {capture.custodian} {day.isoformat()} replay, started {started.replace(microsecond=0).isoformat()}\n# {' '.join(argv)}\n# exit {result.exit_code}\n\n{result.output}"
    if result.exit_code != 0:
        (day_dir / "replay.log").write_text(log, encoding="utf-8", newline="\n")
        return DayCapture(capture.custodian, day, 0, "", "replay_failed", sources=sources, replay_exit=result.exit_code, detail=f"replay exited {result.exit_code}; see {day_dir / 'replay.log'}")

    contents: dict[str, bytes] = {"sources.json": json.dumps({"business_date": day.isoformat(), "files": sources}, indent=2, sort_keys=True).encode("utf-8") + b"\n", "replay.log": log.encode("utf-8")}
    rows: dict[str, int] = {}
    file_names = ", ".join("'" + n.replace("'", "''") + "'" for n in names)
    for output, sql in capture.outputs.items():
        columns, data = legacy.query(sql.format(business_date=day.isoformat(), yyyymmdd=day.strftime("%Y%m%d"), file_names=file_names))
        contents[f"outputs/{output}.csv"] = rows_to_csv(columns, data)
        rows[output] = len(data)
    hashes = {name: _sha(data) for name, data in contents.items()}
    digest = dataset_hash(hashes)

    versions = existing_versions(store, capture.custodian, day)
    if versions and versions[-1][1]["hash"] == digest:
        number, manifest = versions[-1]
        return DayCapture(capture.custodian, day, number, digest, "unchanged", sources, rows, dict(manifest["files"]), f"{store.uri}/{dataset_prefix(capture.custodian, day, number)}", manifest["captured_at"], result.exit_code, "identical to the latest version; nothing written")
    number = (versions[-1][0] + 1) if versions else 1
    captured_at = clock().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = {
        "custodian": capture.custodian,
        "sources": list(capture.sources),
        "business_date": day.isoformat(),
        "version": number,
        "hash": digest,
        "captured_at": captured_at,
        "supersedes": versions[-1][1]["hash"] if versions else None,
        "replay": argv,
        "source_files": sources,
        "rows": rows,
        "rejections_output": capture.rejections,
        "files": hashes,
    }
    prefix = dataset_prefix(capture.custodian, day, number)
    for name, data in contents.items():
        store.put(f"{prefix}/{name}", data)
    store.put(f"{prefix}/manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return DayCapture(capture.custodian, day, number, digest, "captured", sources, rows, hashes, f"{store.uri}/{prefix}", captured_at, result.exit_code)


# -- the index in the repository ---------------------------------------------


def load_index(golden_dir: Path, custodian: str) -> dict:
    path = Path(golden_dir) / custodian / INDEX_FILE
    if not path.is_file():
        return {"custodian": custodian, "datasets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def record(golden_dir: Path, captures: Iterable[DayCapture]) -> dict[str, int]:
    """Append every captured version to the custodian's index; an entry is never rewritten."""
    added: dict[str, int] = {}
    by_custodian: dict[str, list[DayCapture]] = {}
    for c in captures:
        if c.status == "captured":
            by_custodian.setdefault(c.custodian, []).append(c)
    for custodian, days in by_custodian.items():
        index = load_index(golden_dir, custodian)
        known = {(d["business_date"], d["version"]) for d in index["datasets"]}
        for c in days:
            if (c.business_date.isoformat(), c.version) not in known:
                index["datasets"].append(c.to_index_entry())
        index["datasets"].sort(key=lambda d: (d["business_date"], d["version"]))
        path = Path(golden_dir) / custodian / INDEX_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        added[custodian] = len(days)
    return added


@dataclass(frozen=True)
class Coverage:
    custodian: str
    days: int  # business dates with at least one version
    versions: int
    first: str | None
    last: str | None

    @property
    def within_range(self) -> bool:
        return MIN_DAYS <= self.days <= MAX_DAYS

    @property
    def verdict(self) -> str:
        if self.days < MIN_DAYS:
            return f"{self.days} of at least {MIN_DAYS} business days captured"
        if self.days > MAX_DAYS:
            return f"{self.days} business days captured, more than the {MAX_DAYS} the replay needs"
        return f"{self.days} business days captured, within {MIN_DAYS} to {MAX_DAYS}"


def coverage(golden_dir: Path, custodian: str) -> Coverage:
    index = load_index(golden_dir, custodian)
    dates = sorted({d["business_date"] for d in index["datasets"]})
    return Coverage(custodian, len(dates), len(index["datasets"]), dates[0] if dates else None, dates[-1] if dates else None)


def verify(golden_dir: Path, custodian: str, store: ObjectStore) -> list[Problem]:
    """Every indexed version exists in the store, its manifest hash is the hash of its files, and every file still hashes as the manifest says."""
    problems: list[Problem] = []
    index = load_index(golden_dir, custodian)
    where = f"golden/{custodian}/{INDEX_FILE}"
    for entry in index["datasets"]:
        prefix = dataset_prefix(custodian, date.fromisoformat(entry["business_date"]), entry["version"])
        if not store.exists(f"{prefix}/manifest.json"):
            problems.append(Problem(where, None, f"{prefix}: missing from {store.uri}"))
            continue
        manifest = json.loads(store.get(f"{prefix}/manifest.json").decode("utf-8"))
        if manifest["hash"] != entry["hash"]:
            problems.append(Problem(where, None, f"{prefix}: the index says {entry['hash'][:12]} but the manifest says {manifest['hash'][:12]}"))
        if dataset_hash(manifest["files"]) != manifest["hash"]:
            problems.append(Problem(where, None, f"{prefix}: the manifest's hash is not the hash of its files; the manifest was changed after capture"))
        for name, expected in manifest["files"].items():
            key = f"{prefix}/{name}"
            if not store.exists(key):
                problems.append(Problem(where, None, f"{key}: missing from {store.uri}"))
            elif _sha(store.get(key)) != expected:
                problems.append(Problem(where, None, f"{key}: content changed since capture (hash {_sha(store.get(key))[:12]}, manifest {expected[:12]})"))
    return problems


def check(golden_dir: Path, root: Path | None = None) -> tuple[list[Capture], list[Problem]]:
    """What a pull request can check: every capture file is valid and every index entry is well formed and unique."""
    captures: list[Capture] = []
    problems: list[Problem] = []
    for path in discover(golden_dir):
        capture, found = load_capture(path, root)
        problems.extend(found)
        if capture is None:
            continue
        if capture.custodian != path.parent.name:
            problems.append(Problem(display_path(path, root), None, f"custodian '{capture.custodian}' must match the directory '{path.parent.name}'"))
        captures.append(capture)
        index = load_index(golden_dir, capture.custodian)
        seen: set[tuple[str, int]] = set()
        for i, entry in enumerate(index["datasets"]):
            key = (entry.get("business_date"), entry.get("version"))
            where = display_path(path.parent / INDEX_FILE, root)
            if not all(k in entry for k in ("business_date", "version", "hash", "captured_at", "store", "source_files")):
                problems.append(Problem(where, None, f"datasets[{i}] lacks one of business_date, version, hash, captured_at, store, source_files"))
                continue
            if key in seen:
                problems.append(Problem(where, None, f"datasets[{i}]: {key[0]} v{key[1]} is listed twice"))
            seen.add(key)
            if not re.fullmatch(r"[0-9a-f]{64}", entry["hash"]):
                problems.append(Problem(where, None, f"datasets[{i}]: hash is not a sha256"))
            if not capture.is_business_day(date.fromisoformat(entry["business_date"])):
                problems.append(Problem(where, None, f"datasets[{i}]: {entry['business_date']} is not a business day of {capture.custodian}"))
    return captures, problems


def secrets_from(environ: dict[str, str], capture: Capture) -> str:
    value = environ.get(capture.connection_env)
    if not value:
        raise EnvironmentError(f"environment variable {capture.connection_env} not set; the capture file names it for the non-production Loader connection")
    return value
