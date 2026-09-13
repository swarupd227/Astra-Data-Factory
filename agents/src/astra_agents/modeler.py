"""Modeler: a Source Spec mapped to the domain pack's canonical model, with resolution
parameters, DQ suggestions and a draft config in the same shape `configs/<id>.yaml` uses
(S5.5.1, ADR 0045, product spec Section 6).

Three guardrails are enforced in code, not merely asked for in the prompt:

- **A mapping can only target a column the CDM actually has.** The tool's `target` field is a
  closed enum built from the domain pack's own latest model (`astra_knowledge.cdm`) — the model
  cannot invent a canonical column any more than Spec Reader's tool can invent a field without a
  citation. A field with no good target goes in `unmapped`, never forced onto the nearest column.
- **A transform can only be one the renderers implement.** Every proposed `transform` call is
  parsed with `astra_data.transforms.parse_transform`, the same function the compiler itself
  uses; an unparseable or unknown transform is a problem on that mapping, not a silent pass.
- **A breaking CDM change is a request, never applied.** When a field genuinely has nowhere to
  go in the current model, the model may propose a new column instead of forcing a bad mapping.
  `build_cdm_change_request` builds a real candidate `Model` and calls `astra_knowledge.cdm.diff`
  — the same function the domain pack's own version-to-version checks use — to classify it
  breaking or additive; nothing is ever written into `domains/<pack>/cdm/`. A steward reviews the
  request and authors the real version bump themselves, the same "agent proposes, human applies"
  shape ADR 0013 already describes for this agent.

A mapping or a proposed new rule the model cannot confidently stand behind without checking
against the legacy Loader's actual behavior — an ambiguous sign convention, a business rule the
spec states but a real file might contradict — is tagged `confirm_with_loader` (the schema's
`tags` only allows lower-case; a report renders it as CONFIRM_WITH_LOADER). A new rule this way
is written the same way Rule Recovery's are: `status: recovered`, never `confirmed` by the agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from astra_core.problems import Problem
from astra_core.schema import describe_error, load_validator, sorted_errors
from astra_data.transforms import TRANSFORMS, TransformError, parse_transform
from astra_knowledge.cdm import Change, Column, DomainPack, Entity, Model, diff, load_pack
from astra_knowledge.registry import SourceSpec, load_spec_file
from astra_knowledge.rules import Catalog, Citation, HistoryEntry, Owner, Rule, render_rule
from astra_agents.rule_recovery import dedupe_name, rule_to_dict, validate_rule

CONFIG_SCHEMA = "config-v0.schema.json"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 32000

EXTRACTION_TOOL_NAME = "draft_config"
CONFIRM_WITH_LOADER = "confirm_with_loader"
DQ_KINDS = ("not_null", "unique")  # the conservative subset a Modeler suggests; DQ Generator (F5.6) owns the rest


class ModelerError(RuntimeError):
    pass


def load_spec(path: Path) -> SourceSpec:
    path = Path(path)
    if not path.is_file():
        raise ModelerError(f"spec file not found: {path}")
    spec, problems = load_spec_file(path)
    if problems:
        raise ModelerError("; ".join(p.format() for p in problems))
    return spec


def load_domain_pack(root: Path) -> DomainPack:
    pack, problems = load_pack(Path(root))
    if problems:
        raise ModelerError("; ".join(p.format() for p in problems))
    return pack


def load_known_rule_ids(root: Path) -> frozenset[str]:
    catalog, problems = Catalog.load(Path(root))
    if problems:
        raise ModelerError("; ".join(p.format() for p in problems))
    return frozenset(catalog.ids())


# -- reading the CDM into a closed vocabulary ----------------------------------


def cdm_targets(model: Model) -> tuple[str, ...]:
    """Every `entity.field` a mapping may target — lineage columns excluded; the pipeline fills those, not a mapping."""
    return tuple(sorted(f"{e.name.lower()}.{c.name.lower()}" for e in model.entities for c in e.columns))


def cdm_entities(model: Model) -> tuple[str, ...]:
    return tuple(sorted(e.name for e in model.entities))


def _target_lookup(model: Model) -> dict[str, tuple[Entity, Column]]:
    return {f"{e.name.lower()}.{c.name.lower()}": (e, c) for e in model.entities for c in e.columns}


# -- the prompt and the tool -----------------------------------------------------


def system_prompt(*, custodian: str, file_type: str, domain: str) -> str:
    return (
        f"You are mapping a '{custodian}' {file_type} Source Spec to the '{domain}' canonical data model. "
        f"Call {EXTRACTION_TOOL_NAME} exactly once with everything you find.\n\n"
        "For every spec field that has a clear home in the canonical model, propose one mapping: the target "
        "(only from the list you were given — never invent a column, never guess one that is close but not "
        "exact), the spec field it comes from (or a constant, when the value never varies), and a transform "
        "when the raw value needs one (only from the vocabulary you were given, written as name(arguments), "
        "for example signed_implied_decimal(13, 5)).\n\n"
        "If a mapping depends on a business rule already in the catalog, name it as existing_rule. If it "
        "depends on a rule that is not in the catalog yet, propose one as new_rule the same way Rule Recovery "
        "does — plain words, a citation to the spec page it comes from, never a status beyond newly found. Set "
        "new_rule.confirm_with_loader to true when the rule is the kind that really needs checking against "
        "what the legacy Loader actually does before anyone trusts it — an ambiguous sign convention, a "
        "business rule the document states but a real file might contradict — and false when the spec is "
        "clear enough on its own.\n\n"
        "A field with no good target in the model at all — not close, not almost, genuinely missing — goes in "
        "cdm_change_requests: the entity, the column you propose, its type, whether it must be required, and "
        "why. You are not adding it to the model; you are asking for it. A field you cannot confidently map "
        "for any other reason goes in unmapped with a reason, never forced onto the nearest target.\n\n"
        "If the spec's own fields let you recognize how accounts or securities resolve to platform records "
        "(an account number field, a CUSIP/ISIN/SEDOL/TICKER field), propose resolution. Suggest a small "
        "number of high-confidence dq_suggestions — not_null for a field the spec already marks required, "
        "unique for a field that looks like it identifies a row — not a full data quality suite; a separate "
        "agent owns that."
    )


def _extraction_tool(targets: tuple[str, ...], entities: tuple[str, ...]) -> dict[str, Any]:
    mapping_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["target"],
        "properties": {
            "target": {"type": "string", "enum": list(targets)},
            "source_field": {"type": "string"},
            "constant": {"type": ["string", "number", "boolean"]},
            "record": {"type": "string"},
            "transform": {"type": "string"},
            "existing_rule": {"type": "string"},
            "new_rule": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "text", "class", "confirm_with_loader"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "text": {"type": "string", "minLength": 20},
                    "class": {"enum": ["ingestion", "business", "normalisation"]},
                    "confirm_with_loader": {"type": "boolean"},
                },
            },
        },
    }
    return {
        "name": EXTRACTION_TOOL_NAME,
        "description": "Record every field mapping, CDM change request, resolution suggestion, DQ suggestion and unmapped field found.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mappings", "cdm_change_requests", "unmapped"],
            "properties": {
                "mappings": {"type": "array", "items": mapping_item},
                "cdm_change_requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["entity", "column_name", "type", "required", "reason", "source_field"],
                        "properties": {
                            "entity": {"type": "string", "enum": list(entities)},
                            "column_name": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
                            "type": {"enum": ["string", "integer", "decimal", "date", "timestamp", "boolean"]},
                            "precision": {"type": "integer", "minimum": 1},
                            "scale": {"type": "integer", "minimum": 0},
                            "required": {"type": "boolean"},
                            "description": {"type": "string"},
                            "reason": {"type": "string"},
                            "source_field": {"type": "string"},
                        },
                    },
                },
                "resolution": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "account": {"type": "object", "additionalProperties": False, "required": ["source"], "properties": {"source": {"type": "string"}}},
                        "security": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["by"],
                            "properties": {"by": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["identifier", "source"], "properties": {"identifier": {"type": "string"}, "source": {"type": "string"}}}}},
                        },
                    },
                },
                "dq_suggestions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "check"],
                        "properties": {"kind": {"enum": list(DQ_KINDS)}, "field": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}, "check": {"type": "string"}},
                    },
                },
                "unmapped": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": False, "required": ["source_field", "reason"], "properties": {"source_field": {"type": "string"}, "reason": {"type": "string"}}},
                },
            },
        },
    }


@dataclass(frozen=True)
class Extraction:
    mappings: list[dict]
    cdm_change_requests: list[dict]
    unmapped: list[dict]
    resolution: dict | None = None
    dq_suggestions: list[dict] | None = None


class LlmClient(Protocol):
    def extract(self, *, system: str, content: str, targets: tuple[str, ...], entities: tuple[str, ...]) -> Extraction: ...


def _extraction_from_tool_input(data: dict, *, stop_reason: str | None = None) -> Extraction:
    missing = [k for k in ("mappings", "cdm_change_requests", "unmapped") if k not in data]
    if missing:
        if stop_reason == "max_tokens":
            raise ModelerError(
                f"the model's response was cut off at the token limit before it finished calling {EXTRACTION_TOOL_NAME} "
                f"(missing {', '.join(missing)}); pass a higher --max-tokens"
            )
        raise ModelerError(f"the model's {EXTRACTION_TOOL_NAME} call is missing {', '.join(missing)}; stop reason {stop_reason or 'unknown'}")
    return Extraction(
        mappings=data["mappings"],
        cdm_change_requests=data["cdm_change_requests"],
        unmapped=data["unmapped"],
        resolution=data.get("resolution"),
        dq_suggestions=data.get("dq_suggestions"),
    )


def _spec_text(spec: SourceSpec) -> str:
    lines = [f"Source Spec {spec.id} {spec.version} ({spec.file_type}, {spec.format})"]
    for record in spec.records:
        lines.append(f"\nrecord {record.label} ({record.type}):")
        for f in record.fields:
            if f.name == "filler":
                continue
            picture = f" picture={f.picture.text}" if f.picture else ""
            lines.append(f"  - {f.name}: type={f.type}{picture} required={f.required} citation=page {f.citation.page}" + (f" line {f.citation.line}" if f.citation.line else ""))
    return "\n".join(lines)


class AnthropicClient:
    """`LlmClient` against the real Anthropic API. See astra_agents.spec_reader.AnthropicClient
    for why streaming and a high token budget matter; duplicated here rather than shared, for the
    same reason astra_agents.rule_recovery.AnthropicClient is."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, max_tokens: int = MAX_TOKENS) -> None:
        self.model = model
        self._api_key = api_key
        self.max_tokens = max_tokens

    def extract(self, *, system: str, content: str, targets: tuple[str, ...], entities: tuple[str, ...]) -> Extraction:
        try:
            import anthropic
        except ImportError as exc:
            raise ModelerError("calling the model needs the anthropic package; pip install 'astra-agents[llm]'") from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        tool = _extraction_tool(targets, entities)
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            tools=[tool],
            tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        ) as stream:
            message = stream.get_final_message()
        stop_reason = getattr(message, "stop_reason", None)
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == EXTRACTION_TOOL_NAME:
                return _extraction_from_tool_input(block.input, stop_reason=stop_reason)
        raise ModelerError(f"the model did not call {EXTRACTION_TOOL_NAME}; stop reason {stop_reason or 'unknown'}")


