"""The rule catalog (product spec Section 4): every business rule, governed.

A rule lives in `rules/<group>/<name>.yaml` and has the id `<group>.<name>`.
It carries the rule in plain words, where it was found (a spec page, a
file and line of legacy code, or a document page), its class (ingestion,
normalisation, business), its owner, its status (recovered, confirmed,
rejected, legacy defect) and the history of every status it has had, with
who set it and when. The current status is the last history entry, so a
status cannot change without a record of who changed it.

Configs reference rules by id under `rules` and on mappings. Lineage is
computed from the configs, because Git is the system of record; the
generation plane rejects a config that references a rule the catalog does
not have or has rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

SCHEMA = "rule-v0.schema.json"
RULE_SUFFIXES = (".yaml", ".yml")
CLASSES = ("ingestion", "business", "normalisation")
STATUSES = ("recovered", "confirmed", "rejected", "legacy_defect")


@dataclass(frozen=True)
class Citation:
    kind: str  # spec, code, document
    spec_id: str | None = None
    spec_version: str | None = None
    page: int | None = None
    line: int | None = None
    end_line: int | None = None
    file: str | None = None
    repository: str | None = None
    document: str | None = None
    section: str | None = None

    @property
    def text(self) -> str:
        if self.kind == "spec":
            return f"spec {self.spec_id} {self.spec_version} page {self.page}" + (f" line {self.line}" if self.line else "")
        if self.kind == "code":
            where = f"{self.file}:{self.line}" + (f"-{self.end_line}" if self.end_line else "")
            return f"{self.repository} {where}" if self.repository else where
        return f"{self.document} page {self.page}" + (f" ({self.section})" if self.section else "")

    def to_mapping(self) -> dict:
        if self.kind == "spec":
            item = {"id": self.spec_id, "version": self.spec_version, "page": self.page}
            if self.line:
                item["line"] = self.line
            return {"spec": item}
        if self.kind == "code":
            item: dict = {}
            if self.repository:
                item["repository"] = self.repository
            item.update({"file": self.file, "line": self.line})
            if self.end_line:
                item["end_line"] = self.end_line
            return {"code": item}
        item = {"name": self.document, "page": self.page}
        if self.section:
            item["section"] = self.section
        return {"document": item}


@dataclass(frozen=True)
class Owner:
    name: str
    email: str


@dataclass(frozen=True)
class HistoryEntry:
    status: str
    by: str
    at: datetime
    note: str | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    class_: str
    owner: Owner
    status: str
    citation: Citation
    history: tuple[HistoryEntry, ...]
    path: Path
    custodians: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def group(self) -> str:
        return self.id.split(".")[0]

    @property
    def name(self) -> str:
        return self.id.split(".")[1]

    @property
    def last_change(self) -> HistoryEntry:
        return self.history[-1]


@dataclass(frozen=True)
class Catalog:
    root: Path
    rules: tuple[Rule, ...]

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def ids(self) -> list[str]:
        return [r.id for r in self.rules]

    def by_status(self, status: str) -> list[Rule]:
        return [r for r in self.rules if r.status == status]

    @classmethod
    def load(cls, root: Path, repo_root: Path | None = None, registry=None) -> tuple["Catalog", list[Problem]]:
        """Every rule under rules/<group>/<name>.yaml. Problems mean the catalog is not usable."""
        root = Path(root)
        if not root.is_dir():
            return cls(root, ()), [Problem(display_path(root, repo_root), None, "rule catalog directory does not exist")]
        rules: list[Rule] = []
        problems: list[Problem] = []
        for path in discover(root):
            rule, rule_problems = load_rule_file(path, repo_root, registry)
            problems.extend(rule_problems)
            if rule is not None:
                rules.append(rule)
        for stray in sorted(p for p in root.glob("*") if p.is_file() and p.suffix in RULE_SUFFIXES):
            problems.append(Problem(display_path(stray, repo_root), None, "rules live one directory down: rules/<group>/<name>.yaml"))
        return cls(root, tuple(rules)), problems


def discover(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).glob("*/*") if p.is_file() and p.suffix in RULE_SUFFIXES and not p.name.startswith("_"))


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_rule_file(path: Path, root: Path | None = None, registry=None) -> tuple[Rule | None, list[Problem]]:
    """Parse and validate one rule. With a spec registry, a spec citation must name a known version."""
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
    if data.get("rule_version") != 0:
        line = line_of(data, ["rule_version"]) if "rule_version" in data else 1
        return None, [Problem(display, line, f"rule_version must be 0; found {data.get('rule_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    rule = data["rule"]
    expected = f"{path.parent.name}.{path.stem}"
    if rule["id"] != expected:
        problems.append(Problem(display, line_of(data, ["rule", "id"]), f"rule.id '{rule['id']}' must match the file's place in the catalog: {expected}"))

    history = data["history"]
    previous: datetime | None = None
    for i, entry in enumerate(history):
        at = _parse_at(entry["at"])
        if previous is not None and at <= previous:
            problems.append(Problem(display, line_of(data, ["history", i, "at"]), f"history[{i}] is not after history[{i - 1}]; history is oldest first and every change is later than the one before"))
        previous = at
    if history[-1]["status"] != rule["status"]:
        problems.append(Problem(display, line_of(data, ["rule", "status"]), f"rule.status '{rule['status']}' is not the last recorded status '{history[-1]['status']}'; a status changes only through the history (astra-spec rules set-status)"))
    for i, entry in enumerate(history[1:], start=1):
        if entry["status"] == history[i - 1]["status"]:
            problems.append(Problem(display, line_of(data, ["history", i, "status"]), f"history[{i}] repeats the status '{entry['status']}' of history[{i - 1}]; every entry is a change"))

    citation = rule["citation"]
    if "spec" in citation and registry is not None:
        spec = registry.get(citation["spec"]["id"], str(citation["spec"]["version"]))
        if spec is None:
            known = [s.version for s in registry.versions(citation["spec"]["id"])]
            detail = f"; known versions: {', '.join(known)}" if known else "; no such spec in the registry"
            problems.append(Problem(display, line_of(data, ["rule", "citation", "spec"]), f"citation names spec {citation['spec']['id']} {citation['spec']['version']}, which is not in the spec registry{detail}"))
        elif spec.document and citation["spec"]["page"] > int(spec.document.get("pages") or 10**6):
            problems.append(Problem(display, line_of(data, ["rule", "citation", "spec"]), f"citation page {citation['spec']['page']} is beyond the {spec.document.get('pages')} pages of {spec.label}'s document"))
    if problems:
        return None, problems

    return Rule(
        id=rule["id"],
        text=" ".join(rule["text"].split()),
        class_=rule["class"],
        owner=Owner(rule["owner"]["name"], rule["owner"]["email"]),
        status=rule["status"],
        citation=_citation(citation),
        history=tuple(HistoryEntry(e["status"], e["by"], _parse_at(e["at"]), (" ".join(e["note"].split()) if e.get("note") else None)) for e in history),
        path=path,
        custodians=tuple((rule.get("applies_to") or {}).get("custodians") or ()),
        entities=tuple((rule.get("applies_to") or {}).get("entities") or ()),
        tags=tuple(rule.get("tags") or ()),
    ), []


def _citation(data: dict) -> Citation:
    if "spec" in data:
        s = data["spec"]
        return Citation("spec", spec_id=s["id"], spec_version=str(s["version"]), page=s["page"], line=s.get("line"))
    if "code" in data:
        c = data["code"]
        return Citation("code", file=c["file"], line=c["line"], end_line=c.get("end_line"), repository=c.get("repository"))
    d = data["document"]
    return Citation("document", document=d["name"], page=d["page"], section=d.get("section"))


# -- writing -----------------------------------------------------------------


def _yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_rule(rule: Rule) -> str:
    """The canonical text of a rule file. `set_status` rewrites the file with it."""
    lines = [
        "# Rule catalog entry. The status changes only through `astra-spec rules set-status`,",
        "# which records who changed it and when in the history below.",
        "rule_version: 0",
        "",
        "rule:",
        f"  id: {rule.id}",
        f"  text: {_yaml_scalar(rule.text)}",
        f"  class: {rule.class_}",
        f"  owner: {{ name: {_yaml_scalar(rule.owner.name)}, email: {rule.owner.email} }}",
        f"  status: {rule.status}",
        "  citation:",
    ]
    (kind, item), = rule.citation.to_mapping().items()
    lines.append(f"    {kind}: {{ " + ", ".join(f"{k}: {_yaml_scalar(v) if isinstance(v, str) and (k == 'version' or ' ' in v or ':' in v) else v}" for k, v in item.items()) + " }")
    if rule.custodians or rule.entities:
        parts = []
        if rule.custodians:
            parts.append(f"custodians: [{', '.join(rule.custodians)}]")
        if rule.entities:
            parts.append(f"entities: [{', '.join(rule.entities)}]")
        lines.append("  applies_to: { " + ", ".join(parts) + " }")
    if rule.tags:
        lines.append(f"  tags: [{', '.join(rule.tags)}]")
    lines.append("")
    lines.append("history:")
    for entry in rule.history:
        at = entry.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = f"  - {{ status: {entry.status}, by: {_yaml_scalar(entry.by)}, at: {_yaml_scalar(at)}"
        if entry.note:
            text += f", note: {_yaml_scalar(entry.note)}"
        lines.append(text + " }")
    lines.append("")
    return "\n".join(lines)


class StatusError(ValueError):
    """The status change is not allowed."""


def set_status(rule: Rule, status: str, by: str, note: str | None = None, at: datetime | None = None) -> Rule:
    """Record a status change in the rule's history and rewrite its file. Returns the changed rule."""
    if status not in STATUSES:
        raise StatusError(f"'{status}' is not a status; statuses are {', '.join(STATUSES)}")
    if status == rule.status:
        raise StatusError(f"{rule.id} is already {status} (set by {rule.last_change.by} at {rule.last_change.at.strftime('%Y-%m-%d %H:%M UTC')})")
    if not by.strip():
        raise StatusError("who is changing the status must be given (--by)")
    when = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    if when <= rule.last_change.at:
        raise StatusError(f"the change must be later than the last recorded change at {rule.last_change.at.isoformat()}")
    changed = Rule(
        id=rule.id,
        text=rule.text,
        class_=rule.class_,
        owner=rule.owner,
        status=status,
        citation=rule.citation,
        history=rule.history + (HistoryEntry(status, by.strip(), when, note.strip() if note else None),),
        path=rule.path,
        custodians=rule.custodians,
        entities=rule.entities,
        tags=rule.tags,
    )
    rule.path.write_text(render_rule(changed), encoding="utf-8", newline="\n")
    return changed


