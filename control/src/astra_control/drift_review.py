"""Drift change review: a detected layout change shown beside its proposed spec delta, an
optional config delta, and the impact list, approved into a non-prod change-request log — never
a git commit, never a write to `specs/` or `configs/` themselves (S6.3.9, ADR 0065, product spec
Section 7.5: "The diff and impact list (which sources, which consumers) go to the steward...
Approval promotes the change").

Nothing here re-detects drift. `astra_agents.drift_watcher`'s own `report.json` already has every
finding this module needs (`kind`, `path`, `current`, `proposed`, `description`) — the same "no
new agent import, read the report file" boundary `astra_control.queue` already established for
this exact report (`drift_from`). Nothing here re-implements a spec diff either:
`apply_drift_findings` builds a candidate `SourceSpec` purely in memory — a `record_length`
finding updates `file["record_length"]`, a `new_code` finding adds a code to the named field —
and `astra_control.spec_viewer.compare`, already proven against real committed spec drift (ADR
0060), diffs it against the real registered version, unchanged. A `record_length` change never
shows in `compare()`'s own output (it only diffs fields, by design — ADR 0060), so this module's
own spec diff always shows the raw findings themselves alongside `compare()`'s field-level view,
rather than folding a file-level attribute into a comparator scoped to fields.

A config diff, when the caller already has a drafted candidate config file, is `astra_control.
diff_review.review` itself, called directly — the same function, not a second comparison.
**"Diff of... config" has no drift-produced input to diff against by default.** The real
`astra_agents.drift_watcher` implementation only ever proposes spec-level deltas — never a
config-level one, despite the product spec's own aspirational "Drift Watcher... proposed config
delta" (Section 6, the agent catalog). A config diff here is only ever shown when the caller
already has two real config files — typically an engineer's own hand-drafted candidate config
reflecting the spec change, sitting in `work/` per this plane's own draft-not-registry convention
— optional, never synthesized by this module.

**"Impact list of affected custodians" is the changed spec's own `custodians` field** — every
custodian delivering under this spec is affected by a change to it, directly; no cross-config
lineage is needed the way a rule's own is (`astra_control.diff_review`'s reuse of `astra_knowledge.
rules.lineage`). **"...and consumers" has no source anywhere in this repository** — nothing in
`astra_knowledge` or `astra_data` tracks a downstream Gold/read-model consumer (Tamarac or any
other application reading Gold tables). This module accepts an optional, caller-supplied
`consumers` list, honestly empty when none is given, rather than inventing a downstream-consumer
registry this story does not build.

**"Approve creates a Git change; never touches prod" is one append-only change-request log entry,
never a git operation.** No module anywhere in this codebase invokes git to commit, branch or push
(checked exhaustively) — the one git call in this repository, `astra_verification.replay.
old_config_from_git`, only reads a historical ref (`git show`), never mutates. Every prior "this
creates a real change" module in this plane writes a file and stops there: `astra_knowledge.rules.
set_status` rewrites a rule file, `astra_control.config_studio.request_promotion` appends to
`promotion-requests.yaml`. `approve()` follows the exact same shape, appending to a
`drift-change-requests.yaml`-style log — a human or CI turns the approved record into a real
committed change (`README.md`'s own "Working in this repository": "Git is the system of record...
A merge to main deploys to dev; qa follows once a reviewer approves"), never this function.
"Never touches prod" is true structurally: nothing this module does writes to `specs/`, `configs/`,
or deploys anything — the approval record is the only artifact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from astra_core.yamlsource import load
from astra_knowledge.registry import SourceSpec

from astra_control.diff_review import DiffReview, DiffReviewError
from astra_control.diff_review import review as review_config
from astra_control.spec_viewer import SpecFieldDiff, SpecViewerError, compare, load_registry, load_spec

NEW_CODE_PATH = re.compile(r"^records\[(?P<record>.+)\]\.fields\[(?P<field>.+)\]\.codes$")


class DriftReviewError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise DriftReviewError(f"drift report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriftReviewError(f"{path}: {exc}") from exc


def _now(at: datetime | None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- applying a drift finding to a candidate spec, purely in memory ------------------------------


def apply_drift_findings(spec: SourceSpec, findings: tuple[dict, ...]) -> SourceSpec:
    """A candidate SourceSpec, never written to disk: each finding's own `kind` says what to
    change (module docstring). Refuses a finding this module does not recognize, or whose `path`
    does not match its own kind's expected shape, rather than silently ignoring it."""
    new = spec
    for finding in findings:
        kind = finding.get("kind")
        if kind == "record_length":
            if finding.get("path") != "file.record_length":
                raise DriftReviewError(f"a record_length finding is expected at path 'file.record_length', found {finding.get('path')!r}")
            new_file = dict(new.file)
            new_file["record_length"] = finding["proposed"]
            new = replace(new, file=new_file)
        elif kind == "new_code":
            match = NEW_CODE_PATH.match(finding.get("path") or "")
            if not match:
                raise DriftReviewError(f"a new_code finding's path must match 'records[<record>].fields[<field>].codes', found {finding.get('path')!r}")
            record = new.record(match["record"])
            if record is None:
                raise DriftReviewError(f"no record '{match['record']}' in {new.label}")
            target = record.field(match["field"])
            if target is None:
                raise DriftReviewError(f"no field '{match['field']}' in record '{match['record']}' of {new.label}")
            code = str(finding["proposed"])
            new_field = replace(target, codes=target.codes + ((code, "(drift review: new code observed, not yet described)"),))
            new_fields = tuple(new_field if f.name == target.name else f for f in record.fields)
            new_record = replace(record, fields=new_fields)
            new_records = tuple(new_record if r.label == record.label else r for r in new.records)
            new = replace(new, records=new_records)
        else:
            raise DriftReviewError(f"'{kind}' is not a drift finding kind this module recognizes (record_length, new_code)")
    return new


