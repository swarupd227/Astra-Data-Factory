"""The config compiler (S3.1.1): a valid config becomes a compiled intermediate model.

Validation (astra_data.validate) checks the shape of a config and its
references. The compiler goes further: it resolves every reference to the
object it names (the spec version, the pattern, the target profile, the
domain pack and its latest model, the catalog rules) and every mapping to
a canonical column, a source field and a transform, and checks that the
types agree. What comes out is the intermediate model the renderers
(S3.1.2, S3.2.x) work from: nothing in it is a name that still has to be
looked up, and its provenance records exactly which inputs produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from astra_knowledge.cdm import Column, DomainPack, Entity, Model
from astra_knowledge.patterns import PATTERNS, Pattern, patterns_for
from astra_knowledge.registry import Field, Registry, SourceSpec
from astra_knowledge.rules import Catalog, Rule

from astra_core.problems import Problem, dedupe, display_path
from astra_core.yamlsource import LineDict, line_of, load
from astra_data.targets import TargetProfile, get_profile, profile_ids
from astra_data.transforms import TransformCall, TransformError, parse_transform
from astra_data.validate import discover, validate_config_file

# Which source types may land in which canonical column types without a transform.
COMPATIBLE: dict[str, tuple[str, ...]] = {
    "string": ("string",),
    "code": ("string",),
    "integer": ("integer", "decimal"),
    "decimal": ("decimal",),
    "date": ("date", "timestamp"),
    "time": ("string",),
    "boolean": ("boolean", "string"),
}


class CompileError(ValueError):
    """The config cannot be compiled. Carries the problems found."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.format() for p in problems))


@dataclass(frozen=True)
class CompiledMapping:
    entity: Entity
    column: Column
    record: str
    source: Field
    source_type: str
    result_type: str
    transform: TransformCall | None = None
    rule: Rule | None = None

    @property
    def target(self) -> str:
        return f"{self.entity.table}.{self.column.name}"

    def to_dict(self) -> dict:
        return {
            "target": {"entity": self.entity.name, "table": self.entity.table, "column": self.column.name, "type": self.column.sql_type, "required": self.column.required},
            "source": {"record": self.record, "field": self.source.name, "type": self.source_type, "picture": self.source.picture.text if self.source.picture else None, "position": list(self.source.position) if self.source.position else None, "column": self.source.column},
            "transform": self.transform.to_dict() if self.transform else None,
            "result_type": self.result_type,
            "rule": self.rule.id if self.rule else None,
        }


@dataclass(frozen=True)
class CompiledConfig:
    path: Path
    source: dict[str, Any]
    spec: SourceSpec
    pattern: Pattern
    profile: TargetProfile
    pack: DomainPack
    model: Model
    effective_from: date
    owner: dict[str, str]
    mappings: tuple[CompiledMapping, ...]
    rules: tuple[Rule, ...]
    dq_rules: tuple[dict, ...]
    resolution: dict[str, Any]
    delivery: dict[str, Any] | None
    alerts: dict[str, Any] | None
    provenance: dict[str, Any] = field(default_factory=dict)
    processing: dict[str, Any] = field(default_factory=dict)

    DEFAULT_TARGET_LAG_MINUTES = 15

    @property
    def id(self) -> str:
        return self.source["id"]

    @property
    def target_lag_minutes(self) -> int:
        """How stale the parsed tables may be, and how often the source is processed."""
        return int(self.processing.get("target_lag_minutes", self.DEFAULT_TARGET_LAG_MINUTES))

    def to_dict(self) -> dict:
        return {
            "source": dict(self.source),
            "spec": {"id": self.spec.id, "version": self.spec.version, "format": self.spec.format, "file_type": self.spec.file_type, "logical_records": self.spec.logical_records()},
            "pattern": {"id": self.pattern.id, "name": self.pattern.name},
            "target_profile": self.profile.to_dict(),
            "domain_pack": {"name": self.pack.name, "model_version": self.model.version, "schema": self.model.schema},
            "effective_from": self.effective_from.isoformat(),
            "owner": dict(self.owner),
            "mappings": [m.to_dict() for m in self.mappings],
            "rules": [{"id": r.id, "class": r.class_, "status": r.status, "citation": r.citation.text, "text": r.text} for r in self.rules],
            "dq_rules": [dict(d) for d in self.dq_rules],
            "resolution": dict(self.resolution),
            "delivery": dict(self.delivery) if self.delivery else None,
            "alerts": dict(self.alerts) if self.alerts else None,
            "processing": {"target_lag_minutes": self.target_lag_minutes},
            "provenance": dict(self.provenance),
        }


