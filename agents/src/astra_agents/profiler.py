"""Profiler: a sample file profiled for types, nulls, value sets and record types, with drift against the
spec flagged (S5.2.1, ADR 0042, product spec Section 6).

Given a Source Spec already in the registry (`astra_knowledge.registry.SourceSpec`) and a fixed-width
sample file, the agent reuses the same reference parser generation and verification both build on
(`astra_knowledge.patterns.fixed_width.parse_fixed_width`) rather than parsing the file a second, different
way. Every field a real line produced was already run through `convert()` against the spec's declared
type; a field where that conversion failed on at least one row is exactly a field whose observed data
disagrees with what the spec says it should be — the drift this story asks for, read off logic that
already exists rather than a second, parallel type-inference of its own.

Read-only: this agent never writes into the registry, never touches production data, and takes only a
sample file already on disk. Its output is a profile for a person to review, not a spec change and not a
DQ rule; deciding what a flagged field means is exactly the "findings confirmed by reviewer" guardrail the
product spec sets for it.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from astra_knowledge.patterns.fixed_width import parse_fixed_width
from astra_knowledge.patterns.result import ParsedFile
from astra_knowledge.registry import Field, Record, SourceSpec, load_spec_file

TOP_N = 10

# A field-level problem with one of these codes is about completeness, not about the shape of the value
# the field actually holds — not the type disagreement this story flags.
COMPLETENESS_CODES = {"FIELD_REQUIRED_BLANK"}


class ProfilerError(RuntimeError):
    pass


def load_spec(path: Path) -> SourceSpec:
    path = Path(path)
    if not path.is_file():
        raise ProfilerError(f"spec file not found: {path}")
    spec, problems = load_spec_file(path)
    if problems:
        raise ProfilerError("; ".join(p.format() for p in problems))
    return spec


# -- profiling one field, one record ------------------------------------------


@dataclass(frozen=True)
class FieldProfile:
    name: str
    declared_type: str
    observed: int
    nulls: int
    distinct: int
    top_values: tuple[tuple[str, int], ...]
    type_disagreements: int
    disagreement_examples: tuple[str, ...]

    @property
    def null_rate(self) -> float:
        return self.nulls / self.observed if self.observed else 0.0

    @property
    def disagreement_rate(self) -> float:
        return self.type_disagreements / self.observed if self.observed else 0.0

    @property
    def type_ok(self) -> bool:
        return self.type_disagreements == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "declared_type": self.declared_type,
            "observed": self.observed,
            "nulls": self.nulls,
            "null_rate": round(self.null_rate, 4),
            "distinct": self.distinct,
            "top_values": [{"value": v, "count": c} for v, c in self.top_values],
            "type_ok": self.type_ok,
            "type_disagreements": self.type_disagreements,
            "disagreement_rate": round(self.disagreement_rate, 4),
            "disagreement_examples": list(self.disagreement_examples),
        }


@dataclass(frozen=True)
class RecordProfile:
    label: str
    type: str
    count: int
    fields: tuple[FieldProfile, ...]

    @property
    def flagged_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if not f.type_ok)

    def to_dict(self) -> dict:
        return {"label": self.label, "type": self.type, "count": self.count, "fields": [f.to_dict() for f in self.fields]}


@dataclass(frozen=True)
class Profile:
    spec_id: str
    spec_version: str
    document_reference: str
    lines: int
    records: tuple[RecordProfile, ...]
    # File- and record-level problems: a missing header, a line no record type matches, a
    # second header. Field-level problems already show up per field, above.
    problems: tuple[str, ...]

    @property
    def flagged(self) -> tuple[tuple[str, str], ...]:
        return tuple((r.label, name) for r in self.records for name in r.flagged_fields)

    @property
    def ok(self) -> bool:
        return not self.flagged and not self.problems

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "document_reference": self.document_reference,
            "lines": self.lines,
            "ok": self.ok,
            "problems": list(self.problems),
            "records": [r.to_dict() for r in self.records],
        }


def _record_values(record: Record, parsed: ParsedFile) -> list[dict[str, Any]]:
    if record.type == "detail":
        return [row.values for row in parsed.rows if row.record == record.label]
    metadata = parsed.metadata.get(record.label)
    return [metadata] if metadata is not None else []


def _profile_field(field: Field, record: Record, values: list[dict[str, Any]], parsed: ParsedFile, *, top_n: int) -> FieldProfile:
    observed = [v.get(field.name) for v in values]
    non_null = [v for v in observed if v is not None]
    counts = Counter(str(v) for v in non_null)
    disagreements = [p for p in parsed.problems if p.level == "field" and p.record == record.label and p.field == field.name and p.code not in COMPLETENESS_CODES]
    examples = tuple(dict.fromkeys(p.text() for p in disagreements))[:5]
    return FieldProfile(
        name=field.name,
        declared_type=field.type,
        observed=len(observed),
        nulls=len(observed) - len(non_null),
        distinct=len(counts),
        top_values=tuple(counts.most_common(top_n)),
        type_disagreements=len(disagreements),
        disagreement_examples=examples,
    )


def profile_records(spec: SourceSpec, parsed: ParsedFile, *, top_n: int = TOP_N) -> tuple[RecordProfile, ...]:
    profiles = []
    for record in spec.records:
        values = _record_values(record, parsed)
        count = parsed.counts.get(record.label, len(values))
        fields = tuple(_profile_field(f, record, values, parsed, top_n=top_n) for f in record.fields)
        profiles.append(RecordProfile(label=record.label, type=record.type, count=count, fields=fields))
    return tuple(profiles)


def profile_lines(spec: SourceSpec, lines: Iterable[str], *, document_reference: str, top_n: int = TOP_N) -> Profile:
    if spec.format != "fixed_width":
        raise ProfilerError(f"{spec.label} is a {spec.format} layout; the profiler reads fixed_width samples today")
    parsed = parse_fixed_width(spec, lines)
    records = profile_records(spec, parsed, top_n=top_n)
    problems = tuple(p.text() for p in parsed.problems if p.level in ("file", "record"))
    return Profile(spec_id=spec.id, spec_version=spec.version, document_reference=document_reference, lines=parsed.lines, records=records, problems=problems)


def run(sample: Path, spec: SourceSpec, *, top_n: int = TOP_N) -> Profile:
    sample = Path(sample)
    if not sample.is_file():
        raise ProfilerError(f"sample file not found: {sample}")
    lines = sample.read_text(encoding="utf-8").splitlines()
    return profile_lines(spec, lines, document_reference=sample.name, top_n=top_n)


# -- the report ----------------------------------------------------------------


def render_markdown(profile: Profile) -> str:
    out = [f"# Profile: {profile.spec_id} {profile.spec_version}", ""]
    out.append(f"{profile.lines} line(s) read from {profile.document_reference}. Drift found: {'no' if profile.ok else 'yes'}.")
    out.append("")
    if profile.problems:
        out.append("## File and record problems")
        out.append("")
        for p in profile.problems:
            out.append(f"- {p}")
        out.append("")
    out.append("## Record types")
    out.append("")
    out.append("| Record | Type | Count |")
    out.append("|---|---|---|")
    for r in profile.records:
        out.append(f"| {r.label} | {r.type} | {r.count} |")
    out.append("")
    for r in profile.records:
        out.append(f"## {r.label}")
        out.append("")
        out.append("| Field | Declared type | Nulls | Distinct | Top values | Type |")
        out.append("|---|---|---|---|---|---|")
        for f in r.fields:
            top = ", ".join(f"{v} ({c})" for v, c in f.top_values[:3]) or "-"
            status = "OK" if f.type_ok else f"**MISMATCH** ({f.type_disagreements}/{f.observed})"
            out.append(f"| {f.name} | {f.declared_type} | {f.nulls}/{f.observed} ({f.null_rate:.0%}) | {f.distinct} | {top} | {status} |")
        out.append("")
        flagged = [f for f in r.fields if not f.type_ok]
        if flagged:
            out.append("Flagged:")
            out.append("")
            for f in flagged:
                for example in f.disagreement_examples:
                    out.append(f"- `{r.label}.{f.name}`: {example}")
            out.append("")
    return "\n".join(out)


def write_profile(profile: Profile, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(profile), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
