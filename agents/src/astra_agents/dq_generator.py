"""DQ Generator: file, record and pair-level DQ rules proposed from a Source Spec (S5.6.1,
ADR 0046, product spec Section 6).

Every rule kind this story asks for is already declared in the spec's own structure — a
trailer's control-total field, a field's `sign_field` and its declared codes, a date-typed
field, a merge or pairing's key, a pairing's two record types — so this agent needs no model in
the loop, the same way Profiler and Pattern Matcher need none: it reads what `astra_knowledge.
registry.SourceSpec` already parsed and proposes `config-v0.schema.json`'s own `dq_rules` shape
directly, not a second, lighter format a person would have to translate.

"Thresholds default to the client's targets, never invented" (product spec Section 6) is honored
two ways. First, none of the five rule kinds this story names actually needs a fabricated number:
a control total is an exact count, an accepted-values check is the field's own declared codes, a
date check is bounded by today rather than a guessed cutoff, a key is exactly what the spec's own
`merge`/`pairing` already declares, and a pairing check is a plain not-null on a field only the
paired-in record contributes. Second, `severity` (this schema's own notion of how strict a rule
is) defaults to `"error"` — the same flat default `astra_data.dq` already uses when a config
omits one — unless an engagement's own `--targets` file overrides it per rule category; nothing
here guesses a client-specific severity that was never given.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field, replace
from pathlib import Path
from typing import Any

from astra_core.problems import Problem
from astra_core.schema import describe_error, load_validator, sorted_errors
from astra_core.yamlsource import load
from astra_knowledge.registry import Field, Record, SourceSpec, load_spec_file

CONFIG_SCHEMA = "config-v0.schema.json"
DEFAULT_SEVERITY = "error"
SEVERITIES = ("info", "warning", "error")
CATEGORIES = ("control_total", "sign_field", "date", "key", "pairing")

# A trailer field whose name ends in "count" (detail_count, record_count — every trailer this
# repository has ever seen) is treated as the file's control total; a name that does not follow
# this convention is not guessed at.
TRAILER_COUNT_FIELD = re.compile(r"(?:^|_)count$", re.IGNORECASE)


class DqGeneratorError(RuntimeError):
    pass


def load_spec(path: Path) -> SourceSpec:
    path = Path(path)
    if not path.is_file():
        raise DqGeneratorError(f"spec file not found: {path}")
    spec, problems = load_spec_file(path)
    if problems:
        raise DqGeneratorError("; ".join(p.format() for p in problems))
    return spec


# -- a client's own targets, read when given, never invented --------------------


@dataclass(frozen=True)
class Targets:
    severity: dict[str, str] = dc_field(default_factory=dict)

    def for_category(self, category: str) -> str:
        return self.severity.get(category, DEFAULT_SEVERITY)


def load_targets(path: Path | None) -> Targets:
    if path is None:
        return Targets()
    path = Path(path)
    if not path.is_file():
        raise DqGeneratorError(f"targets file not found: {path}")
    data = load(path.read_text(encoding="utf-8"))
    severities = dict((data or {}).get("severity") or {})
    for category, severity in severities.items():
        if category not in CATEGORIES:
            raise DqGeneratorError(f"'{category}' is not a DQ rule category this agent generates; categories are {', '.join(CATEGORIES)}")
        if severity not in SEVERITIES:
            raise DqGeneratorError(f"'{severity}' is not a severity; severities are {', '.join(SEVERITIES)}")
    return Targets(severity=severities)


# -- one proposed rule -----------------------------------------------------------


@dataclass(frozen=True)
class DqRule:
    category: str  # this story's own vocabulary: control_total, sign_field, date, key, pairing
    id: str
    kind: str  # config-v0's own dq_rules[].kind
    level: str
    check: str
    record: str | None = None
    field: str | None = None
    fields: tuple[str, ...] = ()
    trailer_field: str | None = None
    aggregate: str | None = None
    values: tuple[str, ...] = ()
    condition: str | None = None
    citation: dict | None = None
    severity: str = DEFAULT_SEVERITY

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind, "level": self.level, "check": self.check, "severity": self.severity}
        if self.record:
            d["record"] = self.record
        if self.field:
            d["field"] = self.field
        if self.fields:
            d["fields"] = list(self.fields)
        if self.trailer_field:
            d["trailer_field"] = self.trailer_field
        if self.aggregate:
            d["aggregate"] = self.aggregate
        if self.values:
            d["values"] = list(self.values)
        if self.condition:
            d["condition"] = self.condition
        if self.citation:
            d["citation"] = self.citation
        return d


def _citation_of(field: Field) -> dict | None:
    if field.citation.page is None:
        return None
    c: dict[str, Any] = {"page": field.citation.page}
    if field.citation.line:
        c["line"] = field.citation.line
    return c


def _detail_records(spec: SourceSpec) -> list[Record]:
    return [r for r in spec.records if r.type == "detail"]


def _record_ref(spec: SourceSpec, record: Record) -> str | None:
    """`record` is only needed when the spec has more than one detail record type — the same
    rule config-v0.schema.json itself states for dq_rules[].record."""
    return record.label if len(_detail_records(spec)) > 1 else None


# -- the five rule kinds ----------------------------------------------------------


def generate_control_total_rules(spec: SourceSpec) -> list[DqRule]:
    details = _detail_records(spec)
    trailer = next((r for r in spec.records if r.type == "trailer"), None)
    if trailer is None or len(details) != 1:
        return []
    detail = details[0]
    count_field = next((f for f in trailer.fields if TRAILER_COUNT_FIELD.search(f.name)), None)
    if count_field is None:
        return []
    return [
        DqRule(
            category="control_total",
            id=f"{detail.label}_control_total",
            kind="control_total",
            level="file",
            trailer_field=count_field.name,
            aggregate="count",
            check=f"trailer {count_field.name} equals the count of {detail.label} rows",
            citation=_citation_of(count_field),
        )
    ]


def generate_sign_field_rules(spec: SourceSpec) -> list[DqRule]:
    rules = []
    for record in spec.records:
        for f in record.fields:
            if not f.sign_field:
                continue
            sign = record.field(f.sign_field)
            if sign is None or not sign.codes:
                continue
            values = tuple(value for value, _ in sign.codes)
            rules.append(
                DqRule(
                    category="sign_field",
                    id=f"{record.label}_{sign.name}_accepted_values",
                    kind="accepted_values",
                    level="record",
                    record=_record_ref(spec, record),
                    field=sign.name,
                    values=values,
                    check=f"{sign.name} is one of the declared codes ({', '.join(values)})",
                    citation=_citation_of(sign),
                )
            )
    return rules


def generate_date_rules(spec: SourceSpec) -> list[DqRule]:
    rules = []
    for record in spec.records:
        for f in record.fields:
            if f.type != "date":
                continue
            rules.append(
                DqRule(
                    category="date",
                    id=f"{record.label}_{f.name}_not_future",
                    kind="condition",
                    level="record" if record.type == "detail" else "file",
                    record=_record_ref(spec, record) if record.type == "detail" else None,
                    field=f.name,
                    condition=f"{f.name.upper()} <= CURRENT_DATE()",
                    check=f"{f.name} is never in the future",
                    citation=_citation_of(f),
                )
            )
    return rules


def generate_key_rules(spec: SourceSpec) -> list[DqRule]:
    rules = []
    if spec.merge is not None and spec.merge.keys:
        details = _detail_records(spec)
        detail = details[0] if len(details) == 1 else None
        rules.append(
            DqRule(
                category="key",
                id=f"{spec.id}_key_unique",
                kind="unique",
                level="record",
                record=_record_ref(spec, detail) if detail else None,
                fields=tuple(spec.merge.keys),
                check=f"({', '.join(spec.merge.keys)}) identifies one row",
            )
        )
    for pairing in spec.pairings:
        if not pairing.keys:
            continue
        rules.append(
            DqRule(
                category="key",
                id=f"{pairing.name}_key_unique",
                kind="unique",
                level="pair",
                record=pairing.name,
                fields=tuple(pairing.keys),
                check=f"({', '.join(pairing.keys)}) identifies one {pairing.name}",
            )
        )
    return rules


def generate_pairing_rules(spec: SourceSpec) -> list[DqRule]:
    rules = []
    for pairing in spec.pairings:
        if len(pairing.records) < 2:
            continue
        second = spec.record(pairing.records[1])
        if second is None:
            continue
        excluded = set(pairing.keys) | {"filler", "record_type"}
        candidate = next((f for f in second.fields if f.name not in excluded), None)
        if candidate is None:
            continue
        rules.append(
            DqRule(
                category="pairing",
                id=f"{pairing.name}_{candidate.name}_paired",
                kind="not_null",
                level="pair",
                record=pairing.name,
                field=candidate.name,
                check=f"every {pairing.records[0]} record has a matching {pairing.records[1]} record (its {candidate.name} is present)",
                citation=_citation_of(candidate),
            )
        )
    return rules


GENERATORS = (generate_control_total_rules, generate_sign_field_rules, generate_date_rules, generate_key_rules, generate_pairing_rules)


# -- assembling and validating the draft ------------------------------------------


def validate_dq_rules(rules: tuple[DqRule, ...], spec: SourceSpec) -> list[Problem]:
    """Re-checks the generated rules against the real config-v0 schema, wrapped in a minimal stub
    of the other required config fields — the same "propose, then independently re-validate
    against the real schema" shape every other agent in this plane already uses."""
    stub = {
        "config_version": 0,
        "source": {"id": "stub", "custodian": "stub", "file_type": spec.file_type, "tier": "simple"},
        "spec": {"id": spec.id, "version": spec.version},
        "target_profile": "snowflake_iceberg",
        "domain_pack": "custodial",
        "effective_from": "2026-01-01",
        "owner": {"name": "stub", "email": "stub@example.com"},
        "dq_rules": [r.to_dict() for r in rules],
    }
    validator = load_validator("astra_data.schemas", CONFIG_SCHEMA)
    return [Problem(spec.label, None, describe_error(e)) for e in sorted_errors(validator, stub) if list(e.absolute_path)[:1] == ["dq_rules"]]


