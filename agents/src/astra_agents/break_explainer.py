"""Parity / Break Explainer: every dual-run difference explained by rule, field and cause
(S5.10.1, ADR 0050, product spec Section 6).

This agent does not find differences itself — `astra_verification.parity.compare_rows` (S4.2.1,
ADR 0033) already does, and already returns exactly the structure an explanation needs: which
row, which field, the legacy value, the lakehouse value. Nor does it invent a way to tell which
rule governs a field — `astra_data.compiler.compile_config` (S3.1.1) already resolves every
mapping's `rule` reference to a real `astra_knowledge.rules.Rule` object. Break Explainer's own
job is the one piece neither already does: classifying *why* a field differs, from the mapping's
own structure, and citing the rule when there is one.

Cause is read off the mapping the same way DQ Generator and Drift Watcher read their own signals
off a spec — never invented, never a model's guess:

- **transform**: the mapping applies a transform (`signed_implied_decimal`, `implied_decimal`, ...)
  — a rounding or scale difference between two independent implementations of the same transform
  is a real, structural possibility.
- **resolution**: the column is one `astra_data.compiler`'s own `ACCOUNT_COLUMNS`/
  `SECURITY_COLUMNS`/`TRANSACTION_CODE_COLUMNS` names as resolution-produced — a difference here
  can come from reference data timing between the two runs, not the mapping itself.
- **unmapped**: the field has no mapping in this config at all (a column computed downstream,
  `MARKET_VALUE` from `configs/examples/pershing_position.yaml` being the real example) — this
  agent does not trace a computation it was not given.
- **unexplained**: a direct, untransformed, non-resolution mapping still differs. Nothing in the
  config explains this, so it stays unexplained rather than a guess dressed up as an answer —
  the reason this story's own acceptance criterion is "≥[T]%", not 100%.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from astra_data.compiler import ACCOUNT_COLUMNS, SECURITY_COLUMNS, TRANSACTION_CODE_COLUMNS, CompileError, CompiledConfig, CompiledMapping, compile_config
from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog
from astra_verification.parity import FieldMismatch, ParityMapping, ParityResult, RowMismatch, compare_rows, load_parity

RESOLUTION_COLUMNS = ACCOUNT_COLUMNS + SECURITY_COLUMNS + TRANSACTION_CODE_COLUMNS
CAUSES = ("transform", "resolution", "unmapped", "unexplained")


class BreakExplainerError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def load_compiled_config(config_path: Path, *, specs_dir: Path, rules_dir: Path, domains_dir: Path) -> CompiledConfig:
    registry, problems = Registry.load(Path(specs_dir))
    if problems:
        raise BreakExplainerError("; ".join(p.format() for p in problems))
    catalog, problems = Catalog.load(Path(rules_dir))
    if problems:
        raise BreakExplainerError("; ".join(p.format() for p in problems))
    packs, problems = load_packs(Path(domains_dir))
    if problems:
        raise BreakExplainerError("; ".join(p.format() for p in problems))
    try:
        return compile_config(Path(config_path), registry=registry, catalog=catalog, packs=packs)
    except CompileError as exc:
        raise BreakExplainerError("; ".join(p.format() for p in exc.problems)) from exc


def load_parity_mapping(path: Path) -> ParityMapping:
    path = Path(path)
    if not path.is_file():
        raise BreakExplainerError(f"parity file not found: {path}")
    mapping, problems = load_parity(path)
    if problems:
        raise BreakExplainerError("; ".join(p.format() for p in problems))
    return mapping


def load_rows(path: Path) -> list[dict[str, str | None]]:
    path = Path(path)
    if not path.is_file():
        raise BreakExplainerError(f"rows file not found: {path}")
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
    return [{k: (v or None) for k, v in row.items()} for row in reader]


# -- classifying a cause ------------------------------------------------------


def field_mappings(config: CompiledConfig) -> dict[str, CompiledMapping]:
    """Lakehouse column name -> the mapping that produces it."""
    return {m.column.name: m for m in config.mappings}


def classify_cause(lakehouse_field: str, mapping: CompiledMapping | None) -> str:
    # Checked first, regardless of whether a mapping exists: most resolution columns (ACCOUNT_ID,
    # SECURITY_ID, FIRM_ID, ...) are filled by the resolution stage with no mapping at all
    # (astra_data.compiler's own STAGE_COLUMNS design), but CUSTODIAN_SECURITY_ID is both a
    # resolution column and a real, direct mapping in the committed pershing_position.yaml — the
    # resolution reason applies either way, so it must not be shadowed by the mapping checks below.
    if lakehouse_field in RESOLUTION_COLUMNS:
        return "resolution"
    if mapping is None:
        return "unmapped"
    if mapping.transform is not None:
        return "transform"
    return "unexplained"


def _describe(cause: str, field: str, mapping: CompiledMapping | None) -> str:
    if cause == "transform":
        return f"{field} is produced by {mapping.transform.text}; the two pipelines' values differ within what a rounding or scale difference in this transform could explain."
    if cause == "resolution":
        return f"{field} is a resolution-produced column; the two pipelines' values differ within what different reference-data timing between legacy and the lakehouse could explain."
    if cause == "unmapped":
        return f"{field} has no direct mapping in this config; it is likely computed downstream, which this agent does not trace."
    return f"{field} is a direct, untransformed mapping with no resolution dependency; nothing in the config explains a difference here."


# -- one explanation ------------------------------------------------------------


@dataclass(frozen=True)
class Explanation:
    key: tuple[str | None, ...]
    field: str
    legacy: str | None
    lakehouse: str | None
    cause: str
    rule_id: str | None
    rule_text: str | None
    description: str

    @property
    def explained(self) -> bool:
        return self.cause != "unexplained"

    def to_dict(self) -> dict:
        return {
            "key": list(self.key),
            "field": self.field,
            "legacy": self.legacy,
            "lakehouse": self.lakehouse,
            "cause": self.cause,
            "explained": self.explained,
            "rule_id": self.rule_id,
            "rule_text": self.rule_text,
            "description": self.description,
        }


def explain_field(field_mismatch: FieldMismatch, key: tuple[str | None, ...], parity_mapping: ParityMapping, mappings_by_column: dict[str, CompiledMapping]) -> Explanation:
    legacy_to_lakehouse = {f.legacy: f.lakehouse for f in parity_mapping.fields}
    lakehouse_field = legacy_to_lakehouse.get(field_mismatch.field, field_mismatch.field)
    mapping = mappings_by_column.get(lakehouse_field)
    cause = classify_cause(lakehouse_field, mapping)
    rule = mapping.rule if mapping else None
    return Explanation(
        key=key,
        field=lakehouse_field,
        legacy=field_mismatch.legacy,
        lakehouse=field_mismatch.lakehouse,
        cause=cause,
        rule_id=rule.id if rule else None,
        rule_text=rule.text if rule else None,
        description=_describe(cause, lakehouse_field, mapping),
    )


def explain_row(mismatch: RowMismatch, parity_mapping: ParityMapping, mappings_by_column: dict[str, CompiledMapping]) -> tuple[Explanation, ...]:
    return tuple(explain_field(fm, mismatch.key, parity_mapping, mappings_by_column) for fm in mismatch.fields)


# -- the draft -----------------------------------------------------------------


@dataclass(frozen=True)
class ExplainDraft:
    custodian: str
    business_date: str
    explanations: tuple[Explanation, ...]

    @property
    def explained_count(self) -> int:
        return sum(1 for e in self.explanations if e.explained)

    @property
    def explained_rate(self) -> float:
        return self.explained_count / len(self.explanations) if self.explanations else 1.0

    @property
    def by_cause(self) -> dict[str, tuple[Explanation, ...]]:
        return {cause: tuple(e for e in self.explanations if e.cause == cause) for cause in CAUSES}

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "business_date": self.business_date,
            "total": len(self.explanations),
            "explained": self.explained_count,
            "explained_rate": round(self.explained_rate, 4),
            "by_cause": {cause: len(items) for cause, items in self.by_cause.items()},
            "explanations": [e.to_dict() for e in self.explanations],
        }


def generate(config: CompiledConfig, parity_mapping: ParityMapping, result: ParityResult) -> ExplainDraft:
    mappings_by_column = field_mappings(config)
    explanations = tuple(e for row in result.mismatches for e in explain_row(row, parity_mapping, mappings_by_column))
    return ExplainDraft(custodian=result.custodian, business_date=result.business_date, explanations=explanations)


def run(
    config_path: Path,
    parity_path: Path,
    legacy_rows_path: Path,
    lakehouse_rows_path: Path,
    business_date: date | str,
    *,
    specs_dir: Path = Path("specs"),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
) -> ExplainDraft:
    config = load_compiled_config(config_path, specs_dir=specs_dir, rules_dir=rules_dir, domains_dir=domains_dir)
    parity_mapping = load_parity_mapping(parity_path)
    legacy_rows = load_rows(legacy_rows_path)
    lakehouse_rows = load_rows(lakehouse_rows_path)
    result = compare_rows(parity_mapping, legacy_rows, lakehouse_rows, business_date)
    return generate(config, parity_mapping, result)


# -- the report ------------------------------------------------------------------


def render_markdown(draft: ExplainDraft) -> str:
    out = [f"# Break Explainer: {draft.custodian} {draft.business_date}", ""]
    out.append(f"{len(draft.explanations)} difference(s). {draft.explained_count} explained ({draft.explained_rate:.0%}).")
    out.append("")
    out.append("| Key | Field | Legacy | Lakehouse | Cause | Rule | Explanation |")
    out.append("|---|---|---|---|---|---|---|")
    for e in draft.explanations:
        rule = e.rule_id or "-"
        out.append(f"| {list(e.key)} | {e.field} | {e.legacy} | {e.lakehouse} | {e.cause} | {rule} | {e.description} |")
    out.append("")
    out.append("## By cause")
    out.append("")
    out.append("| Cause | Count |")
    out.append("|---|---|")
    for cause, items in draft.by_cause.items():
        out.append(f"| {cause} | {len(items)} |")
    out.append("")
    unexplained = draft.by_cause.get("unexplained", ())
    if unexplained:
        out.append("## Unexplained — needs a person")
        out.append("")
        out.append("Nothing in the config accounts for these; a steward investigates directly.")
        out.append("")
        for e in unexplained:
            out.append(f"- {list(e.key)}.{e.field}: legacy `{e.legacy}` vs lakehouse `{e.lakehouse}`")
        out.append("")
    return "\n".join(out)


def write_draft(draft: ExplainDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
