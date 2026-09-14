"""Diff review: a side-by-side diff of a config change with citations and impact, so approvals
are informed (S6.1.3, ADR 0056, product spec Section 3: steward's own top task).

Nothing here re-implements a diff. `astra_verification.replay.config_diff(old, new) ->
ConfigDiff` (S4.1.3) already compares two compiled configs field by field — mappings, dq_rules,
rules and resolution, grouped and described; this module's only job is the two things that
function does not itself compute: which OTHER custodians are affected by a rule this diff
touches, and a citation someone could actually open.

"Affected custodians" only makes sense for a rule a diff touches — a mapping's own target,
source or transform only ever affects the one custodian whose file it is; changing which rule
governs it, or a rule's own text or status changing, can affect every custodian who references
that same rule. Every rule id this diff adds, removes or changes is looked up in
`astra_knowledge.rules.lineage`'s own `configs_by_rule` — the reverse index this repository
already builds (the rule catalog's own tooling), not a second one built here. Reading which rule
ids a diff actually touches has one real subtlety: `config_diff` records a rule's own reference,
status or text change under the dedicated group `"rule:<id>"`, but a mapping whose *own*
attribution changed (a field gained or lost a `rule:` reference, everything else the same) is
recorded under the rule id itself, grouped with the mapping. The dedicated `"rule:<id>"` entry is
the authoritative signal for whether the reference itself is new, gone or altered — checked
first — and the mapping-level entry only fills in a rule id no dedicated entry already covers.

A citation is already a structured `astra_knowledge.rules.Citation` — `kind` `"spec"`, `"code"`
or `"document"`, with real page/line or file/line fields, never a bare string to parse.
`citation_link` does the one thing `Citation.text` does not: a real, openable reference —
`specs/<id>/<version>.yaml#page=N` for a spec citation (the registry's own file layout — ADR
0001's own spec storage convention), `<file>:<line>` for a code citation (already what an editor
or `git blame` accepts) — matching "opens the spec page or code line" (AC2) exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from astra_core.problems import display_path
from astra_core.yamlsource import SourceError, load
from astra_data.compiler import CompileError, CompiledConfig, compile_config
from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog, Citation, lineage
from astra_verification.replay import ConfigDiff, FieldDiff, config_diff

RULE_GROUP_PREFIX = "rule:"


class DiffReviewError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def _load_context(specs_dir: Path, rules_dir: Path, domains_dir: Path, root: Path | None):
    registry, problems = Registry.load(Path(specs_dir), repo_root=root)
    if problems:
        raise DiffReviewError("; ".join(p.format() for p in problems))
    catalog, problems = Catalog.load(Path(rules_dir), root, registry)
    if problems:
        raise DiffReviewError("; ".join(p.format() for p in problems))
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        raise DiffReviewError("; ".join(p.format() for p in problems))
    return registry, catalog, packs


def _compile(path: Path, registry, catalog, packs, root: Path | None) -> CompiledConfig:
    path = Path(path)
    if not path.is_file():
        raise DiffReviewError(f"config file not found: {path}")
    try:
        return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=root)
    except CompileError as exc:
        raise DiffReviewError("; ".join(p.format() for p in exc.problems)) from exc


def _custodian_of(path: Path) -> str | None:
    """The real Path's own `source.custodian` — never the display-path string `lineage` returns,
    which is relative to `root` for showing to a person, not necessarily to the current process's
    own working directory."""
    try:
        data = load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SourceError):
        return None
    if not isinstance(data, dict):
        return None
    return (data.get("source") or {}).get("custodian")


# -- citations -----------------------------------------------------------------


def citation_link(citation: Citation) -> str:
    """A real, openable reference — the one thing Citation.text does not give: a spec file and
    page, or a code file and line, someone could actually open (AC2)."""
    if citation.kind == "spec":
        base = f"specs/{citation.spec_id}/{citation.spec_version}.yaml"
        return f"{base}#page={citation.page}" if citation.page else base
    if citation.kind == "code":
        where = f"{citation.file}:{citation.line}" if citation.line else (citation.file or "")
        return f"{citation.repository}/{where}" if citation.repository else where
    base = citation.document or ""
    return f"{base}#page={citation.page}" if citation.page else base


# -- rule impact: which rules this diff touches, and who else that affects ----------------------


def _rule_ids_touched(field_diffs: tuple[FieldDiff, ...], old: CompiledConfig, new: CompiledConfig) -> dict[str, str]:
    """rule id -> change kind (added, removed, changed). The dedicated `"rule:<id>"` group is
    authoritative and checked first; a mapping-attributed entry only fills in a rule id no
    dedicated entry already covers (module docstring)."""
    known = {r.id for r in old.rules} | {r.id for r in new.rules}
    touched: dict[str, str] = {}
    for fd in field_diffs:
        if fd.group.startswith(RULE_GROUP_PREFIX):
            touched[fd.group[len(RULE_GROUP_PREFIX):]] = fd.kind
    for fd in field_diffs:
        if fd.group in known and fd.group not in touched:
            touched[fd.group] = fd.kind
    return touched


@dataclass(frozen=True)
class RuleImpact:
    rule_id: str
    change_kind: str  # added, removed, changed
    citation: Citation | None
    citation_link: str | None
    affected_custodians: tuple[str, ...]  # every custodian whose config references this rule, including this diff's own

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "change_kind": self.change_kind,
            "citation_text": self.citation.text if self.citation else None,
            "citation_link": self.citation_link,
            "affected_custodians": list(self.affected_custodians),
        }


# -- the review ------------------------------------------------------------------


@dataclass(frozen=True)
class DiffReview:
    old_config: str
    new_config: str
    field_diffs: tuple[FieldDiff, ...]
    other_diffs: tuple[str, ...]
    rule_impacts: tuple[RuleImpact, ...]

    @property
    def changed(self) -> bool:
        return bool(self.field_diffs or self.other_diffs)

    def to_dict(self) -> dict:
        return {
            "old_config": self.old_config,
            "new_config": self.new_config,
            "changed": self.changed,
            "fields": [{"group": f.group, "column": f.column, "kind": f.kind, "description": f.description} for f in self.field_diffs],
            "other": list(self.other_diffs),
            "rule_impacts": [r.to_dict() for r in self.rule_impacts],
        }


def review(
    old_config: Path,
    new_config: Path,
    *,
    other_configs: Iterable[Path] = (),
    specs_dir: Path = Path("specs"),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
    root: Path | None = None,
) -> DiffReview:
    registry, catalog, packs = _load_context(specs_dir, rules_dir, domains_dir, root)
    old = _compile(old_config, registry, catalog, packs, root)
    new = _compile(new_config, registry, catalog, packs, root)
    diff: ConfigDiff = config_diff(old, new)

    all_configs = (Path(new_config),) + tuple(Path(p) for p in other_configs)
    line = lineage(catalog, all_configs, root)
    custodian_by_display = {display_path(p, root): _custodian_of(p) for p in all_configs}

    touched = _rule_ids_touched(tuple(diff.fields), old, new)
    rules_by_id = {r.id: r for r in old.rules} | {r.id: r for r in new.rules}
    impacts = []
    for rule_id, kind in sorted(touched.items()):
        rule = rules_by_id.get(rule_id)
        citation = rule.citation if rule else None
        configs = line.configs_for(rule_id)
        custodians = tuple(sorted({c for disp in configs if (c := custodian_by_display.get(disp)) is not None}))
        impacts.append(RuleImpact(rule_id=rule_id, change_kind=kind, citation=citation, citation_link=citation_link(citation) if citation else None, affected_custodians=custodians))

    return DiffReview(
        old_config=display_path(Path(old_config), root),
        new_config=display_path(Path(new_config), root),
        field_diffs=tuple(diff.fields),
        other_diffs=tuple(diff.other),
        rule_impacts=tuple(impacts),
    )


# -- the view --------------------------------------------------------------------------


def render_markdown(review_result: DiffReview) -> str:
    out = [f"# Diff review: {review_result.old_config} -> {review_result.new_config}", ""]
    if not review_result.changed:
        out += ["No differences.", ""]
        return "\n".join(out)

    out.append("## Changed fields")
    out.append("")
    out.append("| Group | Column | Kind | Description |")
    out.append("|---|---|---|---|")
    for fd in review_result.field_diffs:
        out.append(f"| {fd.group} | {fd.column or '-'} | {fd.kind} | {fd.description} |")
    out.append("")

    if review_result.other_diffs:
        out.append("## Other differences")
        out.append("")
        for line_text in review_result.other_diffs:
            out.append(f"- {line_text}")
        out.append("")

    if review_result.rule_impacts:
        out.append("## Rules touched — citation and impact")
        out.append("")
        out.append("| Rule | Change | Citation | Open | Affected custodians |")
        out.append("|---|---|---|---|---|")
        for imp in review_result.rule_impacts:
            citation_text = imp.citation.text if imp.citation else "-"
            link = imp.citation_link or "-"
            custodians = ", ".join(imp.affected_custodians) if imp.affected_custodians else "none found"
            out.append(f"| {imp.rule_id} | {imp.change_kind} | {citation_text} | `{link}` | {custodians} |")
        out.append("")

    return "\n".join(out)


def write_review(review_result: DiffReview, out: Path) -> tuple[Path, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(review_result), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(review_result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
