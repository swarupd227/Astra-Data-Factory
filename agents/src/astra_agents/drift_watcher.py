"""Drift Watcher: a new file's layout compared against its spec, with a proposed delta, never
applied, before the file reaches Silver (S5.9.1, ADR 0049, product spec Section 6).

Two structural signals, each read straight off what `astra_knowledge.registry.SourceSpec` and
`astra_knowledge.patterns.fixed_width` already give: a record-length drift is the sample's own
raw line length disagreeing with `file.record_length` on a majority of lines (not one truncated
or padded outlier); a code-set drift is a code-typed field's raw value, at exactly the position
the spec already declares, repeating often enough to be a real pattern rather than one corrupted
row, while never appearing in that field's own declared `codes`. Both are detected the same way
Profiler already detects a field-level disagreement (ADR 0042) — reusing the parser's own
`record_type_of`/`matches` so a field is read from the position its own record type declares even
when the file has grown a few characters longer — except Drift Watcher's job is to propose the
specific delta a spec needs, not just to flag that something disagreed.

The guardrail this story asks for — never modifies production config — is structural: this
module has no function that writes to a spec file at all; `generate` and `run` only ever return
a `DriftDraft`, and `write_draft` writes a report under a working directory, never near
`specs/`. A test proves the real spec file this agent's own example reads is byte-identical
before and after a run that finds drift.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra_knowledge.patterns.fixed_width import record_type_of
from astra_knowledge.registry import SourceSpec, load_spec_file

# A candidate new line length must account for a majority of the sample's non-blank lines before
# it is trusted as real drift rather than one truncated or padded outlier.
RECORD_LENGTH_MAJORITY = 0.5

# A candidate new code must repeat before it is trusted as a real pattern rather than one
# corrupted row; both this and the majority fraction above are round numbers, not measured ones
# (the same honesty ADR 0043's tier thresholds and DQ Generator's severity default already used).
CODE_MIN_OCCURRENCES = 2


class DriftWatcherError(RuntimeError):
    pass


def load_spec(path: Path) -> SourceSpec:
    path = Path(path)
    if not path.is_file():
        raise DriftWatcherError(f"spec file not found: {path}")
    spec, problems = load_spec_file(path)
    if problems:
        raise DriftWatcherError("; ".join(p.format() for p in problems))
    return spec


def read_sample(path: Path) -> tuple[str, ...]:
    path = Path(path)
    if not path.is_file():
        raise DriftWatcherError(f"sample file not found: {path}")
    return tuple(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


# -- the two detectors ------------------------------------------------------------


@dataclass(frozen=True)
class DriftFinding:
    kind: str  # record_length | new_code
    path: str  # where in the spec this would change
    description: str
    current: Any
    proposed: Any
    evidence: dict[str, Any]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "description": self.description, "current": self.current, "proposed": self.proposed, "evidence": self.evidence}


def detect_record_length_drift(spec: SourceSpec, lines: tuple[str, ...]) -> DriftFinding | None:
    if not lines or spec.record_length is None:
        return None
    lengths = Counter(len(line) for line in lines)
    observed, count = lengths.most_common(1)[0]
    if observed == spec.record_length or count < len(lines) * RECORD_LENGTH_MAJORITY:
        return None
    return DriftFinding(
        kind="record_length",
        path="file.record_length",
        description=f"{count}/{len(lines)} line(s) are {observed} characters; the spec declares record_length {spec.record_length}",
        current=spec.record_length,
        proposed=observed,
        evidence={"declared_length": spec.record_length, "observed_length": observed, "matching_lines": count, "total_lines": len(lines)},
    )


def detect_code_drift(spec: SourceSpec, lines: tuple[str, ...]) -> tuple[DriftFinding, ...]:
    findings: list[DriftFinding] = []
    for record in spec.records:
        record_lines = [line for line in lines if record_type_of(spec, line) is record]
        if not record_lines:
            continue
        for f in record.fields:
            if not f.codes:
                continue
            declared = {value for value, _ in f.codes}
            observed = Counter(line[f.start - 1 : f.start - 1 + f.length].strip() for line in record_lines)
            unknown = sorted((value, c) for value, c in observed.items() if value and value not in declared and c >= CODE_MIN_OCCURRENCES)
            for value, count in unknown:
                findings.append(
                    DriftFinding(
                        kind="new_code",
                        path=f"records[{record.label}].fields[{f.name}].codes",
                        description=f"{record.label}.{f.name} has {count} occurrence(s) of '{value}', which is not a declared code ({', '.join(sorted(declared))})",
                        current=sorted(declared),
                        proposed=value,
                        evidence={"record": record.label, "field": f.name, "value": value, "occurrences": count, "total_lines": len(record_lines), "declared_codes": sorted(declared)},
                    )
                )
    return tuple(findings)


# -- the draft ---------------------------------------------------------------------


@dataclass(frozen=True)
class DriftDraft:
    spec_id: str
    spec_version: str
    sample: str
    lines_checked: int
    findings: tuple[DriftFinding, ...]

    @property
    def drift_detected(self) -> bool:
        return bool(self.findings)

    @property
    def ok(self) -> bool:
        """True: nothing detected, safe to let the file continue to Silver."""
        return not self.drift_detected

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "sample": self.sample,
            "lines_checked": self.lines_checked,
            "drift_detected": self.drift_detected,
            "findings": [f.to_dict() for f in self.findings],
        }


def generate(spec: SourceSpec, lines: tuple[str, ...], *, sample_name: str = "") -> DriftDraft:
    findings: list[DriftFinding] = []
    length_finding = detect_record_length_drift(spec, lines)
    if length_finding:
        findings.append(length_finding)
    findings.extend(detect_code_drift(spec, lines))
    return DriftDraft(spec_id=spec.id, spec_version=spec.version, sample=sample_name, lines_checked=len(lines), findings=tuple(findings))


def run(spec_path: Path, sample_path: Path) -> DriftDraft:
    spec = load_spec(spec_path)
    lines = read_sample(sample_path)
    return generate(spec, lines, sample_name=Path(sample_path).name)


# -- the report ----------------------------------------------------------------


def render_markdown(draft: DriftDraft) -> str:
    out = [f"# Drift Watcher: {draft.spec_id} {draft.spec_version} vs {draft.sample or 'sample'}", ""]
    out.append(f"{draft.lines_checked} line(s) checked. Drift detected: {'yes' if draft.drift_detected else 'no'}. This spec was not modified.")
    out.append("")
    if not draft.findings:
        out.append("Nothing found; safe to proceed to Silver.")
        out.append("")
        return "\n".join(out)
    out.append("## Proposed delta")
    out.append("")
    out.append("| Path | Current | Proposed | Description |")
    out.append("|---|---|---|---|")
    for f in draft.findings:
        out.append(f"| {f.path} | {f.current} | {f.proposed} | {f.description} |")
    out.append("")
    out.append("Nothing here has been written to the spec; a person reviews this delta and edits the spec file themselves.")
    out.append("")
    return "\n".join(out)


def write_draft(draft: DriftDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