@dataclass(frozen=True)
class DqDraft:
    spec_id: str
    spec_version: str
    rules: tuple[DqRule, ...]
    problems: tuple[Problem, ...]

    @property
    def valid(self) -> bool:
        return not self.problems

    @property
    def by_category(self) -> dict[str, tuple[DqRule, ...]]:
        out: dict[str, list[DqRule]] = {c: [] for c in CATEGORIES}
        for r in self.rules:
            out[r.category].append(r)
        return {k: tuple(v) for k, v in out.items()}

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "valid": self.valid,
            "rules": [r.to_dict() | {"category": r.category} for r in self.rules],
            "problems": [p.format() for p in self.problems],
        }


def generate(spec: SourceSpec, *, targets: Targets = Targets()) -> DqDraft:
    raw: list[DqRule] = []
    for gen in GENERATORS:
        raw.extend(gen(spec))
    rules = tuple(replace(r, severity=targets.for_category(r.category)) for r in raw)
    problems = validate_dq_rules(rules, spec)
    return DqDraft(spec_id=spec.id, spec_version=spec.version, rules=rules, problems=tuple(problems))


def run(spec_path: Path, *, targets_path: Path | None = None) -> DqDraft:
    spec = load_spec(spec_path)
    targets = load_targets(targets_path)
    return generate(spec, targets=targets)


