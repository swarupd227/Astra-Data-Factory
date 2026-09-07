"""Historical migration with SnowConvert AI: the factory drives extract, convert, migrate and validate (S3.3.1, ADR 0028).

A migration file (`migrations/<id>.yaml`, schema migration-v0) names a
SQL Server source, the schemas that move, the archive-store schema they
land in, and how the SnowConvert AI CLI is invoked for each phase. The
factory runs the phases in order in a working directory, keeps every log,
turns the converted DDL into a release bundle whose single DDL step
creates the tables in the archive-store schema, deploys it when it has a
connection, and records the run, the tool version and the results with
the release under releases/<id>-migration/migration/runs/<run id>/.

Secrets (the connection string, the license key) come from environment
variables the file names; they are never written to the repository, and
the command lines in the logs show the variable name in their place.
"""

from __future__ import annotations

import glob as globbing
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_data.bundle import Bundle, Executor, Target, deploy, load_bundle

SCHEMA = "migration-v0.schema.json"
PHASES = ("extract", "convert", "migrate", "validate")
PLACEHOLDERS = frozenset({"connection", "license", "schemas", "source_server", "source_port", "source_database", "database", "target_schema", "work_dir", "extract_dir", "convert_dir", "migrate_dir", "validate_dir"})
DB = "{{ DATABASE }}"
EWI_MARKER = "!!!RESOLVE EWI!!!"
BUNDLE_SUFFIX = "-migration"


# -- the migration file ------------------------------------------------------


@dataclass(frozen=True)
class Source:
    platform: str
    server: str
    port: int
    database: str
    schemas: tuple[str, ...]
    connection_env: str


@dataclass(frozen=True)
class SnowConvert:
    command: tuple[str, ...]
    phases: dict[str, tuple[str, ...]]
    license_env: str | None = None
    version_args: tuple[str, ...] = ("--version",)
    converted_ddl: str = "**/*.sql"


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    owner: dict[str, str]
    source: Source
    target_schema: str
    table_prefix: str
    snowconvert: SnowConvert
    path: Path

    @property
    def bundle_name(self) -> str:
        return f"{self.id.replace('_', '-')}{BUNDLE_SUFFIX}"


def discover(paths: Iterable[Path | str]) -> tuple[list[Path], list[Problem]]:
    files: list[Path] = []
    problems: list[Problem] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.yaml") if p.is_file() and p.name != "README.yaml"))
        elif path.is_file():
            files.append(path)
        else:
            problems.append(Problem(str(path), None, "no such file or directory"))
    return files, problems


