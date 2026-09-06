"""The rejection taxonomy of a domain pack (product spec Section 4).

Every reason the factory rejects a file, a record or a value is a code in
`domains/<name>/rejections.yaml`, with the level it reaches, its severity,
who resolves it and what they do. The pattern library raises these codes
on its problems, rendered pipelines route exceptions with them, and the
generation plane syncs them to CONTROL.REJECTION_CODES so an exception
row always references a code that exists.

Parity with the legacy Loader: each code may list the Loader codes it
reproduces. When the pack holds `loader-rejections.csv` (the Loader
Rejections reference exported from the client's document, columns `code`
and `description`), `parity` reports the Loader codes no factory code
covers and the Loader codes the taxonomy names that the reference does not.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

SCHEMA = "rejections-v0.schema.json"
REJECTIONS_FILE = "rejections.yaml"
LOADER_REFERENCE_FILE = "loader-rejections.csv"

LEVELS = ("file", "record", "field")
SEVERITIES = ("critical", "error", "warning")
# What a level may be: a rejected file is always critical, a rejected record
# an error, a value either held back (error) or flagged (warning).
SEVERITIES_FOR_LEVEL = {"file": ("critical",), "record": ("error", "warning"), "field": ("error", "warning")}


@dataclass(frozen=True)
class RejectionCode:
    code: str
    name: str
    description: str
    level: str
    severity: str
    category: str
    owner: str
    resolution: str
    entity: str | None = None
    auto_resolve: bool = False
    loader_codes: tuple[str, ...] = ()
    status: str = "active"

    @property
    def active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class Taxonomy:
    domain: str
    codes: tuple[RejectionCode, ...]
    path: Path

    def code(self, name: str) -> RejectionCode | None:
        return next((c for c in self.codes if c.code == name), None)

    def active(self) -> list[RejectionCode]:
        return [c for c in self.codes if c.active]

    def by_loader_code(self) -> dict[str, RejectionCode]:
        return {loader: c for c in self.codes for loader in c.loader_codes}


@dataclass(frozen=True)
class LoaderReference:
    """The Loader Rejections reference: code and description per legacy code."""

    path: Path
    codes: dict[str, str]


@dataclass(frozen=True)
class ParityReport:
    mapped: dict[str, str]  # Loader code -> factory code
    unmapped: tuple[str, ...]  # Loader codes no factory code reproduces
    unknown: tuple[str, ...]  # Loader codes the taxonomy names that are not in the reference

    @property
    def ok(self) -> bool:
        return not self.unmapped and not self.unknown


def _read(path: Path, display: str):
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    return data, []


def load_taxonomy(path: Path, root: Path | None = None) -> tuple[Taxonomy | None, list[Problem]]:
    """Parse and validate a taxonomy file. Returns it only when clean."""
    path = Path(path)
    display = display_path(path, root)
    data, problems = _read(path, display)
    if problems:
        return None, problems
    if data.get("rejections_version") != 0:
        line = line_of(data, ["rejections_version"]) if "rejections_version" in data else 1
        return None, [Problem(display, line, f"rejections_version must be 0; found {data.get('rejections_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    seen: dict[str, int] = {}
    loader_seen: dict[str, str] = {}
    for i, item in enumerate(data["codes"]):
        where = ["codes", i]
        code = item["code"]
        if code in seen:
            problems.append(Problem(display, line_of(data, where + ["code"]), f"code {code} is defined twice; the first is at line {seen[code]}"))
        seen[code] = line_of(data, where + ["code"]) or 0
        allowed = SEVERITIES_FOR_LEVEL[item["level"]]
        if item["severity"] not in allowed:
            problems.append(Problem(display, line_of(data, where + ["severity"]), f"code {code}: a {item['level']}-level rejection is {' or '.join(allowed)}, not {item['severity']}"))
        for loader in item.get("loader_codes") or ():
            if loader in loader_seen:
                problems.append(Problem(display, line_of(data, where + ["loader_codes"]), f"code {code}: Loader code '{loader}' is already reproduced by {loader_seen[loader]}; a Loader code maps to one code"))
            loader_seen[loader] = code
    if problems:
        return None, problems

    codes = tuple(
        RejectionCode(
            code=item["code"],
            name=" ".join(item["name"].split()),
            description=" ".join(item["description"].split()),
            level=item["level"],
            severity=item["severity"],
            category=item["category"],
            owner=item["owner"],
            resolution=" ".join(item["resolution"].split()),
            entity=item.get("entity"),
            auto_resolve=bool(item.get("auto_resolve", False)),
            loader_codes=tuple(item.get("loader_codes") or ()),
            status=item.get("status", "active"),
        )
        for item in data["codes"]
    )
    return Taxonomy(data["domain"], codes, path), []


def load_loader_reference(path: Path, root: Path | None = None) -> tuple[LoaderReference | None, list[Problem]]:
    """Read the Loader Rejections reference: a CSV with a header row naming `code` and `description`."""
    path = Path(path)
    display = display_path(path, root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    reader = csv.DictReader(text.splitlines())
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    if "code" not in fields or "description" not in fields:
        return None, [Problem(display, 1, f"the header row must name the columns code and description; found {', '.join(fields) or 'no header'}")]
    reader.fieldnames = fields
    codes: dict[str, str] = {}
    problems: list[Problem] = []
    for number, row in enumerate(reader, start=2):
        code = (row.get("code") or "").strip()
        if not code:
            problems.append(Problem(display, number, "the code is blank"))
            continue
        if code in codes:
            problems.append(Problem(display, number, f"Loader code '{code}' appears twice"))
            continue
        codes[code] = " ".join((row.get("description") or "").split())
    if not codes and not problems:
        problems.append(Problem(display, None, "the reference lists no codes"))
    if problems:
        return None, problems
    return LoaderReference(path, codes), []


def parity(taxonomy: Taxonomy, reference: LoaderReference) -> ParityReport:
    """Which Loader codes the taxonomy reproduces, which it misses, and which it names that the reference lacks."""
    by_loader = taxonomy.by_loader_code()
    mapped = {loader: by_loader[loader].code for loader in reference.codes if loader in by_loader}
    unmapped = tuple(loader for loader in reference.codes if loader not in by_loader)
    unknown = tuple(sorted(loader for loader in by_loader if loader not in reference.codes))
    return ParityReport(mapped, unmapped, unknown)


def parity_problems(taxonomy: Taxonomy, reference: LoaderReference, root: Path | None = None) -> list[Problem]:
    """The parity report as problems on the taxonomy file: parity is a validation failure, not a report."""
    report = parity(taxonomy, reference)
    display = display_path(taxonomy.path, root)
    problems = [Problem(display, None, f"Loader code '{loader}' ({reference.codes[loader] or 'no description'}) is reproduced by no rejection code; add it to a code's loader_codes") for loader in report.unmapped]
    problems += [Problem(display, None, f"Loader code '{loader}' is named in loader_codes but is not in {display_path(reference.path, root)}") for loader in report.unknown]
    return problems