# -- the review: spec diff, optional config diff, impact list ------------------------------------


@dataclass(frozen=True)
class DriftReview:
    spec_id: str
    spec_version: str
    sample: str
    findings: tuple[dict, ...]
    spec_diff: tuple[SpecFieldDiff, ...]
    config_diff: DiffReview | None
    affected_custodians: tuple[str, ...]
    consumers: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "sample": self.sample,
            "findings": list(self.findings),
            "spec_diff": [d.to_dict() for d in self.spec_diff],
            "config_diff": self.config_diff.to_dict() if self.config_diff else None,
            "affected_custodians": list(self.affected_custodians),
            "consumers": list(self.consumers),
        }


def review(
    drift_report: Path,
    *,
    specs_dir: Path = Path("specs"),
    old_config: Path | None = None,
    new_config: Path | None = None,
    other_configs: Iterable[Path] = (),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
    consumers: tuple[str, ...] = (),
    root: Path | None = None,
) -> DriftReview:
    data = _read_json(drift_report)
    findings = tuple(data.get("findings") or ())

    try:
        registry = load_registry(specs_dir, root)
        old_spec = load_spec(registry, data["spec_id"], data["spec_version"])
    except SpecViewerError as exc:
        raise DriftReviewError(str(exc)) from exc
    new_spec = apply_drift_findings(old_spec, findings)
    spec_diff = compare(old_spec, new_spec)

    config_diff_result = None
    if old_config is not None and new_config is not None:
        try:
            config_diff_result = review_config(Path(old_config), Path(new_config), other_configs=other_configs, specs_dir=specs_dir, rules_dir=rules_dir, domains_dir=domains_dir, root=root)
        except DiffReviewError as exc:
            raise DriftReviewError(str(exc)) from exc

    return DriftReview(
        spec_id=old_spec.id,
        spec_version=old_spec.version,
        sample=data.get("sample", ""),
        findings=findings,
        spec_diff=spec_diff,
        config_diff=config_diff_result,
        affected_custodians=old_spec.custodians,
        consumers=tuple(consumers),
    )


# -- AC3: approve into a non-prod change-request log, never git, never prod ---------------------


@dataclass(frozen=True)
class ChangeRequest:
    spec_id: str
    spec_version: str
    approved_by: str
    note: str | None
    at: str

    def to_dict(self) -> dict:
        return {"spec_id": self.spec_id, "spec_version": self.spec_version, "approved_by": self.approved_by, "note": self.note, "at": self.at}