# -- CDM change requests: a real diff, never applied ----------------------------


@dataclass(frozen=True)
class CdmChangeRequest:
    entity: str
    column: Column
    reason: str
    source_field: str
    change: Change

    @property
    def breaking(self) -> bool:
        return self.change.breaking


def build_cdm_change_request(model: Model, raw: dict) -> CdmChangeRequest:
    entity = model.entity(raw["entity"])
    if entity is None:
        raise ModelerError(f"cdm_change_requests names entity '{raw['entity']}', which is not in {model.label}")
    column = Column(
        name=raw["column_name"],
        type=raw["type"],
        description=raw.get("description", raw["reason"]),
        required=bool(raw.get("required", False)),
        precision=raw.get("precision"),
        scale=raw.get("scale"),
    )
    candidate_entity = replace(entity, columns=entity.columns + (column,))
    candidate_model = replace(model, entities=tuple(candidate_entity if e.name == entity.name else e for e in model.entities))
    changes = [c for c in diff(model, candidate_model) if c.entity == entity.name]
    change = next((c for c in changes if column.name in c.message), changes[0] if changes else Change(bool(column.required), f"{entity.name}: column {column.name} added", entity.name))
    return CdmChangeRequest(entity=entity.name, column=column, reason=raw["reason"], source_field=raw["source_field"], change=change)


