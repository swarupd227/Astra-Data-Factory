"""Release bundles: how a rendered source is laid out, deployed and tested.

A bundle is a directory under `releases/` with a `manifest.yaml` (schema
`bundle-v0`), SQL steps that run in order, and SQL tests that return failing
rows. SQL is environment-neutral: it refers to the target through
placeholders such as `{{ DATABASE }}` that are filled in at deploy time, so
the same bundle moves through dev, qa, uat and prod unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator

from astra_core.problems import Problem
from astra_core.schema import describe_error, error_line
from astra_core.yamlsource import SourceError, load

MANIFEST_NAME = "manifest.yaml"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")
ENVIRONMENT_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")


class BundleError(ValueError):
    """A bundle cannot be used. Carries the problems found."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.format() for p in problems))


class DeployError(RuntimeError):
    """A deploy step or test failed to execute."""

    def __init__(self, bundle: str, step: str, cause: Exception) -> None:
        self.bundle = bundle
        self.step = step
        self.cause = cause
        super().__init__(f"{bundle}: step {step} failed: {cause}")


IDENTIFIER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,254}$")


@dataclass(frozen=True)
class Target:
    """Where a bundle is deployed. Names follow the foundation's naming rule.

    `database` and `warehouse` override the derived names. A sandbox (S1.2.3)
    uses them to point the same bundle at its own database and warehouse.
    """

    environment: str
    prefix: str = "ASTRA"
    database: str | None = None
    warehouse: str | None = None

    def __post_init__(self) -> None:
        if not ENVIRONMENT_PATTERN.match(self.environment):
            raise ValueError("environment must be 2 to 8 lower-case letters or digits, starting with a letter")
        if not PREFIX_PATTERN.match(self.prefix):
            raise ValueError("prefix must be upper-case letters and digits, starting with a letter")
        for name, value in (("database", self.database), ("warehouse", self.warehouse)):
            if value is not None and not IDENTIFIER_PATTERN.match(value):
                raise ValueError(f"{name} must be an unquoted upper-case Snowflake identifier")
        if self.database is None:
            object.__setattr__(self, "database", f"{self.prefix}_{self.environment.upper()}")

    @property
    def environment_database(self) -> str:
        """The shared environment database, whatever this target points at."""
        return f"{self.prefix}_{self.environment.upper()}"

    def parameters(self) -> dict[str, str]:
        tiers = {
            "WAREHOUSE_SIMPLE": f"{self.environment_database}_WH_SIMPLE",
            "WAREHOUSE_MEDIUM": f"{self.environment_database}_WH_MEDIUM",
            "WAREHOUSE_COMPLEX": f"{self.environment_database}_WH_COMPLEX",
        }
        if self.warehouse is not None:
            tiers = {key: self.warehouse for key in tiers}
        return {
            "ENVIRONMENT": self.environment,
            "PREFIX": self.prefix,
            "DATABASE": self.database,
            **tiers,
        }


KNOWN_PLACEHOLDERS = tuple(Target("dev").parameters())


@dataclass(frozen=True)
class Bundle:
    root: Path
    name: str
    version: str
    source: str
    steps: tuple[Path, ...]
    tests: tuple[Path, ...] = field(default_factory=tuple)

    def relative(self, file: Path) -> str:
        return file.relative_to(self.root).as_posix()


def render(text: str, target: Target) -> str:
    """Fill placeholders. Raises KeyError naming an unknown placeholder."""
    parameters = target.parameters()

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in parameters:
            raise KeyError(name)
        return parameters[name]

    return PLACEHOLDER.sub(replace, text)


