"""Spec registry viewer: browse a Source Spec with its citations and compare two versions, so
layout changes are understood before they bite (S6.3.4, ADR 0060, product spec Section 3, the
engineer's own top task).

Every field this module lists is read straight off `astra_knowledge.registry` — never
re-extracted, never re-cited. A citation already lives on every `Field` (`Field.citation`, a
`Citation` of `page`/`line`/`document`) since the Spec Reader (S5.1.1) puts one there for every
field it drafts; this module's own `citation_link` turns it into a real, openable reference, the
same job `astra_control.diff_review.citation_link` already does for a rule's own citation — a
different, simpler `Citation` shape (registry specs cite a page in a layout document; rule
catalog entries cite a spec page *or* a line of legacy code), so this module writes its own
rather than importing that one for a shape that does not fit.

**No spec-version diff exists anywhere in this repository before this story.** `astra_knowledge.
cdm.diff` compares two canonical-model versions; `astra_verification.replay.config_diff` compares
two compiled configs — neither reads a `SourceSpec`. `compare` is new, deliberately shaped like
`config_diff`'s own `FieldDiff` (`kind`: `added`/`removed`/`changed`, `group`/`column`, `before`/
`after`, a plain description) with one addition AC2 asks for by name: `shifted` — a field present
in both versions, under the same name, whose own `position` (`start`, `length`) differs. A
position shift is the one kind of spec change a config or a downstream mapping can silently break
on without any other field of the row ever changing at all, which is exactly why the story wants
it called out on its own rather than folded into a generic "changed."

Fields are matched between two versions by `(record label, field name)` — the same pair
`astra_knowledge.registry.SourceSpec.fields()` already returns flattened across every record, so
a whole record added or removed falls out naturally as every one of its own fields showing added
or removed, with no special case needed for the record level.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astra_knowledge.registry import Citation, Field, Registry, SourceSpec


class SpecViewerError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def load_registry(specs_dir: Path, root: Path | None = None) -> Registry:
    registry, problems = Registry.load(Path(specs_dir), repo_root=root)
    if problems:
        raise SpecViewerError("; ".join(p.format() for p in problems))
    return registry


def load_spec(registry: Registry, spec_id: str, version: str) -> SourceSpec:
    spec = registry.get(spec_id, version)
    if spec is None:
        raise SpecViewerError(f"no spec '{spec_id}' version '{version}' in this registry")
    return spec


# -- citations -----------------------------------------------------------------


def citation_link(citation: Citation, *, fallback_document: str) -> str:
    """A real, openable reference — the one thing Citation.text() does not give: a document name
    and a page/line fragment, matching astra_control.diff_review.citation_link's own job for a
    different Citation shape (module docstring)."""
    document = citation.document or fallback_document
    if citation.page is None:
        return document
    fragment = f"#page={citation.page}"
    if citation.line:
        fragment += f"&line={citation.line}"
    return f"{document}{fragment}"


# -- AC1: the field list --------------------------------------------------------------


@dataclass(frozen=True)
class FieldEntry:
    record: str
    name: str
    start: int | None
    length: int | None
    end: int | None
    type: str
    citation_text: str
    citation_link: str

    def to_dict(self) -> dict:
        return {
            "record": self.record,
            "name": self.name,
            "start": self.start,
            "length": self.length,
            "end": self.end,
            "type": self.type,
            "citation_text": self.citation_text,
            "citation_link": self.citation_link,
        }


def field_list(spec: SourceSpec) -> tuple[FieldEntry, ...]:
    fallback = f"{spec.id} {spec.version} layout document"
    return tuple(
        FieldEntry(
            record=record.label,
            name=field.name,
            start=field.start,
            length=field.length,
            end=field.end,
            type=field.type,
            citation_text=field.citation.text(),
            citation_link=citation_link(field.citation, fallback_document=fallback),
        )
        for record, field in spec.fields()
    )


# -- AC2: version compare -----------------------------------------------------------


@dataclass(frozen=True)
class SpecFieldDiff:
    record: str
    field: str
    kind: str  # added, removed, shifted, changed
    before: dict | None
    after: dict | None
    description: str

    def to_dict(self) -> dict:
        return {"record": self.record, "field": self.field, "kind": self.kind, "before": self.before, "after": self.after, "description": self.description}


def _field_dict(field: Field) -> dict:
    return {"start": field.start, "length": field.length, "type": field.type, "required": field.required, "codes": list(field.codes)}


def compare(old: SourceSpec, new: SourceSpec) -> tuple[SpecFieldDiff, ...]:
    old_fields = {(record.label, field.name): field for record, field in old.fields()}
    new_fields = {(record.label, field.name): field for record, field in new.fields()}

    diffs: list[SpecFieldDiff] = []
    for record, name in sorted(set(old_fields) | set(new_fields)):
        o, n = old_fields.get((record, name)), new_fields.get((record, name))
        if o is None:
            diffs.append(SpecFieldDiff(record, name, "added", None, _field_dict(n), f"{record}.{name} added at {n.start}-{n.end} ({n.type})"))
        elif n is None:
            diffs.append(SpecFieldDiff(record, name, "removed", _field_dict(o), None, f"{record}.{name} removed (was {o.start}-{o.end}, {o.type})"))
        elif o.position != n.position:
            diffs.append(SpecFieldDiff(record, name, "shifted", _field_dict(o), _field_dict(n), f"{record}.{name} moved {o.start}-{o.end} -> {n.start}-{n.end}"))
        elif o.type != n.type or o.codes != n.codes or o.required != n.required:
            reason = f"type {o.type} -> {n.type}" if o.type != n.type else ("codes changed" if o.codes != n.codes else "required flag changed")
            diffs.append(SpecFieldDiff(record, name, "changed", _field_dict(o), _field_dict(n), f"{record}.{name}: {reason}"))
    return tuple(diffs)


# -- the view --------------------------------------------------------------------------


def render_field_list(spec: SourceSpec) -> str:
    out = [f"# {spec.label}", ""]
    out.append(f"{spec.file_type} · {spec.format} · effective {spec.effective_from.isoformat()}")
    out.append("")
    out.append("| Record | Field | Start | Length | End | Type | Citation | Open |")
    out.append("|---|---|---|---|---|---|---|---|")
    for f in field_list(spec):
        out.append(f"| {f.record} | {f.name} | {f.start if f.start is not None else '-'} | {f.length if f.length is not None else '-'} | {f.end if f.end is not None else '-'} | {f.type} | {f.citation_text} | `{f.citation_link}` |")
    out.append("")
    return "\n".join(out)


def render_compare(old: SourceSpec, new: SourceSpec, diffs: tuple[SpecFieldDiff, ...]) -> str:
    out = [f"# Compare: {old.label} -> {new.label}", ""]
    if not diffs:
        out += ["No differences.", ""]
        return "\n".join(out)
    counts: dict[str, int] = {}
    for d in diffs:
        counts[d.kind] = counts.get(d.kind, 0) + 1
    out.append(", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())))
    out.append("")
    out.append("| Record | Field | Kind | Description |")
    out.append("|---|---|---|---|")
    for d in diffs:
        out.append(f"| {d.record} | {d.field} | {d.kind} | {d.description} |")
    out.append("")
    return "\n".join(out)