# -- mappings, resolution, DQ suggestions ----------------------------------------


@dataclass(frozen=True)
class DraftMapping:
    target: str
    source: str | None
    constant: Any | None
    record: str | None
    transform: str | None
    existing_rule: str | None
    new_rule_id: str | None
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"target": self.target}
        if self.source is not None:
            d["source"] = self.source
        if self.constant is not None:
            d["constant"] = self.constant
        if self.record is not None:
            d["record"] = self.record
        if self.transform is not None:
            d["transform"] = self.transform
        rule = self.existing_rule or self.new_rule_id
        if rule:
            d["rule"] = rule
        return d

    def canonical_item(self) -> str:
        item = f"map:{self.target}"
        item += f"<-{self.source}" if self.source is not None else f"<-const:{self.constant}"
        if self.transform:
            item += f"~{self.transform}"
        return item


def _translate_mapping(raw: dict, *, targets: tuple[str, ...], known_rule_ids: frozenset[str], spec: SourceSpec, group: str, owner: Owner, taken_rule_names: set[str], at: datetime) -> tuple[DraftMapping, Rule | None]:
    problems: list[str] = []
    target = raw["target"]
    if target not in targets:
        problems.append(f"target '{target}' is not a column of the canonical model")
    source = raw.get("source_field")
    constant = raw.get("constant")
    if source is None and constant is None:
        problems.append("neither source_field nor constant was given")

    transform = raw.get("transform")
    if transform:
        try:
            parse_transform(transform)
        except TransformError as exc:
            problems.append(str(exc))

    existing_rule = raw.get("existing_rule")
    if existing_rule and existing_rule not in known_rule_ids:
        problems.append(f"existing_rule '{existing_rule}' is not in the rule catalog")
        existing_rule = None

    new_rule_data = raw.get("new_rule")
    new_rule: Rule | None = None
    if new_rule_data:
        field = None
        if source:
            record = spec.record(raw["record"]) if raw.get("record") else None
            field = record.field(source) if record else next((f for r in spec.records for f in r.fields if f.name == source), None)
        page = field.citation.page if field else None
        line = field.citation.line if field else None
        name = dedupe_name(new_rule_data["name"], taken_rule_names)
        taken_rule_names.add(name)
        tags = (CONFIRM_WITH_LOADER,) if new_rule_data.get("confirm_with_loader") else ()
        new_rule = Rule(
            id=f"{group}.{name}",
            text=" ".join(new_rule_data["text"].split()),
            class_=new_rule_data["class"],
            owner=owner,
            status="recovered",
            citation=Citation("spec", spec_id=spec.id, spec_version=spec.version, page=page or 1, line=line),
            history=(HistoryEntry("recovered", "modeler", at, note=f"Proposed while mapping {target}."),),
            path=Path("rules") / group / f"{name}.yaml",
            tags=tags,
        )
        rule_problems = validate_rule(rule_to_dict(new_rule))
        problems.extend(p.message for p in rule_problems)

    mapping = DraftMapping(
        target=target,
        source=source,
        constant=constant,
        record=raw.get("record"),
        transform=transform,
        existing_rule=existing_rule,
        new_rule_id=new_rule.id if new_rule else None,
        problems=tuple(problems),
    )
    return mapping, new_rule


