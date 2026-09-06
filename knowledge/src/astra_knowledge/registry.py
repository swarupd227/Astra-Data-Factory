"""The spec registry: every Source Spec, every version, and which one is in force.

Layout on disk, in Git:

    specs/<spec id>/<version>.yaml

A version is in force for a custodian and file type from its effective_from
date until a later version's. `Registry.resolve(custodian, file_type, date)`
answers which. Two versions of one spec coexist as two files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import LineDict, SourceError, line_of, load

from astra_knowledge.picture import COMPATIBLE_TYPES, Picture, PictureError, parse_picture

SPEC_SUFFIXES = (".yaml", ".yml")
SCHEMA = "source-spec-v0.schema.json"


@dataclass(frozen=True)
class Citation:
    page: int | None = None
    line: int | None = None
    document: str | None = None

    def text(self) -> str:
        parts = [f"page {self.page}" if self.page else None, f"line {self.line}" if self.line else None]
        where = ", ".join(p for p in parts if p)
        return f"{self.document}, {where}" if self.document else where


@dataclass(frozen=True)
class Field:
    name: str
    citation: Citation
    type: str
    picture: Picture | None = None
    position: tuple[int, int] | None = None
    column: int | None = None
    label: str | None = None
    format: str | None = None
    sign_field: str | None = None
    required: bool = False
    description: str = ""
    codes: tuple[tuple[str, str], ...] = ()

    @property
    def start(self) -> int | None:
        return self.position[0] if self.position else None

    @property
    def length(self) -> int | None:
        return self.position[1] if self.position else None

    @property
    def end(self) -> int | None:
        return self.position[0] + self.position[1] - 1 if self.position else None


@dataclass(frozen=True)
class MatchRule:
    """How a line is recognised as a record type."""

    kind: str  # position, end_marker or column
    value: str
    start: int | None = None
    length: int | None = None
    end_marker: str | None = None
    column: int | None = None

    @classmethod
    def from_mapping(cls, data: dict) -> "MatchRule":
        if "position" in data:
            return cls("position", str(data["value"]), start=data["position"]["start"], length=data["position"]["length"])
        if "end_marker" in data:
            return cls("end_marker", str(data["value"]), start=data["start"], end_marker=data["end_marker"])
        return cls("column", str(data["value"]), column=data["column"])

    def text(self) -> str:
        if self.kind == "position":
            return f"'{self.value}' at {self.start}-{self.start + self.length - 1}"
        if self.kind == "end_marker":
            return f"'{self.value}' from {self.start} up to '{self.end_marker}'"
        return f"'{self.value}' in column {self.column}"


@dataclass(frozen=True)
class Record:
    type: str
    fields: tuple[Field, ...]
    name: str | None = None
    match: MatchRule | None = None
    description: str = ""

    @property
    def label(self) -> str:
        return self.name or self.type

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    version: str
    effective_from: date
    file_type: str
    custodians: tuple[str, ...]
    records: tuple[Record, ...]
    document: dict
    file: dict
    path: Path
    family: str | None = None
    provider: str | None = None
    description: str = ""

    @property
    def format(self) -> str:
        return self.file["format"]

    @property
    def record_length(self) -> int | None:
        return self.file.get("record_length")

    def record(self, label: str) -> Record | None:
        return next((r for r in self.records if r.label == label), None)

    def fields(self) -> list[tuple[Record, Field]]:
        return [(r, f) for r in self.records for f in r.fields]

    @property
    def label(self) -> str:
        return f"{self.id} {self.version}"


# -- loading one file ----------------------------------------------------------


def discover(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/*") if p.is_file() and p.suffix in SPEC_SUFFIXES and not p.name.startswith("_"))


def load_spec_file(path: Path, root: Path | None = None) -> tuple[SourceSpec | None, list[Problem]]:
    """Parse, validate against the schema, then check what the schema cannot. Returns the spec only when clean."""
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
    if data.get("spec_version") != 0:
        line = line_of(data, ["spec_version"]) if "spec_version" in data else 1
        return None, [Problem(display, line, f"spec_version must be 0; found {data.get('spec_version')!r}")]

    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    problems = _reference_problems(data, path, display)
    if problems:
        return None, dedupe(problems)
    return _build(data, path), []


def _reference_problems(data: LineDict, path: Path, display: str) -> list[Problem]:
    problems: list[Problem] = []
    spec = data["spec"]

    if path.stem != spec["version"]:
        problems.append(Problem(display, line_of(data, ["spec", "version"]), f"spec.version '{spec['version']}' must match the file name; the file must be {spec['version']}{path.suffix}"))
    if path.parent.name != spec["id"]:
        problems.append(Problem(display, line_of(data, ["spec", "id"]), f"spec.id '{spec['id']}' must match the directory name; the file must be under specs/{spec['id']}/"))
    try:
        date.fromisoformat(spec["effective_from"])
    except ValueError:
        problems.append(Problem(display, line_of(data, ["spec", "effective_from"]), f"spec.effective_from '{spec['effective_from']}' is not a valid calendar date"))

    file = data["file"]
    fixed = file["format"] == "fixed_width"
    if fixed and "record_length" not in file:
        problems.append(Problem(display, line_of(data, ["file", "format"]), "file.record_length is required for a fixed_width file"))
    if not fixed and "delimiter" not in file:
        problems.append(Problem(display, line_of(data, ["file", "format"]), "file.delimiter is required for a delimited file"))
    record_length = file.get("record_length")

    records = data["records"]
    kinds = [r["type"] for r in records]
    for kind in ("header", "trailer"):
        if kinds.count(kind) > 1:
            problems.append(Problem(display, line_of(data, ["records"]), f"a file has at most one {kind} record; found {kinds.count(kind)}"))
    if kinds.count("detail") == 0:
        problems.append(Problem(display, line_of(data, ["records"]), "a file must have at least one detail record"))
    details = [(i, r) for i, r in enumerate(records) if r["type"] == "detail"]
    if len(details) > 1:
        for i, r in details:
            if "name" not in r:
                problems.append(Problem(display, line_of(data, ["records", i, "type"]), f"records[{i}] needs a name because the file has more than one detail record type"))
            if "match" not in r:
                problems.append(Problem(display, line_of(data, ["records", i, "type"]), f"records[{i}] needs a match rule because the file has more than one detail record type"))
    labels = [r.get("name") or r["type"] for r in records]
    for i, label in enumerate(labels):
        if labels.index(label) != i:
            problems.append(Problem(display, line_of(data, ["records", i]), f"records[{i}] label '{label}' is already used by another record"))

    for i, r in enumerate(records):
        match = r.get("match")
        if match is None:
            if len(records) > 1:
                problems.append(Problem(display, line_of(data, ["records", i, "type"]), f"records[{i}] needs a match rule so that its lines can be told apart from the other record types"))
            continue
        where = ["records", i, "match"]
        if fixed and "column" in match:
            problems.append(Problem(display, line_of(data, where), f"records[{i}].match uses a column, but this is a fixed_width file; use position or start with end_marker"))
        if not fixed and "column" not in match:
            problems.append(Problem(display, line_of(data, where), f"records[{i}].match must use a column in a delimited file"))
        if not fixed and "column" in match and file.get("column_count") is not None and match["column"] > file["column_count"]:
            problems.append(Problem(display, line_of(data, where + ["column"]), f"records[{i}].match column {match['column']} is beyond the column count {file['column_count']}"))
        if fixed and record_length is not None:
            if "position" in match and match["position"]["start"] + match["position"]["length"] - 1 > record_length:
                problems.append(Problem(display, line_of(data, where + ["position"]), f"records[{i}].match position ends beyond the record length {record_length}"))
            if "start" in match and match["start"] > record_length:
                problems.append(Problem(display, line_of(data, where + ["start"]), f"records[{i}].match start {match['start']} is beyond the record length {record_length}"))
        if "position" in match and len(match["value"]) != match["position"]["length"]:
            problems.append(Problem(display, line_of(data, where + ["value"]), f"records[{i}].match value '{match['value']}' has {len(match['value'])} characters but the position is {match['position']['length']} long"))

    for ri, record in enumerate(records):
        names: dict[str, int] = {}
        spans: list[tuple[int, int, str]] = []
        columns: dict[int, str] = {}
        for fi, field in enumerate(record["fields"]):
            where = ["records", ri, "fields", fi]
            name = field["name"]
            if name in names:
                problems.append(Problem(display, line_of(data, where + ["name"]), f"records[{ri}].fields[{fi}].name '{name}' is already used in this record"))
            names[name] = fi

            picture = None
            if "picture" in field:
                try:
                    picture = parse_picture(field["picture"])
                except PictureError as exc:
                    problems.append(Problem(display, line_of(data, where + ["picture"]), f"records[{ri}].fields[{fi}].picture: {exc}"))
            declared = field.get("type")
            if picture and declared and declared not in COMPATIBLE_TYPES[picture.kind]:
                problems.append(Problem(display, line_of(data, where + ["type"]), f"records[{ri}].fields[{fi}].type '{declared}' does not fit picture {picture.text}; a {picture.kind} picture may be {', '.join(sorted(COMPATIBLE_TYPES[picture.kind]))}"))
            if declared in ("date", "time") and "format" not in field:
                problems.append(Problem(display, line_of(data, where + ["type"]), f"records[{ri}].fields[{fi}] is a {declared} and needs a format, for example YYYYMMDD"))

            if fixed:
                if "position" not in field:
                    problems.append(Problem(display, line_of(data, where + ["name"]), f"records[{ri}].fields[{fi}] '{name}' needs a position in a fixed_width file"))
                    continue
                start, length = field["position"]["start"], field["position"]["length"]
                end = start + length - 1
                if record_length is not None and end > record_length:
                    problems.append(Problem(display, line_of(data, where + ["position"]), f"records[{ri}].fields[{fi}] '{name}' ends at {end}, beyond the record length {record_length}"))
                if picture and picture.length != length:
                    problems.append(Problem(display, line_of(data, where + ["position"]), f"records[{ri}].fields[{fi}] '{name}' has length {length} but picture {picture.text} occupies {picture.length}"))
                for other_start, other_end, other in spans:
                    if start <= other_end and other_start <= end:
                        problems.append(Problem(display, line_of(data, where + ["position"]), f"records[{ri}].fields[{fi}] '{name}' ({start}-{end}) overlaps '{other}' ({other_start}-{other_end})"))
                spans.append((start, end, name))
            else:
                if "column" not in field:
                    problems.append(Problem(display, line_of(data, where + ["name"]), f"records[{ri}].fields[{fi}] '{name}' needs a column in a delimited file"))
                    continue
                if field["column"] in columns:
                    problems.append(Problem(display, line_of(data, where + ["column"]), f"records[{ri}].fields[{fi}] '{name}' uses column {field['column']}, already used by '{columns[field['column']]}'"))
                columns[field["column"]] = name
                column_count = file.get("column_count")
                if column_count is not None and field["column"] > column_count:
                    problems.append(Problem(display, line_of(data, where + ["column"]), f"records[{ri}].fields[{fi}] '{name}' is in column {field['column']}, beyond the column count {column_count}"))
                if "label" in field and not file.get("header_rows"):
                    problems.append(Problem(display, line_of(data, where + ["label"]), f"records[{ri}].fields[{fi}] '{name}' has a label but file.header_rows is 0, so there is no header row to check it against"))

            values = [c["value"] for c in field.get("codes") or []]
            for ci, value in enumerate(values):
                if values.index(value) != ci:
                    problems.append(Problem(display, line_of(data, where + ["codes", ci, "value"]), f"records[{ri}].fields[{fi}] '{name}' lists code {value!r} more than once"))

        for fi, field in enumerate(record["fields"]):
            sign = field.get("sign_field")
            if sign and sign not in names:
                problems.append(Problem(display, line_of(data, ["records", ri, "fields", fi, "sign_field"]), f"records[{ri}].fields[{fi}].sign_field '{sign}' is not a field of this record"))

    return problems


def _build(data: dict, path: Path) -> SourceSpec:
    spec = data["spec"]
    records = []
    for r in data["records"]:
        fields = []
        for f in r["fields"]:
            picture = parse_picture(f["picture"]) if "picture" in f else None
            declared = f.get("type") or (picture.default_type if picture else "string")
            fields.append(
                Field(
                    name=f["name"],
                    citation=Citation(f["citation"].get("page"), f["citation"].get("line"), f["citation"].get("document")),
                    type=declared,
                    picture=picture,
                    position=(f["position"]["start"], f["position"]["length"]) if "position" in f else None,
                    column=f.get("column"),
                    label=f.get("label"),
                    format=f.get("format"),
                    sign_field=f.get("sign_field"),
                    required=bool(f.get("required", False)),
                    description=f.get("description", ""),
                    codes=tuple((str(c["value"]), c["meaning"]) for c in f.get("codes") or []),
                )
            )
        records.append(Record(type=r["type"], fields=tuple(fields), name=r.get("name"), match=MatchRule.from_mapping(r["match"]) if r.get("match") else None, description=r.get("description", "")))
    return SourceSpec(
        id=spec["id"],
        version=spec["version"],
        effective_from=date.fromisoformat(spec["effective_from"]),
        file_type=spec["file_type"],
        custodians=tuple(spec["custodians"]),
        records=tuple(records),
        document=dict(data["document"]),
        file=dict(data["file"]),
        path=path,
        family=spec.get("family"),
        provider=spec.get("provider"),
        description=spec.get("description", ""),
    )


# -- the registry --------------------------------------------------------------


class Registry:
    """Every spec version under a directory, with resolution by custodian, file type and date."""

    def __init__(self, root: Path, specs: Iterable[SourceSpec] = ()) -> None:
        self.root = Path(root)
        self.specs: list[SourceSpec] = sorted(specs, key=lambda s: (s.id, s.effective_from, s.version))

    @classmethod
    def load(cls, root: Path, repo_root: Path | None = None) -> tuple["Registry", list[Problem]]:
        """Load every spec and check them against each other. Problems mean the registry is not usable."""
        root = Path(root)
        specs: list[SourceSpec] = []
        problems: list[Problem] = []
        if not root.is_dir():
            return cls(root), [Problem(display_path(root, repo_root), None, "spec registry directory does not exist")]
        for path in discover(root):
            spec, file_problems = load_spec_file(path, repo_root)
            problems.extend(file_problems)
            if spec is not None:
                specs.append(spec)

        by_key: dict[tuple[str, str, date], SourceSpec] = {}
        for spec in specs:
            for custodian in spec.custodians:
                key = (custodian, spec.file_type, spec.effective_from)
                if key in by_key:
                    other = by_key[key]
                    problems.append(Problem(display_path(spec.path, repo_root), None, f"{spec.label} and {other.label} both come into force for custodian '{custodian}' {spec.file_type} files on {spec.effective_from}; two versions cannot be in force on the same date"))
                else:
                    by_key[key] = spec
        return cls(root, specs), problems

    def get(self, spec_id: str, version: str) -> SourceSpec | None:
        return next((s for s in self.specs if s.id == spec_id and s.version == version), None)

    def versions(self, spec_id: str) -> list[SourceSpec]:
        return [s for s in self.specs if s.id == spec_id]

    def ids(self) -> list[str]:
        return sorted({s.id for s in self.specs})

    def resolve(self, custodian: str, file_type: str, business_date: date) -> SourceSpec | None:
        """The version in force for the custodian's file type on the business date, or None."""
        candidates = [s for s in self.specs if custodian in s.custodians and s.file_type == file_type and s.effective_from <= business_date]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.effective_from)

    def in_force(self, custodian: str, file_type: str, business_date: date) -> list[SourceSpec]:
        """Every version that has ever been or will be in force for the pair, in date order, for display."""
        return sorted((s for s in self.specs if custodian in s.custodians and s.file_type == file_type), key=lambda s: s.effective_from)

    def unclassified(self) -> list[SourceSpec]:
        """Specs with no family yet: the Pattern Matcher has not classified them."""
        return [s for s in self.specs if s.family is None]

    def search(
        self,
        *,
        custodian: str | None = None,
        family: str | None = None,
        file_type: str | None = None,
        business_date: date | None = None,
        all_versions: bool = False,
    ) -> list["SearchHit"]:
        """Find specs for a custodian, a family or a file type, ranked.

        Rank 1: the spec lists the custodian. Rank 2: the spec belongs to the
        family (a reuse candidate for a custodian the registry has not seen).
        Rank 3: only the file type matched. Within a rank, newest first.

        With a business date, only versions in force on that date are
        considered. Unless `all_versions` is set, one version per spec is
        returned: the one in force on the date, or the latest.
        """
        if not any([custodian, family, file_type]):
            raise ValueError("search needs at least one of custodian, family or file_type")

        matched: list[SearchHit] = []
        for spec in self.specs:
            if file_type is not None and spec.file_type != file_type:
                continue
            if business_date is not None and spec.effective_from > business_date:
                continue
            by_custodian = custodian is not None and custodian in spec.custodians
            by_family = family is not None and spec.family == family
            if (custodian is not None or family is not None) and not (by_custodian or by_family):
                continue
            match = "custodian" if by_custodian else "family" if by_family else "file_type"
            flags = ("no family; needs classification",) if spec.family is None else ()
            matched.append(SearchHit(spec=spec, match=match, flags=flags, business_date=business_date))

        if not all_versions:
            latest: dict[str, SearchHit] = {}
            for hit in matched:
                current = latest.get(hit.spec.id)
                if current is None or hit.spec.effective_from > current.spec.effective_from:
                    latest[hit.spec.id] = hit
            matched = list(latest.values())

        return sorted(matched, key=lambda h: (h.rank, -h.spec.effective_from.toordinal(), h.spec.id, h.spec.version))


@dataclass(frozen=True)
class SearchHit:
    spec: SourceSpec
    match: str
    flags: tuple[str, ...] = ()
    business_date: date | None = None

    RANKS = {"custodian": 1, "family": 2, "file_type": 3}

    @property
    def rank(self) -> int:
        return self.RANKS[self.match]

    @property
    def needs_classification(self) -> bool:
        return self.spec.family is None