def placeholders_in(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


_manifest_validator: Draft202012Validator | None = None


def _validator() -> Draft202012Validator:
    global _manifest_validator
    if _manifest_validator is None:
        schema = json.loads(resources.files("astra_data.schemas").joinpath("bundle-v0.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        _manifest_validator = Draft202012Validator(schema)
    return _manifest_validator


def load_bundle(root: Path, repo_root: Path | None = None) -> Bundle:
    """Read and check a bundle directory. Raises BundleError with every problem found."""
    root = Path(root)
    manifest_path = root / MANIFEST_NAME
    display = _display(manifest_path, repo_root)
    problems: list[Problem] = []

    if not manifest_path.is_file():
        raise BundleError([Problem(display, None, "bundle has no manifest.yaml")])

    try:
        manifest = load(manifest_path.read_text(encoding="utf-8"))
    except SourceError as exc:
        raise BundleError([Problem(display, exc.line, f"invalid YAML: {exc}")]) from exc
    if not isinstance(manifest, dict):
        raise BundleError([Problem(display, 1, "the manifest must contain a mapping at the top level")])

    schema_errors = sorted(_validator().iter_errors(manifest), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    if schema_errors:
        raise BundleError([Problem(display, error_line(manifest, e), describe_error(e)) for e in schema_errors])

    if manifest["bundle"] != root.name:
        problems.append(Problem(display, None, f"manifest bundle '{manifest['bundle']}' must match the directory name '{root.name}'"))

    steps: list[Path] = []
    for i, step in enumerate(manifest["steps"]):
        file = root / step
        if not file.is_file():
            problems.append(Problem(display, None, f"steps[{i}] '{step}' does not exist in the bundle"))
            continue
        unknown = sorted(placeholders_in(file.read_text(encoding="utf-8")) - set(KNOWN_PLACEHOLDERS))
        if unknown:
            problems.append(Problem(_display(file, repo_root), None, f"unknown placeholder{'s' if len(unknown) > 1 else ''} {', '.join('{{ ' + u + ' }}' for u in unknown)}; known placeholders are {', '.join(KNOWN_PLACEHOLDERS)}"))
        steps.append(file)

    tests: list[Path] = []
    for i, pattern in enumerate(manifest.get("tests") or []):
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
        if not matches:
            problems.append(Problem(display, None, f"tests[{i}] '{pattern}' matches no file in the bundle"))
        for file in matches:
            unknown = sorted(placeholders_in(file.read_text(encoding="utf-8")) - set(KNOWN_PLACEHOLDERS))
            if unknown:
                problems.append(Problem(_display(file, repo_root), None, f"unknown placeholder{'s' if len(unknown) > 1 else ''} {', '.join('{{ ' + u + ' }}' for u in unknown)}"))
        tests.extend(matches)

    if problems:
        raise BundleError(problems)

    return Bundle(root=root, name=manifest["bundle"], version=str(manifest["version"]), source=manifest["source"], steps=tuple(steps), tests=tuple(tests))


def discover_bundles(releases_dir: Path) -> list[Path]:
    releases_dir = Path(releases_dir)
    if not releases_dir.is_dir():
        return []
    return sorted(p for p in releases_dir.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file())


def check_bundles(releases_dir: Path, repo_root: Path | None = None) -> tuple[list[Bundle], list[Problem]]:
    bundles: list[Bundle] = []
    problems: list[Problem] = []
    for root in discover_bundles(releases_dir):
        try:
            bundles.append(load_bundle(root, repo_root))
        except BundleError as exc:
            problems.extend(exc.problems)
    return bundles, problems


# -- execution ---------------------------------------------------------------


class Executor(Protocol):
    """What deploy and test need from a database connection."""

    def execute_script(self, sql: str) -> None:
        """Run one or more statements."""

    def query(self, sql: str) -> list[tuple]:
        """Run one query and return its rows."""


@dataclass(frozen=True)
class DeployResult:
    bundle: str
    version: str
    target: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class TestResult:
    bundle: str
    test: str
    passed: bool
    failing_rows: int
    sample: tuple[tuple, ...] = ()

    @property
    def detail(self) -> str:
        if self.passed:
            return "no failing rows"
        shown = "; ".join(", ".join(str(v) for v in row) for row in self.sample)
        return f"{self.failing_rows} failing row{'s' if self.failing_rows != 1 else ''}" + (f": {shown}" if shown else "")


def deploy(bundle: Bundle, target: Target, executor: Executor) -> DeployResult:
    """Render every step, then run them in order. Nothing runs if any step fails to render."""
    rendered: list[tuple[str, str]] = []
    for step in bundle.steps:
        try:
            rendered.append((bundle.relative(step), render(step.read_text(encoding="utf-8"), target)))
        except KeyError as exc:
            raise BundleError([Problem(bundle.relative(step), None, f"unknown placeholder {{{{ {exc.args[0]} }}}}")]) from exc

    for name, sql in rendered:
        try:
            executor.execute_script(sql)
        except Exception as exc:  # the database's own error is the message
            raise DeployError(bundle.name, name, exc) from exc

    return DeployResult(bundle.name, bundle.version, target.database, tuple(name for name, _ in rendered))


def run_tests(bundle: Bundle, target: Target, executor: Executor, sample_size: int = 5) -> list[TestResult]:
    """Run every test query. A test passes when it returns no rows."""
    results: list[TestResult] = []
    for test in bundle.tests:
        name = bundle.relative(test)
        try:
            rows = executor.query(render(test.read_text(encoding="utf-8"), target))
        except KeyError as exc:
            raise BundleError([Problem(name, None, f"unknown placeholder {{{{ {exc.args[0]} }}}}")]) from exc
        except Exception as exc:
            raise DeployError(bundle.name, name, exc) from exc
        results.append(TestResult(bundle.name, name, len(rows) == 0, len(rows), tuple(tuple(r) for r in rows[:sample_size])))
    return results


def _display(path: Path, repo_root: Path | None) -> str:
    try:
        return path.resolve().relative_to((repo_root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