@dataclass(frozen=True)
class DqSuggestion:
    kind: str
    field: str | None
    fields: tuple[str, ...]
    check: str

    def canonical_item(self) -> str:
        target = self.field or ",".join(self.fields)
        return f"dq:{self.kind}:{target}"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"kind": self.kind, "check": self.check}
        if self.field:
            d["field"] = self.field
        if self.fields:
            d["fields"] = list(self.fields)
        return d


@dataclass(frozen=True)
class ConfigDraft:
    spec_id: str
    spec_version: str
    custodian: str
    file_type: str
    tier: str
    domain: str
    mappings: tuple[DraftMapping, ...]
    new_rules: tuple[Rule, ...]
    cdm_change_requests: tuple[CdmChangeRequest, ...]
    resolution: dict
    dq_suggestions: tuple[DqSuggestion, ...]
    unmapped: tuple[dict, ...]

    @property
    def valid(self) -> bool:
        return all(m.valid for m in self.mappings)

    @property
    def breaking_change_requests(self) -> tuple[CdmChangeRequest, ...]:
        return tuple(r for r in self.cdm_change_requests if r.breaking)

    @property
    def confirm_with_loader(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.new_rules if CONFIRM_WITH_LOADER in r.tags)

    @property
    def ok(self) -> bool:
        """False when a person needs to look at this draft before trusting it: an invalid
        mapping, a breaking CDM change request, or a rule tagged CONFIRM_WITH_LOADER. A
        non-breaking change request or an unmapped field is informational, not a block —
        the same way Spec Reader's own unparsed list does not fail its run."""
        return self.valid and not self.breaking_change_requests and not self.confirm_with_loader

    def canonical_items(self) -> tuple[str, ...]:
        items = [m.canonical_item() for m in self.mappings if m.valid]
        if self.resolution.get("account"):
            items.append(f"resolution:account.source={self.resolution['account']['source']}")
        for by in (self.resolution.get("security") or {}).get("by", []):
            items.append(f"resolution:security.by={by['identifier']}:{by['source']}")
        items.extend(d.canonical_item() for d in self.dq_suggestions)
        return tuple(items)

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "custodian": self.custodian,
            "file_type": self.file_type,
            "tier": self.tier,
            "domain": self.domain,
            "valid": self.valid,
            "ok": self.ok,
            "mappings": [m.to_dict() | {"valid": m.valid, "problems": list(m.problems)} for m in self.mappings],
            "new_rules": [{"id": r.id, "confirm_with_loader": CONFIRM_WITH_LOADER in r.tags} for r in self.new_rules],
            "cdm_change_requests": [{"entity": r.entity, "column": r.column.name, "type": r.column.type, "breaking": r.breaking, "reason": r.reason, "source_field": r.source_field, "change": r.change.message} for r in self.cdm_change_requests],
            "resolution": self.resolution,
            "dq_suggestions": [d.to_dict() for d in self.dq_suggestions],
            "unmapped": list(self.unmapped),
        }


