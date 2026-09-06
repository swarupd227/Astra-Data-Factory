"""Reference-data feeds of a domain pack (product spec Section 4).

`domains/<name>/reference-data.yaml` declares the reference data the pack
replicates into the platform: the security master and the account
cross-reference for the custodial domain. A feed says what it carries, how
it is keyed, how and when its snapshots arrive, which canonical entity it
resolves and through which identifiers, and which rejection codes
resolution against it raises. The pattern library's reference-data pattern
is the reference implementation of replication and resolution; the
generation plane renders the same semantics as a release bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_knowledge.columns import Column, column_from, column_problems

SCHEMA = "reference-data-v0.schema.json"
REFERENCE_DATA_FILE = "reference-data.yaml"
REFERENCE_SCHEMA = "REFERENCE"
DEFAULT_PATTERN = ".*[.]csv"


@dataclass(frozen=True)
class FeedFile:
    folder: str
    pattern: str = DEFAULT_PATTERN


@dataclass(frozen=True)
class Schedule:
    cron: str
    timezone: str


@dataclass(frozen=True)
class Resolves:
    entity: str
    identifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedRejections:
    not_found: str
    ambiguous: str
    conflict: str


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    system: str
    description: str
    table: str
    key: tuple[str, ...]
    columns: tuple[Column, ...]
    file: FeedFile
    schedule: Schedule
    expected_every_hours: int
    resolves: Resolves
    rejections: FeedRejections
    stale_severity: str = "error"

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def label(self) -> str:
        return f"{self.id} ({self.system})"


@dataclass(frozen=True)
class ReferenceData:
    domain: str
    feeds: tuple[Feed, ...]
    path: Path

    def feed(self, feed_id: str) -> Feed | None:
        return next((f for f in self.feeds if f.id == feed_id), None)


def load_reference_data(path: Path, root: Path | None = None) -> tuple[ReferenceData | None, list[Problem]]:
    """Parse and validate the feeds file. Returns it only when clean."""
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
    if data.get("reference_data_version") != 0:
        line = line_of(data, ["reference_data_version"]) if "reference_data_version" in data else 1
        return None, [Problem(display, line, f"reference_data_version must be 0; found {data.get('reference_data_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    ids: dict[str, int] = {}
    tables: dict[str, str] = {}
    for i, feed in enumerate(data["feeds"]):
        where = ["feeds", i]
        if feed["id"] in ids:
            problems.append(Problem(display, line_of(data, where + ["id"]), f"feed '{feed['id']}' is defined twice"))
        ids[feed["id"]] = i
        if feed["table"] in tables:
            problems.append(Problem(display, line_of(data, where + ["table"]), f"table {feed['table']} is used by both '{tables[feed['table']]}' and '{feed['id']}'"))
        tables[feed["table"]] = feed["id"]
        columns: dict[str, dict] = {}
        for ci, column in enumerate(feed["columns"]):
            for message in column_problems(column):
                problems.append(Problem(display, line_of(data, where + ["columns", ci]), message))
            if column["name"] in columns:
                problems.append(Problem(display, line_of(data, where + ["columns", ci, "name"]), f"column '{column['name']}' of feed '{feed['id']}' is defined twice"))
            columns[column["name"]] = column
        for key in feed["key"]:
            if key not in columns:
                problems.append(Problem(display, line_of(data, where + ["key"]), f"key column '{key}' is not a column of feed '{feed['id']}'"))
            elif not columns[key].get("required", False):
                problems.append(Problem(display, line_of(data, where + ["key"]), f"key column '{key}' of feed '{feed['id']}' must be required"))
        for name in feed["resolves"].get("identifiers") or ():
            if name not in columns:
                problems.append(Problem(display, line_of(data, where + ["resolves", "identifiers"]), f"identifier '{name}' is not a column of feed '{feed['id']}'"))
            elif name in feed["key"]:
                problems.append(Problem(display, line_of(data, where + ["resolves", "identifiers"]), f"identifier '{name}' of feed '{feed['id']}' is a key column; identifiers are the alternate ways a record names a row"))
    if problems:
        return None, problems

    feeds = tuple(
        Feed(
            id=f["id"],
            name=" ".join(f["name"].split()),
            system=f["system"],
            description=" ".join(f["description"].split()),
            table=f["table"],
            key=tuple(f["key"]),
            columns=tuple(column_from(c) for c in f["columns"]),
            file=FeedFile(f["file"]["folder"], f["file"].get("pattern", DEFAULT_PATTERN)),
            schedule=Schedule(f["schedule"]["cron"], f["schedule"]["timezone"]),
            expected_every_hours=int(f["expected_every_hours"]),
            resolves=Resolves(f["resolves"]["entity"], tuple(f["resolves"].get("identifiers") or ())),
            rejections=FeedRejections(f["rejections"]["not_found"], f["rejections"]["ambiguous"], f["rejections"]["conflict"]),
            stale_severity=f.get("stale_severity", "error"),
        )
        for f in data["feeds"]
    )
    return ReferenceData(data["domain"], feeds, path), []


def reference_data_problems(reference: ReferenceData, entity_names: set[str], rejection_codes: set[str], root: Path | None = None) -> list[Problem]:
    """What the feeds must agree with elsewhere in the pack: entities of the model, codes of the taxonomy."""
    display = display_path(reference.path, root)
    problems: list[Problem] = []
    for feed in reference.feeds:
        if feed.resolves.entity not in entity_names:
            problems.append(Problem(display, None, f"feed '{feed.id}' resolves entity '{feed.resolves.entity}', which is in no model version"))
        for kind, code in (("not_found", feed.rejections.not_found), ("ambiguous", feed.rejections.ambiguous), ("conflict", feed.rejections.conflict)):
            if code not in rejection_codes:
                problems.append(Problem(display, None, f"feed '{feed.id}' rejections.{kind} names {code}, which is not in the rejection taxonomy"))
    return problems