# -- the report ------------------------------------------------------------------


def render_markdown(draft: DqDraft) -> str:
    out = [f"# DQ Generator draft: {draft.spec_id} {draft.spec_version}", ""]
    out.append(f"{len(draft.rules)} rule(s) proposed across {sum(1 for c in draft.by_category.values() if c)} categor(ies). Valid against the config schema: {'yes' if draft.valid else 'no'}.")
    out.append("")
    if draft.problems:
        out.append("## Schema problems")
        out.append("")
        for p in draft.problems:
            out.append(f"- {p.format()}")
        out.append("")
    for category in CATEGORIES:
        rules = draft.by_category[category]
        out.append(f"## {category} ({len(rules)})")
        out.append("")
        if not rules:
            out.append("None generated.")
            out.append("")
            continue
        out.append("| Id | Kind | Level | Check | Severity |")
        out.append("|---|---|---|---|---|")
        for r in rules:
            out.append(f"| {r.id} | {r.kind} | {r.level} | {r.check} | {r.severity} |")
        out.append("")
    return "\n".join(out)


def render_dq_rules_yaml(draft: DqDraft) -> str:
    """A `dq_rules:` block in the exact shape a real config's own section takes, ready to paste in."""
    lines = ["# Proposed dq_rules, ready to paste into a config's own dq_rules: block after review.", "dq_rules:"]
    for r in draft.rules:
        lines.append(f"  - id: {r.id}")
        lines.append(f"    kind: {r.kind}")
        lines.append(f"    level: {r.level}")
        lines.append(f"    check: {r.check!r}")
        if r.record:
            lines.append(f"    record: {r.record}")
        if r.field:
            lines.append(f"    field: {r.field}")
        if r.fields:
            lines.append(f"    fields: [{', '.join(r.fields)}]")
        if r.trailer_field:
            lines.append(f"    trailer_field: {r.trailer_field}")
        if r.aggregate:
            lines.append(f"    aggregate: {r.aggregate}")
        if r.values:
            lines.append(f"    values: [{', '.join(r.values)}]")
        if r.condition:
            lines.append(f"    condition: {r.condition!r}")
        if r.citation:
            lines.append(f"    citation: {json.dumps(r.citation)}")
        lines.append(f"    severity: {r.severity}")
    lines.append("")
    return "\n".join(lines)


def write_draft(draft: DqDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (out / "dq_rules.yaml").write_text(render_dq_rules_yaml(draft), encoding="utf-8", newline="\n")
    return report_path, data_path
