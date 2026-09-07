"""Static check of the generated tests of every bundle: each parses as one Snowflake SELECT.

The generated tests run for real after a deploy (`astra-data test`); this
is what a pull request can check without an account: every test file, with
its placeholders filled for a stand-in environment, is a single SELECT
statement the Snowflake dialect parses. A renderer bug that leaves a
dangling comma or an unbalanced bracket fails here, on the file and line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astra_core.problems import Problem
from astra_data.bundle import Bundle, Target, check_bundles, render

LINT_TARGET = Target("lint")


@dataclass(frozen=True)
class LintResult:
    bundle: str
    tests: int


def lint_bundle(bundle: Bundle) -> tuple[LintResult, list[Problem]]:
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError
    except ImportError as exc:  # pragma: no cover - environment without the lint extra
        raise RuntimeError("bundles lint needs sqlglot; install astra-data[lint]") from exc

    problems: list[Problem] = []
    for test in bundle.tests:
        name = f"{bundle.name}/{bundle.relative(test)}"
        text = test.read_text(encoding="utf-8")
        try:
            sql = render(text, LINT_TARGET)
        except KeyError as exc:
            problems.append(Problem(name, None, f"unknown placeholder {{{{ {exc.args[0]} }}}}"))
            continue
        try:
            statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
        except ParseError as exc:
            problems.append(Problem(name, _line_of(exc), f"does not parse as Snowflake SQL: {_first_line(exc)}"))
            continue
        if len(statements) != 1:
            problems.append(Problem(name, None, f"a generated test is one statement; found {len(statements)}"))
        elif not isinstance(statements[0], exp.Select) and not isinstance(statements[0], (exp.Union, exp.Intersect, exp.Except)):
            problems.append(Problem(name, None, f"a generated test is a SELECT that returns the failing rows; found {type(statements[0]).__name__.upper()}"))
    return LintResult(bundle.name, len(bundle.tests)), problems


def lint_bundles(releases_dir: Path, repo_root: Path | None = None) -> tuple[list[LintResult], list[Problem]]:
    """Every bundle under the directory: the bundle contract, then the parse of every test."""
    bundles, problems = check_bundles(Path(releases_dir), repo_root=repo_root)
    if problems:
        return [], problems
    results: list[LintResult] = []
    for bundle in bundles:
        result, found = lint_bundle(bundle)
        results.append(result)
        problems.extend(found)
    return results, problems


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0].strip()


def _line_of(exc: Exception) -> int | None:
    errors = getattr(exc, "errors", None) or []
    for error in errors:
        line = error.get("line") if isinstance(error, dict) else None
        if line:
            return int(line)
    return None