def build_draft(
    extraction: Extraction,
    *,
    spec: SourceSpec,
    model: Model,
    custodian: str,
    tier: str,
    group: str,
    owner: Owner,
    known_rule_ids: frozenset[str] = frozenset(),
    clock=lambda: datetime.now(timezone.utc),
) -> ConfigDraft:
    at = clock()
    targets = cdm_targets(model)
    taken_rule_names: set[str] = set()
    mappings: list[DraftMapping] = []
    new_rules: list[Rule] = []
    for raw in extraction.mappings:
        mapping, new_rule = _translate_mapping(raw, targets=targets, known_rule_ids=known_rule_ids, spec=spec, group=group, owner=owner, taken_rule_names=taken_rule_names, at=at)
        mappings.append(mapping)
        if new_rule is not None:
            new_rules.append(new_rule)

    change_requests = tuple(build_cdm_change_request(model, r) for r in extraction.cdm_change_requests)
    dq = tuple(DqSuggestion(kind=d["kind"], field=d.get("field"), fields=tuple(d.get("fields") or ()), check=d["check"]) for d in (extraction.dq_suggestions or ()))

    return ConfigDraft(
        spec_id=spec.id,
        spec_version=spec.version,
        custodian=custodian,
        file_type=spec.file_type,
        tier=tier,
        domain=model.domain,
        mappings=tuple(mappings),
        new_rules=tuple(new_rules),
        cdm_change_requests=change_requests,
        resolution=extraction.resolution or {},
        dq_suggestions=dq,
        unmapped=tuple(extraction.unmapped),
    )


