"""Config validation: schema first, then the references the schema cannot see.

Every problem is reported with file and line and in plain words so that it
can be shown on the pull request that introduced it. A config that passes
here is the input the compiler (S3.1.1) accepts; the compiler adds meaning,
this adds nothing but checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from astra_data.yamlsource import LineDict, SourceError, line_of, load

SCHEMA_FILES = {0: "config-v0.schema.json"}
CONFIG_SUFFIXES = (".yaml", ".yml")

IDENTIFIER_PATTERN = "^[a-z][a-z0-9_]{0,62}$"
DATE_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with one file."""

    path: str
    line: int | None
    message: str

    def format(self, style: str = "text") -> str:
        if style == "github":
            location = f"file={self.path}" + (f",line={self.line}" if self.line else "")
            return f"::error {location},title=Config validation::{self.message}"
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"


def load_schema(version: int = 0) -> dict:
    name = SCHEMA_FILES[version]
    return json.loads(resources.files("astra_data.schemas").joinpath(name).read_text(encoding="utf-8"))


_validators: dict[int, Draft202012Validator] = {}


def _validator(version: int) -> Draft202012Validator:
    if version not in _validators:
        schema = load_schema(version)
        Draft202012Validator.check_schema(schema)
        _validators[version] = Draft202012Validator(schema)
    return _validators[version]


def discover(paths: Iterable[Path | str]) -> tuple[list[Path], list[Problem]]:
    """Config files under the given files and directories, sorted, plus problems for missing paths."""
    files: list[Path] = []
    problems: list[Problem] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in CONFIG_SUFFIXES and not p.name.startswith("_")))
        elif path.is_file():
            files.append(path)
        else:
            problems.append(Problem(path.as_posix(), None, "no such file or directory"))
    return files, problems


def validate_paths(paths: Iterable[Path | str], root: Path | None = None) -> tuple[int, list[Problem]]:
    """Validate every config under the paths. Returns (files checked, problems)."""
    files, problems = discover(paths)
    for file in files:
        problems.extend(validate_config_file(file, root))
    return len(files), problems


