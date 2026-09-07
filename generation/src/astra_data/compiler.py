"""The config compiler (S3.1.1): a valid config becomes a compiled intermediate model.

Validation (astra_data.validate) checks the shape of a config and its
references. The compiler goes further: it resolves every reference to the
object it names (the spec version, the pattern, the target profile, the
domain pack and its latest model, the catalog rules, the reference-data
feeds) and every mapping to a canonical column, a source field or a
constant, and a transform, and checks that the types agree and that the
target entity's key and required columns are all produced. What comes out
is the intermediate model the renderers (S3.1.2, S3.2.x) work from:
nothing in it is a name that still has to be looked up, and its
provenance records exactly which inputs produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from astra_knowledge.cdm import Column, DomainPack, Entity, Model
from astra_knowledge.patterns import PATTERNS, Pattern, patterns_for
from astra_knowledge.reference_data import Feed
from astra_knowledge.registry import Field, Registry, SourceSpec
from astra_knowledge.rules import Catalog, Rule

from astra_core.problems import Problem, dedupe, display_path
from astra_core.yamlsource import LineDict, line_of, load
from astra_data.dq import CompiledDqRule, compile_dq_rules
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

# Columns the resolution stage fills when the entity has them, without a mapping.
STAGE_COLUMNS = ("CUSTODIAN_ID", "SOURCE_SYSTEM", "SOURCE_FILE", "SOURCE_LINE", "CONFIG_VERSION", "LOADED_AT", "UPDATED_AT")
ACCOUNT_COLUMNS = ("ACCOUNT_ID", "FIRM_ID")
SECURITY_COLUMNS = ("SECURITY_ID", "CUSTODIAN_SECURITY_ID")
TRANSACTION_CODE_COLUMNS = ("TRANSACTION_TYPE", "CUSTODIAN_TRANSACTION_CODE")


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
    source: Field | None
    source_type: str
    result_type: str
    transform: TransformCall | None = None
    rule: Rule | None = None
    constant: Any = None

    @property
    def target(self) -> str:
        return f"{self.entity.table}.{self.column.name}"

    def to_dict(self) -> dict:
        return {
            "target": {"entity": self.entity.name, "table": self.entity.table, "column": self.column.name, "type": self.column.sql_type, "required": self.column.required},
            "source": {"record": self.record, "field": self.source.name, "type": self.source_type, "picture": self.source.picture.text if self.source.picture else None, "position": list(self.source.position) if self.source.position else None, "column": self.source.column} if self.source else None,
            "constant": self.constant if not isinstance(self.constant, (date, Decimal)) else str(self.constant),
            "transform": self.transform.to_dict() if self.transform else None,
            "result_type": self.result_type,
            "rule": self.rule.id if self.rule else None,
        }


@dataclass(frozen=True)
class AccountResolution:
    feed: Feed
    source: Field
    require_open: bool
    not_found: str
    closed: str


@dataclass(frozen=True)
class SecurityLookup:
    identifier: str
    source: Field


@dataclass(frozen=True)
class SecurityResolution:
    feed: Feed
    by: tuple[SecurityLookup, ...]
    require_active: bool
    not_found: str
    ambiguous: str
    inactive: str


@dataclass(frozen=True)
class TransactionCodeResolution:
    source: Field
    map: dict[str, str]
    unmapped: str


@dataclass(frozen=True)
class PriceResolution:
    when: str  # missing or always
    lookback_days: int
    price_type: str
    missing: str


@dataclass(frozen=True)
class Resolution:
    account: AccountResolution | None = None
    security: SecurityResolution | None = None
    transaction_code: TransactionCodeResolution | None = None
    price: PriceResolution | None = None

    @property
    def any(self) -> bool:
        return any((self.account, self.security, self.transaction_code, self.price))

    def to_dict(self) -> dict:
        return {
            "account": {"feed": self.account.feed.id, "source": self.account.source.name, "require_open": self.account.require_open, "not_found": self.account.not_found, "closed": self.account.closed} if self.account else None,
            "security": {"feed": self.security.feed.id, "by": [{"identifier": b.identifier, "source": b.source.name} for b in self.security.by], "require_active": self.security.require_active, "not_found": self.security.not_found, "ambiguous": self.security.ambiguous, "inactive": self.security.inactive} if self.security else None,
            "transaction_code": {"source": self.transaction_code.source.name, "map": dict(self.transaction_code.map), "unmapped": self.transaction_code.unmapped} if self.transaction_code else None,
            "price": {"when": self.price.when, "lookback_days": self.price.lookback_days, "price_type": self.price.price_type, "missing": self.price.missing} if self.price else None,
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
    dq_rules: tuple[CompiledDqRule, ...]
    resolution: Resolution
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

    @property
    def pii_fields(self) -> dict[tuple[str, str], str]:
        """PII category per (record, field) of the spec: a field mapped to a PII canonical column carries that category."""
        found: dict[tuple[str, str], str] = {}
        for m in self.mappings:
            if m.source is not None and m.column.pii:
                found.setdefault((m.record, m.source.name), m.column.pii)
        return found

    @property
    def target_entity(self) -> Entity | None:
        """The canonical entity the mappings land in; None when the config maps nothing."""
        return self.mappings[0].entity if self.mappings else None

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
            "dq_rules": [d.to_dict() for d in self.dq_rules],
            "resolution": self.resolution.to_dict(),
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
    resolution = _resolution(data, display, spec, pack, model, problems)
    dq_rules = compile_dq_rules(data, display, spec, problems)
    if problems:
        raise CompileError(dedupe(problems))
    _coverage(data, display, model, mappings, resolution, problems)
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
        dq_rules=dq_rules,
        resolution=resolution,
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


def _constant(column: Column, text: str) -> tuple[Any, str | None]:
    """A constant typed by its target column, or a message saying why it cannot be."""
    kind = column.type
    try:
        if kind == "string":
            if column.codes and text not in [c.value for c in column.codes]:
                return None, f"'{text}' is not one of the codes of {column.name}: {', '.join(c.value for c in column.codes)}"
            return text, None
        if kind == "integer":
            return int(text), None
        if kind == "decimal":
            return Decimal(text), None
        if kind == "date":
            return date.fromisoformat(text), None
        if kind == "boolean":
            if text.lower() in ("true", "false"):
                return text.lower() == "true", None
            return None, f"'{text}' is not true or false"
        return text, None
    except (ValueError, InvalidOperation):
        return None, f"'{text}' is not a {kind} for {column.name} ({column.sql_type})"


def _mappings(data: LineDict, display: str, spec: SourceSpec, model: Model, catalog: Catalog, problems: list[Problem]) -> list[CompiledMapping]:
    logical = spec.logical_records()
    compiled: list[CompiledMapping] = []
    entities: dict[str, int] = {}
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
        entities.setdefault(entity.name, i)
        if len(entities) > 1:
            first = next(iter(entities))
            problems.append(Problem(display, line_of(data, where + ["target"]), f"mappings[{i}].target '{mapping['target']}' lands in {entity.name}, but the mappings already land in {first}; a source maps into one canonical entity"))
            continue

        if "constant" in mapping:
            value, message = _constant(column, str(mapping["constant"]))
            if message:
                problems.append(Problem(display, line_of(data, where + ["constant"]), f"mappings[{i}].constant: {message}"))
                continue
            compiled.append(CompiledMapping(entity, column, "", None, column.type, column.type, None, catalog.get(mapping["rule"]) if mapping.get("rule") else None, value))
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


def _source_field(data: LineDict, display: str, spec: SourceSpec, where: list, name: str, problems: list[Problem]) -> Field | None:
    logical = spec.logical_records()
    for label in logical:
        found = _field_in(spec, label, name)
        if found is not None:
            return found
    names = sorted({n for label in logical for n in spec.logical_fields(label)})
    problems.append(Problem(display, line_of(data, where), f"{'.'.join(str(w) for w in where)} '{name}' is not a field of spec {spec.label}; fields are {', '.join(names)}"))
    return None


def _code(data: LineDict, display: str, pack: DomainPack, where: list, code: str, problems: list[Problem]) -> str:
    if pack.rejections.code(code) is None:
        problems.append(Problem(display, line_of(data, where), f"{'.'.join(str(w) for w in where)} '{code}' is not in the rejection taxonomy of {pack.name}"))
    return code


def _resolution(data: LineDict, display: str, spec: SourceSpec, pack: DomainPack, model: Model, problems: list[Problem]) -> Resolution:
    raw = data.get("resolution") or {}
    feeds = pack.reference_data.feeds if pack.reference_data else ()
    account = security = transaction_code = price = None

    if "account" in raw:
        block = raw["account"]
        feed_id = block.get("feed", "account_xref")
        feed = next((f for f in feeds if f.id == feed_id), None)
        source = _source_field(data, display, spec, ["resolution", "account", "source"], block["source"], problems)
        if feed is None:
            problems.append(Problem(display, line_of(data, ["resolution", "account"]), f"resolution.account.feed '{feed_id}' is not a reference-data feed of {pack.name}; feeds are {', '.join(f.id for f in feeds) or 'none'}"))
        elif source is not None:
            account = AccountResolution(
                feed,
                source,
                bool(block.get("require_open", True)),
                _code(data, display, pack, ["resolution", "account", "not_found"], block.get("not_found", feed.rejections.not_found), problems),
                _code(data, display, pack, ["resolution", "account", "closed"], block.get("closed", "ACCOUNT_CLOSED"), problems),
            )

    if "security" in raw:
        block = raw["security"]
        feed_id = block.get("feed", "security_master")
        feed = next((f for f in feeds if f.id == feed_id), None)
        lookups: list[SecurityLookup] = []
        if feed is None:
            problems.append(Problem(display, line_of(data, ["resolution", "security"]), f"resolution.security.feed '{feed_id}' is not a reference-data feed of {pack.name}; feeds are {', '.join(f.id for f in feeds) or 'none'}"))
        else:
            for i, lookup in enumerate(block["by"]):
                where = ["resolution", "security", "by", i]
                if lookup["identifier"] not in feed.resolves.identifiers:
                    problems.append(Problem(display, line_of(data, where + ["identifier"]), f"resolution.security.by[{i}].identifier '{lookup['identifier']}' is not an identifier of feed {feed.id}; identifiers are {', '.join(feed.resolves.identifiers)}"))
                    continue
                source = _source_field(data, display, spec, where + ["source"], lookup["source"], problems)
                if source is not None:
                    lookups.append(SecurityLookup(lookup["identifier"], source))
            if lookups and not problems:
                security = SecurityResolution(
                    feed,
                    tuple(lookups),
                    bool(block.get("require_active", False)),
                    _code(data, display, pack, ["resolution", "security", "not_found"], block.get("not_found", feed.rejections.not_found), problems),
                    _code(data, display, pack, ["resolution", "security", "ambiguous"], block.get("ambiguous", feed.rejections.ambiguous), problems),
                    _code(data, display, pack, ["resolution", "security", "inactive"], block.get("inactive", "SECURITY_INACTIVE"), problems),
                )

    if "transaction_code" in raw:
        block = raw["transaction_code"]
        source = _source_field(data, display, spec, ["resolution", "transaction_code", "source"], block["source"], problems)
        transaction = model.entity("Transaction")
        canonical = [c.value for c in transaction.column("TRANSACTION_TYPE").codes] if transaction and transaction.column("TRANSACTION_TYPE") else []
        mapping = {str(k): str(v) for k, v in block["map"].items()}
        for code, kind in mapping.items():
            if canonical and kind not in canonical:
                problems.append(Problem(display, line_of(data, ["resolution", "transaction_code", "map"]), f"resolution.transaction_code.map: custodian code '{code}' maps to '{kind}', which is not a canonical transaction type; types are {', '.join(canonical)}"))
        if source is not None:
            transaction_code = TransactionCodeResolution(source, mapping, _code(data, display, pack, ["resolution", "transaction_code", "unmapped"], block.get("unmapped", "TRANSACTION_CODE_UNMAPPED"), problems))

    if "price" in raw:
        block = raw["price"]
        price = PriceResolution(block["when"], int(block.get("lookback_days", 5)), str(block.get("price_type", "CLOSE")), _code(data, display, pack, ["resolution", "price", "missing"], block.get("missing", "PRICE_MISSING"), problems))

    return Resolution(account, security, transaction_code, price)


def provided_columns(entity: Entity, model: Model, resolution: Resolution) -> set[str]:
    """Columns of the entity the resolution stage fills without a mapping."""
    names = {c.name for c in model.table_columns(entity)}
    provided = {c for c in STAGE_COLUMNS if c in names}
    if resolution.account:
        provided |= {c for c in ACCOUNT_COLUMNS if c in names}
    if resolution.security:
        provided |= {c for c in SECURITY_COLUMNS if c in names}
    if resolution.transaction_code:
        provided |= {c for c in TRANSACTION_CODE_COLUMNS if c in names}
    if resolution.price and "PRICE" in names:
        provided.add("PRICE")
    return provided


def _coverage(data: LineDict, display: str, model: Model, mappings: tuple[CompiledMapping, ...], resolution: Resolution, problems: list[Problem]) -> None:
    """Every key and required column of the target entity must come from a mapping, a constant, the resolution or the stage."""
    if not mappings:
        return
    entity = mappings[0].entity
    produced = {m.column.name for m in mappings} | provided_columns(entity, model, resolution)
    needed = [c.name for c in model.table_columns(entity) if (c.required or c.name in entity.key) and c.name not in produced]
    if needed:
        problems.append(Problem(display, line_of(data, ["mappings"]), f"{entity.name} needs {', '.join(needed)}: map a source field or a constant to each, or configure the resolution that provides it"))


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
