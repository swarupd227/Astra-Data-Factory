"""Pattern Matcher: a custodian's Source Spec assigned a family, a tier and a pattern list, with
a new shape routed to the architect queue instead of guessed into an existing family (S5.3.1,
ADR 0043, product spec Section 6).

`astra_knowledge.registry.Registry.unclassified()` already exists for exactly this agent — "specs
with no family yet: the Pattern Matcher has not classified them." Given the registry, this agent
compares an unclassified spec's detail records against every already-classified spec's, using only
each field's picture kind and declared type (never its name, since two custodians name the same kind
of field differently) to score how alike two layouts are. A confident match reuses that family; a
spec too unlike anything already known gets no family at all, marked as a new-pattern proposal — the
guardrail the backlog asks for, and the same "agent proposes, human approves" shape Spec Reader's own
`unparsed` list and draft-not-registry write already established. There is no separate ticketing
system in this repository to route a proposal into; the "architect queue" this story asks for is the
same report a person reads before promoting anything else here, with the proposal called out in its
own section.

The pattern list itself needs nothing new: `astra_knowledge.patterns.patterns_for(spec)` already
classifies a spec by its file format against the pattern library. Tier (`simple`/`medium`/`complex`,
the vocabulary a source config's own `tier` field already uses) has no existing formula to reuse — a
small, explicit point score is defined here instead, and documented, so the reasoning behind a
tier is visible rather than a black box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from astra_knowledge.patterns import patterns_for
from astra_knowledge.registry import Record, Registry, SourceSpec

# A spec's own declared family always wins; this is only how well an *unclassified* spec's detail
# records match an already-classified one. Calibrated against the two real data points this repo
# has: the same family's two real spec versions (pershing_gcus 2017-07-25 vs 2026-01-01, one field
# added) score ~0.94; the nearest *different* family (split_position_example, two paired detail
# records instead of one) scores ~0.23 after the detail-count penalty below. 0.7 sits with a wide
# margin on both sides of that gap.
FAMILY_THRESHOLD = 0.7

# Two specs whose detail records don't even come in the same number are not the same family, however
# similar their fields look field-by-field (one flat detail record vs. an A/B pair is a different
# shape); this penalty pulls that similarity below FAMILY_THRESHOLD without a hard, brittle cutoff.
DETAIL_COUNT_MISMATCH_PENALTY = 0.3

# Tier scoring: one point each for a handful of structural signals that a person reading the layout
# would themselves notice as the file's complexity. No pilot data yet ties an exact threshold to
# in-force onboarding time (the KPI in product spec Section 14 is by tier, not per-signal), so these
# are round numbers, not measured ones: a real ~90-field, two-detail-record layout the size of the
# full Pershing GCUS document scores 4 by this rule (complex); this repository's own illustrative
# pershing_gcus spec, one flat detail record with a merge rule, scores 1 (medium) — which matches the
# one tier already on file for it, in `configs/examples/pershing_position.yaml`.
FIELD_COUNT_COMPLEX = 30
RECORD_LENGTH_COMPLEX = 300
COLUMN_COUNT_COMPLEX = 20
TIER_COMPLEX_SCORE = 3
TIER_MEDIUM_SCORE = 1


class PatternMatcherError(RuntimeError):
    pass


def load_registry(root: Path) -> Registry:
    registry, problems = Registry.load(Path(root))
    if problems:
        raise PatternMatcherError("; ".join(p.format() for p in problems))
    return registry


# -- family similarity ----------------------------------------------------------


def _field_signature(field) -> tuple[str | None, str]:
    return (field.picture.kind if field.picture else None, field.type)


def _detail_signature(record: Record) -> tuple[tuple[str | None, str], ...]:
    return tuple(_field_signature(f) for f in record.fields if f.name != "filler")


def _lcs_len(a: tuple, b: tuple) -> int:
    """Length of the longest common subsequence: how much of one field sequence survives, in
    order, inside the other, tolerant of a field inserted or dropped between two custodians (or
    two versions of the same layout) without every later field being counted as a mismatch."""
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            current[j] = previous[j - 1] + 1 if x == y else max(previous[j], current[j - 1])
        previous = current
    return previous[-1]


def _record_similarity(a: tuple, b: tuple) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return 2 * _lcs_len(a, b) / (len(a) + len(b))


def _best_match_average(details_a: list[tuple], details_b: list[tuple]) -> float:
    """Greedily pairs each of A's detail records with the most similar unmatched one of B's;
    a detail record on either side with nothing left to pair against scores 0, so a file with an
    extra detail record type is not scored as if that record type did not need to match anything."""
    remaining = list(details_b)
    scores = []
    for a in details_a:
        if not remaining:
            scores.append(0.0)
            continue
        best_index = max(range(len(remaining)), key=lambda i: _record_similarity(a, remaining[i]))
        scores.append(_record_similarity(a, remaining[best_index]))
        remaining.pop(best_index)
    scores.extend([0.0] * len(remaining))
    return sum(scores) / len(scores) if scores else 0.0


def similarity(a: SourceSpec, b: SourceSpec) -> float:
    """How alike two specs' detail records are, 0-1. Header and trailer records are ignored: they
    are almost always a generic record-type-and-control-total shell, common to unrelated layouts,
    and would swamp the one part of a file that actually says what it is."""
    if a.format != b.format or a.file_type != b.file_type:
        return 0.0
    details_a = [_detail_signature(r) for r in a.records if r.type == "detail"]
    details_b = [_detail_signature(r) for r in b.records if r.type == "detail"]
    score = _best_match_average(details_a, details_b)
    if len(details_a) != len(details_b):
        score *= DETAIL_COUNT_MISMATCH_PENALTY
    return score


# -- tier -------------------------------------------------------------------


def tier_score(spec: SourceSpec) -> int:
    score = 0
    if sum(1 for r in spec.records if r.type == "detail") > 1:
        score += 1
    if spec.merge is not None:
        score += 1
    if spec.pairings:
        score += 1
    if spec.splits:
        score += 1
    if spec.lifecycle is not None:
        score += 1
    field_count = sum(1 for r in spec.records for f in r.fields if f.name != "filler")
    if field_count > FIELD_COUNT_COMPLEX:
        score += 1
    size = spec.record_length if spec.format == "fixed_width" else spec.file.get("column_count")
    size_threshold = RECORD_LENGTH_COMPLEX if spec.format == "fixed_width" else COLUMN_COUNT_COMPLEX
    if size is not None and size > size_threshold:
        score += 1
    return score


def assign_tier(spec: SourceSpec) -> str:
    score = tier_score(spec)
    if score >= TIER_COMPLEX_SCORE:
        return "complex"
    if score >= TIER_MEDIUM_SCORE:
        return "medium"
    return "simple"


# -- assignment ---------------------------------------------------------------


@dataclass(frozen=True)
class FamilyMatch:
    family: str
    similarity: float
    matched_spec_id: str
    matched_spec_version: str


@dataclass(frozen=True)
class Assignment:
    spec_id: str
    spec_version: str
    file_type: str
    tier: str
    tier_score: int
    patterns: tuple[str, ...]
    family: str | None
    match: FamilyMatch | None
    reuse_candidates: tuple[tuple[str, str], ...]
    new_pattern_proposal: bool

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "file_type": self.file_type,
            "tier": self.tier,
            "tier_score": self.tier_score,
            "patterns": list(self.patterns),
            "family": self.family,
            "match": (
                {
                    "family": self.match.family,
                    "similarity": round(self.match.similarity, 4),
                    "matched_spec_id": self.match.matched_spec_id,
                    "matched_spec_version": self.match.matched_spec_version,
                }
                if self.match
                else None
            ),
            "reuse_candidates": [{"id": i, "version": v} for i, v in self.reuse_candidates],
            "new_pattern_proposal": self.new_pattern_proposal,
        }


def best_match(spec: SourceSpec, known: Iterable[SourceSpec]) -> FamilyMatch | None:
    """The most similar already-classified spec, whether or not it is similar enough to reuse."""
    candidates = [(s, similarity(spec, s)) for s in known if s.family is not None]
    if not candidates:
        return None
    matched, score = max(candidates, key=lambda pair: pair[1])
    return FamilyMatch(family=matched.family, similarity=score, matched_spec_id=matched.id, matched_spec_version=matched.version)


def classify(spec: SourceSpec, registry: Registry, *, family_threshold: float = FAMILY_THRESHOLD) -> Assignment:
    known = [s for s in registry.specs if not (s.id == spec.id and s.version == spec.version)]
    match = best_match(spec, known)

    if spec.family is not None:
        family, new_pattern_proposal = spec.family, False
    elif match is not None and match.similarity >= family_threshold:
        family, new_pattern_proposal = match.family, False
    else:
        family, new_pattern_proposal = None, True

    reuse_candidates = tuple(sorted({(s.id, s.version) for s in known if family is not None and s.family == family}))
    return Assignment(
        spec_id=spec.id,
        spec_version=spec.version,
        file_type=spec.file_type,
        tier=assign_tier(spec),
        tier_score=tier_score(spec),
        patterns=tuple(p.id for p in patterns_for(spec)),
        family=family,
        match=match,
        reuse_candidates=reuse_candidates,
        new_pattern_proposal=new_pattern_proposal,
    )


def run(registry: Registry, *, spec_id: str | None = None, spec_version: str | None = None, family_threshold: float = FAMILY_THRESHOLD) -> tuple[Assignment, ...]:
    """Classify one named spec, or every spec the registry has not classified yet."""
    if spec_id is not None:
        spec = registry.get(spec_id, spec_version) if spec_version else max(registry.versions(spec_id), key=lambda s: s.effective_from, default=None)
        if spec is None:
            raise PatternMatcherError(f"no spec '{spec_id}'" + (f" version '{spec_version}'" if spec_version else "") + " in this registry")
        targets = [spec]
    else:
        targets = registry.unclassified()
    return tuple(classify(s, registry, family_threshold=family_threshold) for s in targets)


# -- the report ----------------------------------------------------------------


def render_markdown(assignments: tuple[Assignment, ...]) -> str:
    out = ["# Pattern Matcher assignments", ""]
    proposals = [a for a in assignments if a.new_pattern_proposal]
    out.append(f"{len(assignments)} spec(s) classified. {len(proposals)} new pattern proposal(s) for the architect queue.")
    out.append("")
    out.append("| Spec | File type | Family | Tier | Pattern(s) |")
    out.append("|---|---|---|---|---|")
    for a in assignments:
        family = a.family or "*(none — see proposal below)*"
        out.append(f"| {a.spec_id} {a.spec_version} | {a.file_type} | {family} | {a.tier} | {', '.join(a.patterns) or '-'} |")
    out.append("")
    for a in assignments:
        if a.reuse_candidates:
            out.append(f"`{a.spec_id} {a.spec_version}` reuse candidates ({a.family}): " + ", ".join(f"{i} {v}" for i, v in a.reuse_candidates))
    out.append("")
    if proposals:
        out.append("## Architect queue: new pattern proposals")
        out.append("")
        out.append("No existing family matched closely enough to reuse; a person decides whether this is really a new pattern or the threshold missed a real match.")
        out.append("")
        for a in proposals:
            nearest = f"{a.match.family} (similarity {a.match.similarity:.2f}, nearest: {a.match.matched_spec_id} {a.match.matched_spec_version})" if a.match else "no other classified spec to compare against"
            out.append(f"- `{a.spec_id} {a.spec_version}` ({a.file_type}, tier {a.tier}): nearest known family is {nearest}")
        out.append("")
    return "\n".join(out)


def write_assignments(assignments: tuple[Assignment, ...], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(assignments), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps([a.to_dict() for a in assignments], indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