def _entity_for(model: Model, name: str) -> Entity | None:
    wanted = name.lower()
    return next((e for e in model.entities if e.table.lower() == wanted or e.name.lower().replace(" ", "_") == wanted), None)


def _field_in(spec: SourceSpec, record_label: str, name: str) -> Field | None:
    """The field of a logical record: a pairing's records, a split part's source record, or the record itself."""
    pairing = next((p for p in spec.pairings if p.name == record_label), None)
    labels = list(pairing.records) if pairing else [record_label]
    for rule in spec.splits:
        if any(part.name == record_label for part in rule.parts):
            labels = [rule.record]
    for label in labels:
        record = spec.record(label)
        if record is None:
            continue
        found = record.field(name)
        if found is not None:
            return found
    return None


def compile_config(path: Path, *, registry: Registry, catalog: Catalog, packs: Iterable[DomainPack], root: Path | None = None) -> CompiledConfig:
    """Validate, then resolve every reference of one config. Raises CompileError with every problem found."""
    path = Path(path)
    display = display_path(path, root)
    problems = validate_config_file(path, root, registry, catalog)
    if problems:
        raise CompileError(problems)
    text = path.read_text(encoding="utf-8")
    data: LineDict = load(text)

    spec = registry.get(data["spec"]["id"], str(data["spec"]["version"]))
    assert spec is not None  # validation checked the reference

    pattern = _pattern(data, display, spec, problems)
    profile = get_profile(data["target_profile"])
    if profile is None:
        problems.append(Problem(display, line_of(data, ["target_profile"]), f"target_profile '{data['target_profile']}' has no renderers; target profiles are {', '.join(profile_ids())}"))
    packs = list(packs)
    pack = next((p for p in packs if p.name == data["domain_pack"]), None)
    if pack is None:
        known = ", ".join(p.name for p in packs) or "none"
        problems.append(Problem(display, line_of(data, ["domain_pack"]), f"domain_pack '{data['domain_pack']}' is not a domain pack under domains/; domain packs are {known}"))
    if problems:
        raise CompileError(dedupe(problems))

    model = pack.latest
    mappings = tuple(_mappings(data, display, spec, model, catalog, problems))
    if problems:
        raise CompileError(dedupe(problems))

    rules = tuple(catalog.get(rule_id) for rule_id in data.get("rules") or [])
    return CompiledConfig(
        path=path,
        source=dict(data["source"]),
        spec=spec,
        pattern=pattern,
        profile=profile,
        pack=pack,
        model=model,
        effective_from=date.fromisoformat(data["effective_from"]),
        owner=dict(data["owner"]),
        mappings=mappings,
        rules=rules,
        dq_rules=tuple(dict(d) for d in data.get("dq_rules") or []),
        resolution=dict(data.get("resolution") or {}),
        delivery=dict(data["delivery"]) if data.get("delivery") else None,
        alerts=dict(data["alerts"]) if data.get("alerts") else None,
        processing=dict(data.get("processing") or {}),
        provenance={
            "config": {"path": display, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
            "spec": {"id": spec.id, "version": spec.version, "path": display_path(spec.path, root), "sha256": _sha(spec.path)},
            "pattern": pattern.id,
            "target_profile": profile.id,
            "domain_pack": {"name": pack.name, "model_version": model.version, "model_path": display_path(model.path, root), "sha256": _sha(model.path)},
            "rules": [{"id": r.id, "status": r.status, "changed_by": r.last_change.by, "changed_at": r.last_change.at.isoformat(), "sha256": _sha(r.path)} for r in rules],
        },
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pattern(data: LineDict, display: str, spec: SourceSpec, problems: list[Problem]) -> Pattern | None:
    applicable = patterns_for(spec)
    named = data.get("pattern")
    if named is None:
        if not applicable:
            problems.append(Problem(display, line_of(data, ["spec"]), f"no pattern in the library handles spec {spec.label} ({spec.format} files)"))
            return None
        return applicable[0]
    pattern = PATTERNS.get(named)
    if pattern is None:
        problems.append(Problem(display, line_of(data, ["pattern"]), f"pattern '{named}' is not in the pattern library; patterns are {', '.join(sorted(PATTERNS))}"))
        return None
    if pattern not in applicable:
        handles = ", ".join(p.id for p in applicable) or "none"
        problems.append(Problem(display, line_of(data, ["pattern"]), f"pattern '{named}' does not handle spec {spec.label} ({spec.format} files); patterns that do: {handles}"))
        return None
    return pattern


def _mappings(data: LineDict, display: str, spec: SourceSpec, model: Model, catalog: Catalog, problems: list[Problem]) -> list[CompiledMapping]:
    logical = spec.logical_records()
    compiled: list[CompiledMapping] = []
    for i, mapping in enumerate(data.get("mappings") or []):
        where = ["mappings", i]
        entity_name, column_name = mapping["target"].split(".", 1)
        entity = _entity_for(model, entity_name)
        if entity is None:
            problems.append(Problem(display, line_of(data, where + ["target"]), f"mappings[{i}].target '{mapping['target']}': '{entity_name}' is not an entity of {model.label}; entities are {', '.join(e.table.lower() for e in model.entities)}"))
            continue
        column = entity.column(column_name.upper())
        if column is None:
            problems.append(Problem(display, line_of(data, where + ["target"]), f"mappings[{i}].target '{mapping['target']}': '{column_name}' is not a column of {entity.name}; columns are {', '.join(c.name.lower() for c in entity.columns)}"))
            continue

        record = mapping.get("record")
        if record is None:
            if len(logical) != 1:
                problems.append(Problem(display, line_of(data, where), f"mappings[{i}]: spec {spec.label} has {len(logical)} logical records ({', '.join(logical)}); say which with `record`"))
                continue
            record = logical[0]
        elif record not in logical:
            problems.append(Problem(display, line_of(data, where + ["record"]), f"mappings[{i}].record '{record}' is not a logical record of spec {spec.label}; records are {', '.join(logical)}"))
            continue
        source = _field_in(spec, record, mapping["source"])
        if source is None:
            names = ", ".join(sorted(spec.logical_fields(record)))
            problems.append(Problem(display, line_of(data, where + ["source"]), f"mappings[{i}].source '{mapping['source']}' is not a field of record '{record}' in spec {spec.label}; fields are {names}"))
            continue

        transform: TransformCall | None = None
        if mapping.get("transform"):
            try:
                transform = parse_transform(mapping["transform"])
            except TransformError as exc:
                problems.append(Problem(display, line_of(data, where + ["transform"]), f"mappings[{i}].transform: {exc}"))
                continue
            if source.type not in transform.transform.accepts:
                problems.append(Problem(display, line_of(data, where + ["transform"]), f"mappings[{i}].transform {transform.transform.name} does not accept a {source.type} field; it accepts {', '.join(transform.transform.accepts)}"))
                continue
        result_type = transform.result_type(source.type) if transform else source.type
        if column.type not in COMPATIBLE.get(result_type, ()):
            via = f" after {transform.text}" if transform else ""
            problems.append(Problem(display, line_of(data, where), f"mappings[{i}]: {record}.{source.name} is {result_type}{via}, which cannot land in {entity.table}.{column.name} ({column.sql_type}); add a transform or map another field"))
            continue
        compiled.append(CompiledMapping(entity, column, record, source, source.type, result_type, transform, catalog.get(mapping["rule"]) if mapping.get("rule") else None))
    return compiled


def compile_paths(paths: Iterable[Path | str], *, registry: Registry, catalog: Catalog, packs: Iterable[DomainPack], root: Path | None = None) -> tuple[list[CompiledConfig], list[Problem]]:
    """Compile every config under the paths. Returns the compiled models and every problem."""
    files, problems = discover(paths)
    packs = list(packs)
    compiled: list[CompiledConfig] = []
    for file in files:
        try:
            compiled.append(compile_config(file, registry=registry, catalog=catalog, packs=packs, root=root))
        except CompileError as exc:
            problems.extend(exc.problems)
    return compiled, problems