def validate_config_file(path: Path, root: Path | None = None) -> list[Problem]:
    display = _display_path(path, root)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]

    try:
        data = load(text)
    except SourceError as exc:
        return [Problem(display, exc.line, f"invalid YAML: {exc}")]

    if not isinstance(data, dict):
        return [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]

    version = data.get("config_version")
    if version not in SCHEMA_FILES:
        known = ", ".join(str(v) for v in SCHEMA_FILES)
        line = line_of(data, ["config_version"]) if "config_version" in data else 1
        return [Problem(display, line, f"config_version must be one of {known}; found {version!r}")]

    problems = [
        Problem(display, _error_line(data, error), _describe(error))
        for error in sorted(_validator(version).iter_errors(data), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    ]
    if problems:
        return _dedupe(problems)
    return _dedupe(_reference_problems(data, path, display))


# -- describing schema errors ----------------------------------------------


def _path_text(path: Iterable[Any]) -> str:
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else (f".{step}" if text else str(step))
    return text or "top level"


def _error_line(data: Any, error: ValidationError) -> int | None:
    path = list(error.absolute_path)
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extras = _extra_keys(error)
        if extras:
            return line_of(data, path + [extras[0]])
    return line_of(data, path)


def _extra_keys(error: ValidationError) -> list[str]:
    allowed = set(error.schema.get("properties", {}))
    return sorted(k for k in error.instance if k not in allowed)


def _type_name(value: Any) -> str:
    return {dict: "mapping", list: "list", str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}.get(type(value), type(value).__name__)


def _describe(error: ValidationError) -> str:
    where = _path_text(error.absolute_path)
    kind = error.validator
    if kind == "required":
        missing = [name for name in error.validator_value if name not in error.instance]
        return f"{where}: missing required field{'s' if len(missing) > 1 else ''} {', '.join(missing)}"
    if kind == "additionalProperties":
        extras = _extra_keys(error)
        allowed = ", ".join(sorted(error.schema.get("properties", {})))
        return f"{where}: unknown field{'s' if len(extras) > 1 else ''} {', '.join(extras)}; allowed fields are {allowed}"
    if kind == "enum":
        return f"{where}: {error.instance!r} is not one of {', '.join(map(str, error.validator_value))}"
    if kind == "const":
        return f"{where}: must be {error.validator_value!r}"
    if kind == "pattern":
        if error.validator_value == IDENTIFIER_PATTERN:
            return f"{where}: {error.instance!r} must be lower-case letters, digits and underscores, starting with a letter"
        if error.validator_value == DATE_PATTERN:
            return f"{where}: {error.instance!r} must be a date written as YYYY-MM-DD"
        return f"{where}: {error.instance!r} does not match the required pattern {error.validator_value}"
    if kind == "type":
        expected = error.validator_value if isinstance(error.validator_value, str) else " or ".join(error.validator_value)
        expected = {"object": "mapping", "array": "list"}.get(expected, expected)
        return f"{where}: expected {expected}, found {_type_name(error.instance)}"
    if kind == "minLength":
        return f"{where}: must not be empty"
    if kind == "minItems":
        return f"{where}: must have at least {error.validator_value} item{'s' if error.validator_value != 1 else ''}"
    return f"{where}: {error.message}"


# -- references the schema cannot check ------------------------------------


def _reference_problems(data: LineDict, path: Path, display: str) -> list[Problem]:
    problems: list[Problem] = []

    source_id = data["source"]["id"]
    if path.stem != source_id:
        problems.append(Problem(display, line_of(data, ["source", "id"]), f"source.id '{source_id}' must match the file name; rename the file to {source_id}{path.suffix} or change the id"))

    try:
        date.fromisoformat(data["effective_from"])
    except ValueError:
        problems.append(Problem(display, line_of(data, ["effective_from"]), f"effective_from '{data['effective_from']}' is not a valid calendar date"))

    rules = data.get("rules") or []
    rule_status = {}
    for i, rule in enumerate(rules):
        if rule["id"] in rule_status:
            problems.append(Problem(display, line_of(data, ["rules", i, "id"]), f"rules[{i}].id '{rule['id']}' is already used by another rule; rule ids must be unique"))
        rule_status[rule["id"]] = rule["status"]

    seen_dq: set[str] = set()
    for i, dq in enumerate(data.get("dq_rules") or []):
        if dq["id"] in seen_dq:
            problems.append(Problem(display, line_of(data, ["dq_rules", i, "id"]), f"dq_rules[{i}].id '{dq['id']}' is already used by another DQ rule; ids must be unique"))
        seen_dq.add(dq["id"])

    seen_targets: set[str] = set()
    for i, mapping in enumerate(data.get("mappings") or []):
        target = mapping["target"]
        if target in seen_targets:
            problems.append(Problem(display, line_of(data, ["mappings", i, "target"]), f"mappings[{i}].target '{target}' is mapped more than once"))
        seen_targets.add(target)
        rule_id = mapping.get("rule")
        if rule_id is None:
            continue
        if rule_id not in rule_status:
            problems.append(Problem(display, line_of(data, ["mappings", i, "rule"]), f"mappings[{i}].rule '{rule_id}' is not defined under rules"))
        elif rule_status[rule_id] == "rejected":
            problems.append(Problem(display, line_of(data, ["mappings", i, "rule"]), f"mappings[{i}].rule '{rule_id}' has been rejected by its owner; a config may not use a rejected rule"))

    delivery = data.get("delivery")
    if delivery:
        timezone = delivery["timezone"]
        if not _timezone_exists(timezone):
            problems.append(Problem(display, line_of(data, ["delivery", "timezone"]), f"delivery.timezone '{timezone}' is not a known IANA timezone"))
        seen_patterns: set[str] = set()
        for i, file in enumerate(delivery["files"]):
            if file["pattern"] in seen_patterns:
                problems.append(Problem(display, line_of(data, ["delivery", "files", i, "pattern"]), f"delivery.files[{i}].pattern '{file['pattern']}' is listed more than once"))
            seen_patterns.add(file["pattern"])

    return problems


def _timezone_exists(name: str) -> bool:
    """True unless the timezone database is present and does not know the name."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

    if name == "UTC":
        return True
    if not available_timezones():
        return True  # no tz database on this machine; the shape check has already passed
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _dedupe(problems: list[Problem]) -> list[Problem]:
    seen: set[tuple[str, int | None, str]] = set()
    unique: list[Problem] = []
    for p in problems:
        key = (p.path, p.line, p.message)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _display_path(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = ["Problem", "discover", "load_schema", "validate_config_file", "validate_paths"]
