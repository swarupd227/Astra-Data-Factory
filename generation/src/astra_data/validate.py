"""Config validation: schema first, then the references the schema cannot see.

Every problem is reported with file and line and in plain words so that it
can be shown on the pull request that introduced it. A config that passes
here is the input the compiler (S3.1.1) accepts; the compiler adds meaning,
this adds nothing but checks.

When a spec registry is given, each config's `spec` reference is checked
against it: the version must exist, the custodian must be one that delivers
the layout, the file type must match, and the config must not be in force
before the spec version is. When a rule catalog is given, every rule the
config lists must be in it and must not have been rejected by its owner.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import LineDict, SourceError, line_of, load

SCHEMA_FILES = {0: "config-v0.schema.json"}
CONFIG_SUFFIXES = (".yaml", ".yml")


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


def validate_paths(paths: Iterable[Path | str], root: Path | None = None, registry=None, catalog=None) -> tuple[int, list[Problem]]:
    """Validate every config under the paths. Returns (files checked, problems)."""
    files, problems = discover(paths)
    for file in files:
        problems.extend(validate_config_file(file, root, registry, catalog))
    return len(files), problems


def validate_config_file(path: Path, root: Path | None = None, registry=None, catalog=None) -> list[Problem]:
    display = display_path(path, root)
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

    validator = load_validator("astra_data.schemas", SCHEMA_FILES[version])
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(validator, data)]
    if problems:
        return dedupe(problems)

    problems = _reference_problems(data, path, display)
    if registry is not None:
        problems.extend(_registry_problems(data, display, registry))
    if catalog is not None:
        problems.extend(_catalog_problems(data, display, catalog))
    return dedupe(problems)


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

    rule_ids = data.get("rules") or []
    declared: set[str] = set()
    for i, rule_id in enumerate(rule_ids):
        if rule_id in declared:
            problems.append(Problem(display, line_of(data, ["rules", i]), f"rules[{i}] '{rule_id}' is listed more than once"))
        declared.add(rule_id)

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
        if rule_id is not None and rule_id not in declared:
            problems.append(Problem(display, line_of(data, ["mappings", i, "rule"]), f"mappings[{i}].rule '{rule_id}' is not listed under rules; a config lists every rule it uses"))

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


def _registry_problems(data: LineDict, display: str, registry) -> list[Problem]:
    """The config's spec reference against the spec registry (astra_knowledge.registry.Registry)."""
    spec_id, version = data["spec"]["id"], data["spec"]["version"]
    spec = registry.get(spec_id, version)
    if spec is None:
        known = [s.version for s in registry.versions(spec_id)]
        if known:
            message = f"spec.version '{version}' of spec '{spec_id}' is not in the spec registry; known versions: {', '.join(known)}"
        else:
            message = f"spec.id '{spec_id}' is not in the spec registry (specs/<id>/<version>.yaml)"
        return [Problem(display, line_of(data, ["spec", "version" if known else "id"]), message)]

    problems: list[Problem] = []
    custodian, file_type = data["source"]["custodian"], data["source"]["file_type"]
    if custodian not in spec.custodians:
        problems.append(Problem(display, line_of(data, ["source", "custodian"]), f"custodian '{custodian}' is not listed as delivering spec {spec_id} {version}; it delivers to {', '.join(spec.custodians)}"))
    if file_type != spec.file_type:
        problems.append(Problem(display, line_of(data, ["source", "file_type"]), f"source.file_type '{file_type}' does not match spec {spec_id} {version}, which describes '{spec.file_type}' files"))
    try:
        effective = date.fromisoformat(data["effective_from"])
    except ValueError:
        effective = None
    if effective is not None and effective < spec.effective_from:
        problems.append(Problem(display, line_of(data, ["effective_from"]), f"effective_from {effective} is before spec {spec_id} {version} comes into force on {spec.effective_from}"))
    return problems


def _catalog_problems(data: LineDict, display: str, catalog) -> list[Problem]:
    """The config's rule references against the rule catalog (astra_knowledge.rules.Catalog)."""
    problems: list[Problem] = []
    for i, rule_id in enumerate(data.get("rules") or []):
        rule = catalog.get(rule_id)
        if rule is None:
            problems.append(Problem(display, line_of(data, ["rules", i]), f"rules[{i}] '{rule_id}' is not in the rule catalog ({catalog.root.name}/<group>/<name>.yaml)"))
        elif rule.status == "rejected":
            last = rule.last_change
            problems.append(Problem(display, line_of(data, ["rules", i]), f"rules[{i}] '{rule_id}' was rejected by {last.by} on {last.at:%Y-%m-%d}; a config may not use a rejected rule"))
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


__all__ = ["Problem", "discover", "validate_config_file", "validate_paths"]