def run(spec: SourceSpec, pack: DomainPack, client: LlmClient, *, custodian: str, group: str, owner_name: str, owner_email: str, tier: str | None = None, known_rule_ids: frozenset[str] = frozenset()) -> ConfigDraft:
    from astra_agents.pattern_matcher import assign_tier

    model = pack.latest
    system = system_prompt(custodian=custodian, file_type=spec.file_type, domain=pack.name)
    content = _spec_text(spec)
    extraction = client.extract(system=system, content=content, targets=cdm_targets(model), entities=cdm_entities(model))
    return build_draft(extraction, spec=spec, model=model, custodian=custodian, tier=tier or assign_tier(spec), group=group, owner=Owner(owner_name, owner_email), known_rule_ids=known_rule_ids)


# -- the report ------------------------------------------------------------------


def render_markdown(draft: ConfigDraft) -> str:
    out = [f"# Modeler draft: {draft.spec_id} {draft.spec_version} ({draft.custodian})", ""]
    out.append(f"{len(draft.mappings)} mapping(s) proposed. Valid: {'yes' if draft.valid else 'no'}.")
    out.append("")
    out.append("## Mappings")
    out.append("")
    out.append("| Target | Source | Transform | Rule | Valid |")
    out.append("|---|---|---|---|---|")
    for m in draft.mappings:
        src = m.source or f"const:{m.constant}"
        rule = m.existing_rule or m.new_rule_id or "-"
        if m.new_rule_id:
            owning = next((r for r in draft.new_rules if r.id == m.new_rule_id), None)
            if owning and CONFIRM_WITH_LOADER in owning.tags:
                rule += " **(CONFIRM_WITH_LOADER)**"
        out.append(f"| {m.target} | {src} | {m.transform or '-'} | {rule} | {'yes' if m.valid else 'no: ' + '; '.join(m.problems)} |")
    out.append("")
    if draft.resolution:
        out.append("## Resolution")
        out.append("")
        out.append(f"`{json.dumps(draft.resolution)}`")
        out.append("")
    if draft.dq_suggestions:
        out.append("## DQ suggestions")
        out.append("")
        for d in draft.dq_suggestions:
            out.append(f"- {d.kind}: {d.field or ', '.join(d.fields)} — {d.check}")
        out.append("")
    out.append("## CDM change requests — never applied, always a request")
    out.append("")
    if draft.cdm_change_requests:
        out.append("| Entity | Column | Type | Breaking | Reason |")
        out.append("|---|---|---|---|---|")
        for r in draft.cdm_change_requests:
            out.append(f"| {r.entity} | {r.column.name} | {r.column.type} | {'**yes**' if r.breaking else 'no'} | {r.reason} |")
    else:
        out.append("None proposed.")
    out.append("")
    out.append("## Unmapped")
    out.append("")
    if draft.unmapped:
        out.append("| Field | Reason |")
        out.append("|---|---|")
        for u in draft.unmapped:
            out.append(f"| {u['source_field']} | {u['reason']} |")
    else:
        out.append("Nothing was left unmapped.")
    out.append("")
    return "\n".join(out)


def write_draft(draft: ConfigDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    for rule in draft.new_rules:
        rule_path = out / "rules" / rule.id.split(".", 1)[0] / f"{rule.id.split('.', 1)[1]}.yaml"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(render_rule(rule), encoding="utf-8", newline="\n")
    report_path = out / "report.md"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path = out / "report.json"
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