def load_change_requests(path: Path | None) -> tuple[ChangeRequest, ...]:
    if path is None:
        return ()
    path = Path(path)
    if not path.is_file():
        return ()
    data = load(path.read_text(encoding="utf-8")) or {}
    return tuple(
        ChangeRequest(spec_id=r["spec_id"], spec_version=r["spec_version"], approved_by=r["approved_by"], note=r.get("note"), at=r["at"])
        for r in data.get("requests") or ()
    )


def _yaml_str(value: str) -> str:
    return json.dumps(value)


def _yaml_field(value: str | None) -> str:
    return _yaml_str(value) if value is not None else "null"


def approve(review_result: DriftReview, path: Path, *, approved_by: str, note: str | None = None, at: datetime | None = None) -> tuple[ChangeRequest, ...]:
    """Records approval to non-prod in an append-only log — module docstring: never a git
    operation, never a write to specs/ or configs/. Refuses outright, nothing written, when
    `approved_by` is blank."""
    approved_by = approved_by.strip()
    if not approved_by:
        raise DriftReviewError("approved_by must be given")
    note = note.strip() if note and note.strip() else None

    existing = load_change_requests(path if Path(path).is_file() else None)
    request = ChangeRequest(spec_id=review_result.spec_id, spec_version=review_result.spec_version, approved_by=approved_by, note=note, at=_now(at))
    updated = existing + (request,)

    lines = ["# Drift change review: one non-prod approval per entry, oldest first. A human or CI turns this into a real git change; nothing here touches specs/, configs/ or prod.", "requests_version: 0", "", "requests:"]
    for r in updated:
        lines.append("  - { spec_id: " + r.spec_id + ", spec_version: " + _yaml_str(r.spec_version) + ", approved_by: " + _yaml_str(r.approved_by) + ", note: " + _yaml_field(r.note) + ", at: " + _yaml_str(r.at) + " }")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return updated


# -- the view --------------------------------------------------------------------------


def render_markdown(review_result: DriftReview) -> str:
    out = [f"# Drift review: {review_result.spec_id} {review_result.spec_version}", ""]
    out.append(f"Sample: `{review_result.sample}`")
    out.append("")

    out.append("## Findings")
    out.append("")
    out.append("| Kind | Path | Current | Proposed | Description |")
    out.append("|---|---|---|---|---|")
    for f in review_result.findings:
        out.append(f"| {f.get('kind')} | `{f.get('path')}` | {f.get('current')} | {f.get('proposed')} | {f.get('description', '')} |")
    out.append("")

    out.append("## Spec field diff")
    out.append("")
    if review_result.spec_diff:
        out.append("| Record | Field | Kind | Description |")
        out.append("|---|---|---|---|")
        for d in review_result.spec_diff:
            out.append(f"| {d.record} | {d.field} | {d.kind} | {d.description} |")
    else:
        out.append("No field-level differences (a record_length change, if any, is shown above under Findings only — `compare` diffs fields, not file-level attributes).")
    out.append("")

    out.append("## Config diff")
    out.append("")
    out.append("Given." if review_result.config_diff else "No config diff given — this drift has no drafted candidate config yet.")
    out.append("")

    out.append("## Impact")
    out.append("")
    out.append(f"Affected custodians: {', '.join(review_result.affected_custodians) or 'none'}")
    out.append(f"Consumers: {', '.join(review_result.consumers) if review_result.consumers else 'not tracked'}")
    out.append("")
    return "\n".join(out)


def render_change_requests_markdown(requests: tuple[ChangeRequest, ...]) -> str:
    out = ["# Drift change requests (non-prod)", ""]
    if not requests:
        out += ["No approvals recorded yet.", ""]
        return "\n".join(out)
    out.append("| Spec | Version | Approved by | At | Note |")
    out.append("|---|---|---|---|---|")
    for r in requests:
        out.append(f"| {r.spec_id} | {r.spec_version} | {r.approved_by} | {r.at} | {r.note or '-'} |")
    out.append("")
    return "\n".join(out)