def load_migration(path: Path, root: Path | None = None) -> tuple[Migration | None, list[Problem]]:
    """Parse and validate a migration file. Returns it only when clean."""
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
    if data.get("migration_version") != 0:
        line = line_of(data, ["migration_version"]) if "migration_version" in data else 1
        return None, [Problem(display, line, f"migration_version must be 0; found {data.get('migration_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_data.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)
    sc = data["snowconvert"]
    for phase, argv in sc["phases"].items():
        for i, arg in enumerate(argv):
            for name in re.findall(r"\{([a-z_]+)\}", arg):
                if name not in PLACEHOLDERS:
                    problems.append(Problem(display, line_of(data, ["snowconvert", "phases", phase, i]), f"snowconvert.phases.{phase}[{i}]: unknown placeholder {{{name}}}; placeholders are {', '.join('{' + p + '}' for p in sorted(PLACEHOLDERS))}"))
                if name == "license" and not sc.get("license_env"):
                    problems.append(Problem(display, line_of(data, ["snowconvert", "phases", phase, i]), f"snowconvert.phases.{phase}[{i}] uses {{license}} but snowconvert.license_env names no variable"))
    for key in ("connection_env",):
        if _looks_like_a_value(data["source"][key]):
            problems.append(Problem(display, line_of(data, ["source", key]), f"source.{key} must name an environment variable, not hold the connection string"))
    if problems:
        return None, problems
    src = data["source"]
    migration = Migration(
        id=data["id"],
        description=" ".join(data["description"].split()),
        owner=dict(data["owner"]),
        source=Source(src["platform"], src["server"], int(src.get("port", 1433)), src["database"], tuple(src["schemas"]), src["connection_env"]),
        target_schema=data["target"]["schema"],
        table_prefix=data["target"].get("table_prefix", ""),
        snowconvert=SnowConvert(
            command=tuple(sc["command"]),
            phases={phase: tuple(argv) for phase, argv in sc["phases"].items()},
            license_env=sc.get("license_env"),
            version_args=tuple(sc.get("version_args", ["--version"])),
            converted_ddl=sc.get("converted_ddl", "**/*.sql"),
        ),
        path=path,
    )
    return migration, []


def _looks_like_a_value(text: str) -> bool:
    return "=" in text or ";" in text or "://" in text


def validate_paths(paths: Iterable[Path | str], root: Path | None = None) -> tuple[list[Migration], list[Problem]]:
    files, problems = discover(paths)
    migrations: list[Migration] = []
    for file in files:
        migration, found = load_migration(file, root)
        problems.extend(found)
        if migration is not None:
            migrations.append(migration)
    return migrations, problems


# -- running the tool --------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str


Runner = Callable[[list[str], Path, dict[str, str]], CommandResult]


def subprocess_runner(argv: list[str], cwd: Path, env: dict[str, str]) -> CommandResult:
    """Run the tool, merging stdout and stderr in order, as the log keeps them."""
    completed = subprocess.run(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    return CommandResult(completed.returncode, completed.stdout)


@dataclass
class PhaseResult:
    phase: str
    status: str  # succeeded, failed, skipped
    command: tuple[str, ...] = ()  # secrets shown as <VAR>
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    seconds: float | None = None
    log: str | None = None  # path relative to the bundle directory

    def to_dict(self) -> dict:
        return {"phase": self.phase, "status": self.status, "command": list(self.command), "exit_code": self.exit_code, "started_at": self.started_at, "finished_at": self.finished_at, "seconds": self.seconds, "log": self.log}


@dataclass
class ConvertedTable:
    source_schema: str
    source_name: str
    table: str  # name in the archive-store schema
    file: str
    ewis: int = 0

    def to_dict(self) -> dict:
        return {"source": f"{self.source_schema}.{self.source_name}", "table": self.table, "file": self.file, "ewis": self.ewis}


@dataclass
class MigrationRun:
    migration: Migration
    run_id: str
    started_at: str
    work_dir: Path
    tool_version: str = ""
    phases: list[PhaseResult] = field(default_factory=list)
    tables: list[ConvertedTable] = field(default_factory=list)
    ewis: int = 0
    deployed: bool = False
    bundle_dir: Path | None = None
    finished_at: str | None = None

    @property
    def status(self) -> str:
        if any(p.status == "failed" for p in self.phases):
            return "failed"
        if all(p.status == "succeeded" for p in self.phases) and self.phases:
            return "succeeded"
        return "partial"

    def to_dict(self) -> dict:
        return {
            "migration": self.migration.id,
            "migration_file": self.migration.path.name,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tool": {"command": list(self.migration.snowconvert.command), "version": self.tool_version},
            "source": {"platform": self.migration.source.platform, "server": self.migration.source.server, "database": self.migration.source.database, "schemas": list(self.migration.source.schemas)},
            "target": {"schema": self.migration.target_schema, "table_prefix": self.migration.table_prefix},
            "phases": [p.to_dict() for p in self.phases],
            "converted": {"tables": [t.to_dict() for t in self.tables], "ewis": self.ewis},
            "deployed": self.deployed,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id(now: datetime | None = None) -> str:
    moment = now or _now()
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def phase_argv(migration: Migration, phase: str, work_dir: Path, target: Target, secrets: dict[str, str]) -> tuple[list[str], list[str]]:
    """The command line for a phase, and the same line with secret values replaced by <VARIABLE> for the log."""
    values = {
        "connection": secrets[migration.source.connection_env],
        "license": secrets.get(migration.snowconvert.license_env or "", ""),
        "schemas": ",".join(migration.source.schemas),
        "source_server": migration.source.server,
        "source_port": str(migration.source.port),
        "source_database": migration.source.database,
        "database": target.database,
        "target_schema": migration.target_schema,
        "work_dir": str(work_dir),
        **{f"{p}_dir": str(work_dir / p) for p in PHASES},
    }
    shown = dict(values, connection=f"<{migration.source.connection_env}>", license=f"<{migration.snowconvert.license_env}>" if migration.snowconvert.license_env else "")
    args = list(migration.snowconvert.command) + [a.format(**values) for a in migration.snowconvert.phases[phase]]
    masked = list(migration.snowconvert.command) + [a.format(**shown) for a in migration.snowconvert.phases[phase]]
    return args, masked


def run_migration(
    migration: Migration,
    target: Target,
    work_root: Path,
    releases_dir: Path,
    *,
    phases: Iterable[str] = PHASES,
    runner: Runner = subprocess_runner,
    environ: dict[str, str] | None = None,
    executor: Executor | None = None,
    repo_root: Path | None = None,
    clock: Callable[[], datetime] = _now,
    tool_override: list[str] | None = None,
) -> MigrationRun:
    """Run the requested phases in order, stop at the first failure, and store the results with the release.

    The converted DDL becomes the bundle's DDL step after the convert phase; with an
    executor the bundle is deployed before the migrate phase, so the tables the data
    migration fills exist.
    """
    environ = dict(os.environ if environ is None else environ)
    wanted = [p for p in PHASES if p in set(phases)]
    if not wanted:
        raise ValueError(f"no phase to run; phases are {', '.join(PHASES)}")
    missing = [migration.source.connection_env] if migration.source.connection_env not in environ else []
    if migration.snowconvert.license_env and migration.snowconvert.license_env not in environ:
        missing.append(migration.snowconvert.license_env)
    if missing:
        raise EnvironmentError(f"environment variable{'s' if len(missing) > 1 else ''} {', '.join(missing)} not set; the migration file names {'them' if len(missing) > 1 else 'it'} for the secret{'s' if len(missing) > 1 else ''} the tool needs")
    if tool_override:
        migration = Migration(**{**migration.__dict__, "snowconvert": SnowConvert(**{**migration.snowconvert.__dict__, "command": tuple(tool_override)})})

    started = clock()
    run_id = new_run_id(started)
    run = MigrationRun(migration, run_id, _stamp(started), Path(work_root) / migration.id / run_id)
    for phase in PHASES:
        (run.work_dir / phase).mkdir(parents=True, exist_ok=True)
    bundle_dir = Path(releases_dir) / migration.bundle_name
    run_dir = bundle_dir / "migration" / "runs" / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run.bundle_dir = bundle_dir

    # the tool's version, for the record
    version = runner(list(migration.snowconvert.command) + list(migration.snowconvert.version_args), run.work_dir, environ)
    lines = [line.strip() for line in version.output.splitlines() if line.strip()]
    run.tool_version = lines[-1] if lines else f"exit {version.exit_code}"  # the last line: the version, after any start-up noise

    secrets = {k: environ[k] for k in (migration.source.connection_env, migration.snowconvert.license_env) if k and k in environ}
    failed = False
    for phase in PHASES:
        if phase not in wanted or failed:
            run.phases.append(PhaseResult(phase, "skipped"))
            continue
        argv, masked = phase_argv(migration, phase, run.work_dir, target, secrets)
        began = clock()
        result = runner(argv, run.work_dir / phase, environ)
        ended = clock()
        log_path = run_dir / f"{phase}.log"
        log_path.write_text(
            f"# {migration.id} {phase}, run {run.run_id}, started {_stamp(began)}\n# {' '.join(masked)}\n# exit {result.exit_code} after {(ended - began).total_seconds():.1f}s\n\n{_redact(result.output, secrets)}",
            encoding="utf-8",
            newline="\n",
        )
        run.phases.append(PhaseResult(phase, "succeeded" if result.exit_code == 0 else "failed", tuple(masked), result.exit_code, _stamp(began), _stamp(ended), round((ended - began).total_seconds(), 3), f"migration/runs/{run.run_id}/{phase}.log"))
        if result.exit_code != 0:
            failed = True
            continue
        if phase == "convert":
            run.tables, run.ewis = collect_converted_ddl(migration, run.work_dir / "convert", bundle_dir)
            _copy_reports(run.work_dir / "convert", run_dir / "reports")
            write_bundle_files(migration, run, bundle_dir, repo_root)
            if executor is not None:
                deploy(load_bundle(bundle_dir, repo_root), target, executor)
                run.deployed = True
    run.finished_at = _stamp(clock())
    _write_run(run, run_dir, bundle_dir, repo_root)
    return run


def _redact(text: str, secrets: dict[str, str]) -> str:
    for name, value in secrets.items():
        if value:
            text = text.replace(value, f"<{name}>")
    return text


def _copy_reports(convert_dir: Path, reports_dir: Path) -> None:
    """Every non-SQL file the convert phase wrote (assessment and conversion reports) travels with the run."""
    for path in sorted(p for p in convert_dir.rglob("*") if p.is_file() and p.suffix.lower() != ".sql"):
        destination = reports_dir / path.relative_to(convert_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


# -- converted DDL into the archive-store schema -----------------------------


_OBJECT = re.compile(r'(?<![\w."])("?)(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\1\.("?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\3', re.IGNORECASE)
_CREATE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+)?(TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?", re.IGNORECASE)
_DROP_STATEMENT = re.compile(r"^\s*(CREATE\s+(?:OR\s+REPLACE\s+)?SCHEMA|USE\s+(?:SCHEMA|DATABASE)|CREATE\s+(?:OR\s+REPLACE\s+)?DATABASE)\b", re.IGNORECASE)


def archive_name(migration: Migration, source_schema: str, quoted: bool, name: str) -> str:
    """The table's name in the archive store: the source name (upper-cased when the tool left it unquoted), prefixed by its schema when several schemas move, and by the configured prefix."""
    base = name if quoted else name.upper()
    schema_part = f"{source_schema.upper()}_" if len(migration.source.schemas) > 1 else ""
    return f"{migration.table_prefix}{schema_part}{base}"


def created_objects(migration: Migration, text: str) -> list[tuple[str, str, bool]]:
    """The (schema, name as written, quoted) of every table or view a converted file creates in a moved schema."""
    schemas = {s.lower(): s for s in migration.source.schemas}
    found: list[tuple[str, str, bool]] = []
    for statement in _split_statements(text):
        head = _CREATE.match(statement)
        if not head:
            continue
        target = _OBJECT.search(statement[head.end() :])
        if target and target.group("schema").lower() in schemas:
            found.append((schemas[target.group("schema").lower()], target.group("name"), bool(target.group(3))))
    return found


def name_registry(migration: Migration, created: Iterable[tuple[str, str, bool]]) -> dict[tuple[str, str], str]:
    """Archive-store name per created object, so every reference to it resolves the same way however it is quoted."""
    return {(schema.lower(), name.lower()): archive_name(migration, schema, quoted, name) for schema, name, quoted in created}


def rewrite_ddl(migration: Migration, text: str, registry: dict[tuple[str, str], str] | None = None) -> tuple[str, list[tuple[str, str, str]]]:
    """Point every converted object at the archive-store schema. Returns the SQL and the (schema, source name, archive name) of each table or view created.

    A reference to an object created in this run takes the created object's name; any other
    reference to a moved schema follows the quoting rule on its own.
    """
    schemas = {s.lower(): s for s in migration.source.schemas}
    names = dict(name_registry(migration, created_objects(migration, text)))
    names.update(registry or {})

    def resolve(schema: str, quoted: bool, name: str) -> str:
        return names.get((schema.lower(), name.lower())) or archive_name(migration, schemas[schema.lower()], quoted, name)

    def replace(match: re.Match) -> str:
        schema = match.group("schema")
        if schema.lower() not in schemas:
            return match.group(0)
        return f'{DB}."{migration.target_schema}"."{resolve(schema, bool(match.group(3)), match.group("name"))}"'

    created: list[tuple[str, str, str]] = []
    kept: list[str] = []
    for statement in _split_statements(text):
        if _DROP_STATEMENT.match(statement):
            continue
        head = _CREATE.match(statement)
        if head:
            target = _OBJECT.search(statement[head.end() :])
            if target and target.group("schema").lower() in schemas:
                created.append((schemas[target.group("schema").lower()], target.group("name"), resolve(target.group("schema"), bool(target.group(3)), target.group("name"))))
        kept.append(_OBJECT.sub(replace, statement).strip())
    return "\n\n".join(kept) + ("\n" if kept else ""), created


def _split_statements(text: str) -> list[str]:
    """Statements separated by ';' outside quotes and comments; each keeps its terminator."""
    out: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    in_string = in_line_comment = in_block_comment = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            current.append(ch)
            if ch == "*" and nxt == "/":
                current.append(nxt)
                i += 1
                in_block_comment = False
        elif in_string:
            current.append(ch)
            if ch == "'":
                if nxt == "'":
                    current.append(nxt)
                    i += 1
                else:
                    in_string = False
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            current.append(ch)
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            current.append(ch)
        elif ch == "'":
            in_string = True
            current.append(ch)
        elif ch == ";":
            current.append(ch)
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        out.append(tail + ";")
    return [s for s in out if s.strip(" \n;")]


def collect_converted_ddl(migration: Migration, convert_dir: Path, bundle_dir: Path) -> tuple[list[ConvertedTable], int]:
    """Rewrite every converted DDL file into the bundle's DDL step and list the tables it creates."""
    files = sorted(Path(p) for p in globbing.glob(str(convert_dir / migration.snowconvert.converted_ddl), recursive=True) if Path(p).is_file())
    parts: list[str] = [
        f"-- Converted schema of {migration.source.database} ({', '.join(migration.source.schemas)}) from {migration.source.platform}, by SnowConvert AI, pointed at",
        f"-- the archive-store schema {migration.target_schema}. Rendered by astra-data migrate; the tool's output is under migration/runs/<run>/. Do not edit.",
        "",
    ]
    tables: list[ConvertedTable] = []
    ewis = 0
    texts = {file: file.read_text(encoding="utf-8", errors="replace") for file in files}
    registry = name_registry(migration, [obj for text in texts.values() for obj in created_objects(migration, text)])
    for file in files:
        text = texts[file]
        relative = file.relative_to(convert_dir).as_posix()
        count = text.count(EWI_MARKER)
        ewis += count
        sql, created = rewrite_ddl(migration, text, registry)
        if not sql.strip():
            continue
        parts.append(f"-- {relative}" + (f" ({count} EWI marker{'s' if count != 1 else ''} to resolve)" if count else ""))
        parts.append(sql)
        for schema, name, table in created:
            tables.append(ConvertedTable(schema, name, table, relative, count))
    ddl_dir = bundle_dir / "ddl"
    ddl_dir.mkdir(parents=True, exist_ok=True)
    (ddl_dir / f"archive_{migration.id}.sql").write_text("\n".join(parts), encoding="utf-8", newline="\n")
    return tables, ewis


def write_bundle_files(migration: Migration, run: MigrationRun, bundle_dir: Path, repo_root: Path | None) -> None:
    ddl_name = f"ddl/archive_{migration.id}.sql"
    tests_dir = bundle_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    names = ", ".join(f"('{t.table}')" for t in run.tables) or "('__none__')"
    (tests_dir / f"archive_{migration.id}_tables_exist.sql").write_text(
        "\n".join(
            [
                f"-- {migration.id}: every table the convert phase produced exists in the archive-store schema {migration.target_schema}. Returns the ones that do not.",
                'SELECT w."TABLE_NAME"',
                f'FROM (VALUES {names}) w ("TABLE_NAME")',
                f'WHERE w."TABLE_NAME" <> \'__none__\' AND NOT EXISTS (',
                f"  SELECT 1 FROM {DB}.INFORMATION_SCHEMA.TABLES t",
                f"  WHERE t.\"TABLE_SCHEMA\" = '{migration.target_schema}' AND t.\"TABLE_NAME\" = w.\"TABLE_NAME\");",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    ddl_text = (bundle_dir / ddl_name).read_text(encoding="utf-8")
    version = hashlib.sha256(ddl_text.encode("utf-8")).hexdigest()[:12]
    (bundle_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                f"# Historical migration {migration.id}: the converted schema of {migration.source.database} in the archive store.",
                f"# Written by astra-data migrate from migrations/{migration.path.name}; the version is a digest of the converted DDL. Do not edit.",
                f"bundle: {migration.bundle_name}",
                f'version: "{version}"',
                f"source: {migration.id}",
                "steps:",
                f"  - {ddl_name}",
                "tests:",
                "  - tests/*.sql",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def _write_run(run: MigrationRun, run_dir: Path, bundle_dir: Path, repo_root: Path | None) -> None:
    (run_dir / "run.json").write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    migration = run.migration
    files = {p.relative_to(bundle_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle_dir.rglob("*")) if p.is_file() and p.name != "PROVENANCE.json"}
    provenance = {
        "bundle": {"name": migration.bundle_name, "kind": "migration"},
        "inputs": {
            "migration": {"id": migration.id, "path": display_path(migration.path, repo_root), "sha256": hashlib.sha256(migration.path.read_bytes()).hexdigest()},
            "source": {"platform": migration.source.platform, "server": migration.source.server, "database": migration.source.database, "schemas": list(migration.source.schemas)},
            "tool": {"command": list(migration.snowconvert.command), "version": run.tool_version},
        },
        "latest_run": run.run_id,
        "runs": sorted(p.name for p in (bundle_dir / "migration" / "runs").iterdir() if p.is_dir()),
        "files": files,
    }
    (bundle_dir / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def plan(migration: Migration, target: Target, work_root: Path, phases: Iterable[str] = PHASES) -> list[tuple[str, list[str]]]:
    """The command lines a run would execute, with secrets shown as <VARIABLE>."""
    work_dir = Path(work_root) / migration.id / "<run id>"
    placeholders = {migration.source.connection_env: f"<{migration.source.connection_env}>"}
    if migration.snowconvert.license_env:
        placeholders[migration.snowconvert.license_env] = f"<{migration.snowconvert.license_env}>"
    return [(phase, phase_argv(migration, phase, work_dir, target, placeholders)[1]) for phase in PHASES if phase in set(phases)]


def load_run_bundle(bundle_dir: Path, repo_root: Path | None = None) -> Bundle:
    return load_bundle(bundle_dir, repo_root)