# -- lineage -----------------------------------------------------------------


@dataclass
class Lineage:
    """Which configs reference which rules, and references the catalog cannot satisfy."""

    configs_by_rule: dict[str, list[str]] = field(default_factory=dict)
    unknown: list[tuple[str, str]] = field(default_factory=list)  # (config, rule id)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def configs_for(self, rule_id: str) -> list[str]:
        return self.configs_by_rule.get(rule_id, [])


def config_rule_references(data: dict) -> list[str]:
    """Rule ids a loaded config references: under `rules` and on mappings, in order, once each."""
    ids: list[str] = []
    for item in data.get("rules") or []:
        if isinstance(item, str) and item not in ids:
            ids.append(item)
    for mapping in data.get("mappings") or []:
        rule_id = mapping.get("rule") if isinstance(mapping, dict) else None
        if isinstance(rule_id, str) and rule_id not in ids:
            ids.append(rule_id)
    return ids


def lineage(catalog: Catalog, config_paths: Iterable[Path], root: Path | None = None) -> Lineage:
    """Scan configs for rule references. Unreadable configs are skipped; validation reports them elsewhere."""
    result = Lineage({r.id: [] for r in catalog.rules})
    for path in config_paths:
        path = Path(path)
        try:
            data = load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SourceError):
            continue
        if not isinstance(data, dict):
            continue
        display = display_path(path, root)
        for rule_id in config_rule_references(data):
            rule = catalog.get(rule_id)
            if rule is None:
                result.unknown.append((display, rule_id))
                continue
            result.configs_by_rule.setdefault(rule_id, []).append(display)
            if rule.status == "rejected":
                result.rejected.append((display, rule_id))
    return result
