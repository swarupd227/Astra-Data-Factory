"""Test Generator: SQL assertion tests and synthetic edge files generated from a config's own
`dq_rules`, one branch at a time (S5.7.1, ADR 0047, product spec Section 6).

A `dq_rule` already names a discrete outcome a table's rows must not violate — a null where one
is forbidden, a value outside a declared set, a count that does not match, a duplicate key, a
date in the future. Each is one branch; this agent enumerates every branch a config's `dq_rules`
declares and, for each, renders `tests/unit/<rule id>.sql` — the same "a test returns failing
rows" convention `astra_knowledge.cdm.render_tests` already uses — next to `tests/edge/<rule id
>_<branch>.dat`, a complete, minimal, entirely synthetic sample file built to trip exactly that
rule and nothing else.

No LLM: every branch this agent knows how to synthesize is read straight off a `dq_rule`'s own
typed fields (`kind`, `field`/`fields`, `values`, `min`/`max`, `trailer_field`) rather than
judged from prose. `condition` is the one kind with no fixed shape — a free SQL string — so only
the one pattern the DQ Generator itself is known to produce (`FIELD <= CURRENT_DATE()`) is
recognized; anything else is reported as not covered rather than guessed at.

Every synthetic value comes from a fixed, small, programmatic vocabulary (`SYNTH`-prefixed
strings, all-zero digits, a fixed placeholder date) — this module never reads reference data,
a real sample file, or anything outside the spec and config it was given, so "contains no real
records" is architectural, not a claim checked after the fact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from astra_core.problems import Problem
from astra_core.schema import describe_error, load_validator, sorted_errors
from astra_core.yamlsource import load
from astra_knowledge.patterns.values import DATE_FORMATS
from astra_knowledge.registry import Field, Record, SourceSpec, load_spec_file
from astra_agents.dq_generator import TRAILER_COUNT_FIELD

CONFIG_SCHEMA = "config-v0.schema.json"
SYNTHETIC_DATE = datetime(2026, 1, 1)
SYNTHETIC_FUTURE_DATE = datetime(2099, 12, 31)
SYNTHETIC_STRING = "SYNTH"
UNRECOGNIZED_CODE = "##"

# The one condition shape the DQ Generator itself is known to produce; any other condition
# string is reported as not covered rather than guessed at (this module cannot parse arbitrary SQL).
DATE_NOT_FUTURE = re.compile(r"^(\w+)\s*<=\s*CURRENT_DATE\(\)$")


class TestGeneratorError(RuntimeError):
    __test__ = False  # pytest: this is not a test class, despite the name matching this agent's own naming convention



def load_spec(path: Path) -> SourceSpec:
    path = Path(path)
    if not path.is_file():
        raise TestGeneratorError(f"spec file not found: {path}")
    spec, problems = load_spec_file(path)
    if problems:
        raise TestGeneratorError("; ".join(p.format() for p in problems))
    return spec


def load_config(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise TestGeneratorError(f"config file not found: {path}")
    data = load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TestGeneratorError(f"{path}: the file must contain a mapping at the top level")
    validator = load_validator("astra_data.schemas", CONFIG_SCHEMA)
    problems = [Problem(str(path), None, describe_error(e)) for e in sorted_errors(validator, data)]
    if problems:
        raise TestGeneratorError("; ".join(p.format() for p in problems))
    return data


# -- rendering a complete, synthetic fixed-width line ----------------------------


def _pad(value: str, field: Field) -> str:
    if field.picture and field.picture.kind == "numeric":
        return value.rjust(field.length, "0")[-field.length :]
    return value.ljust(field.length)[: field.length]


def default_value(field: Field) -> str:
    """A structurally valid, entirely synthetic value for one field: never read from anywhere,
    always the same small vocabulary, so the field is filled without ever needing real data."""
    if field.name == "filler":
        return " " * field.length
    if field.codes:
        return _pad(field.codes[0][0], field)
    if field.type == "date":
        pattern = DATE_FORMATS.get((field.format or "YYYYMMDD").upper(), "%Y%m%d")
        return _pad(SYNTHETIC_DATE.strftime(pattern), field)
    if field.picture and field.picture.kind == "numeric":
        return _pad("0", field)
    return _pad(SYNTHETIC_STRING, field)


def render_record(record: Record, overrides: dict[str, str] | None = None) -> str:
    overrides = overrides or {}
    ordered = sorted(record.fields, key=lambda f: f.start or 0)
    return "".join(_pad(overrides[f.name], f) if f.name in overrides else default_value(f) for f in ordered)


def render_synthetic_file(spec: SourceSpec, *, detail_lines: list[str] | None = None, header_overrides: dict[str, str] | None = None, trailer_overrides: dict[str, str] | None = None) -> str:
    """A minimal, complete, entirely synthetic file: a header (if the spec has one, with any
    given overrides), the given detail lines (default: one, all-default values), and a trailer
    whose control-total field is the real count unless a branch deliberately overrides it."""
    header = spec.header()
    trailer = next((r for r in spec.records if r.type == "trailer"), None)
    detail = next(r for r in spec.records if r.type == "detail")
    lines: list[str] = []
    if header is not None:
        lines.append(render_record(header, header_overrides))
    if detail_lines is None:
        detail_lines = [render_record(detail)]
    lines.extend(detail_lines)
    if trailer is not None:
        overrides = dict(trailer_overrides or {})
        count_field = next((f for f in trailer.fields if TRAILER_COUNT_FIELD.search(f.name)), None)
        if count_field is not None and count_field.name not in overrides:
            overrides[count_field.name] = str(len(detail_lines))
        lines.append(render_record(trailer, overrides))
    return "\n".join(lines) + "\n"


# -- one branch of one rule --------------------------------------------------------


@dataclass(frozen=True)
class EdgeCase:
    rule_id: str
    kind: str
    branch: str
    description: str
    sql: str
    file_name: str | None
    file_content: str | None

    @property
    def has_file(self) -> bool:
        return self.file_content is not None


def _sql(rule_id: str, check: str, condition: str) -> str:
    return "\n".join([f"-- {rule_id}: {check}. Returns failing rows.", "SELECT *", "FROM {{ DATABASE }}.{{ SCHEMA }}.{{ TABLE }}", f"WHERE {condition};"]) + "\n"


def _group_by_sql(rule_id: str, check: str, group_by: str, having: str) -> str:
    return "\n".join([f"-- {rule_id}: {check}. Returns failing rows.", f"SELECT {group_by}, COUNT(*) AS ROW_COUNT", "FROM {{ DATABASE }}.{{ SCHEMA }}.{{ TABLE }}", f"GROUP BY {group_by}", f"HAVING {having};"]) + "\n"


def _record_for(rule: dict, spec: SourceSpec) -> Record | None:
    if rule.get("record"):
        return spec.record(rule["record"])
    if rule.get("level") == "file":
        return spec.header()
    return next((r for r in spec.records if r.type == "detail"), None)


def _not_null_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    record = _record_for(rule, spec)
    field_name = rule["field"]
    sql = _sql(rule["id"], rule["check"], f"{field_name.upper()} IS NULL")
    content = render_synthetic_file(spec, detail_lines=[render_record(record, {field_name: " "})]) if record and record.type == "detail" else None
    return [EdgeCase(rule["id"], rule["kind"], "null", f"{field_name} is blank", sql, f"{rule['id']}_null.dat" if content else None, content)]


def _unique_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    record = _record_for(rule, spec)
    fields = rule.get("fields") or []
    group_by = ", ".join(f.upper() for f in fields)
    sql = _group_by_sql(rule["id"], rule["check"], group_by, "COUNT(*) > 1")
    content = None
    if record and record.type == "detail":
        line = render_record(record)
        content = render_synthetic_file(spec, detail_lines=[line, line])  # the same key twice
    return [EdgeCase(rule["id"], rule["kind"], "duplicate", f"({', '.join(fields)}) repeated across two rows", sql, f"{rule['id']}_duplicate.dat" if content else None, content)]


def _accepted_values_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    record = _record_for(rule, spec)
    field_name = rule["field"]
    values = rule.get("values") or []
    in_list = ", ".join(f"'{v}'" for v in values)
    sql = _sql(rule["id"], rule["check"], f"{field_name.upper()} NOT IN ({in_list})")
    unrecognized = UNRECOGNIZED_CODE if UNRECOGNIZED_CODE not in values else UNRECOGNIZED_CODE * 2
    content = render_synthetic_file(spec, detail_lines=[render_record(record, {field_name: unrecognized})]) if record and record.type == "detail" else None
    return [EdgeCase(rule["id"], rule["kind"], "unrecognized", f"{field_name} is a code not in ({', '.join(values)})", sql, f"{rule['id']}_unrecognized.dat" if content else None, content)]


def _range_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    """The SQL assertion covers min and max in the rule's own logical units, always. The edge
    *file* is only rendered when the boundary value can actually exist as raw bytes: an unsigned
    numeric picture (every field this repository's real specs use) cannot hold a negative raw
    digit string, so "one below a minimum of 0" has no file to render, only the assertion — and
    a field with implied decimals would need its boundary scaled into raw digits to render one
    correctly, which this first version does not yet do (ADR 0047's own consequences)."""
    record = _record_for(rule, spec)
    field_name = rule["field"]
    field = record.field(field_name) if record else None
    unscaled = not (field and field.picture and field.picture.scale)
    cases = []
    if "min" in rule:
        sql = _sql(rule["id"], rule["check"], f"{field_name.upper()} < {rule['min']}")
        below = int(rule["min"]) - 1 if str(rule["min"]).lstrip("-").isdigit() else None
        representable = field is not None and unscaled and below is not None and (below >= 0 or (field.picture and field.picture.signed))
        content = render_synthetic_file(spec, detail_lines=[render_record(record, {field_name: str(below)})]) if representable else None
        cases.append(EdgeCase(rule["id"], rule["kind"], "below_min", f"{field_name} is below the minimum {rule['min']}", sql, f"{rule['id']}_below_min.dat" if content else None, content))
    if "max" in rule:
        sql = _sql(rule["id"], rule["check"], f"{field_name.upper()} > {rule['max']}")
        above = int(rule["max"]) + 1 if str(rule["max"]).lstrip("-").isdigit() else None
        representable = field is not None and unscaled and above is not None
        content = render_synthetic_file(spec, detail_lines=[render_record(record, {field_name: str(above)})]) if representable else None
        cases.append(EdgeCase(rule["id"], rule["kind"], "above_max", f"{field_name} is above the maximum {rule['max']}", sql, f"{rule['id']}_above_max.dat" if content else None, content))
    return cases


def _control_total_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    detail = next(r for r in spec.records if r.type == "detail")
    sql = "\n".join(
        [
            f"-- {rule['id']}: {rule['check']}. Returns a mismatch (empty when the counts agree).",
            "SELECT DETAIL_ROWS, TRAILER_COUNT",
            "FROM (",
            "  SELECT",
            "    (SELECT COUNT(*) FROM {{ DATABASE }}.{{ SCHEMA }}.{{ DETAIL_TABLE }}) AS DETAIL_ROWS,",
            f"    (SELECT {rule['trailer_field'].upper()} FROM {{{{ DATABASE }}}}.{{{{ SCHEMA }}}}.{{{{ TRAILER_TABLE }}}}) AS TRAILER_COUNT",
            ")",
            "WHERE DETAIL_ROWS != TRAILER_COUNT;",
        ]
    )
    line = render_record(detail)
    actual = 1
    content = render_synthetic_file(spec, detail_lines=[line], trailer_overrides={rule["trailer_field"]: str(actual + 1)})
    return [EdgeCase(rule["id"], rule["kind"], "mismatch", f"the trailer's {rule['trailer_field']} does not match the real detail row count", sql, f"{rule['id']}_mismatch.dat", content)]


def _condition_case(rule: dict, spec: SourceSpec) -> list[EdgeCase]:
    match = DATE_NOT_FUTURE.match((rule.get("condition") or "").strip())
    if not match:
        return []
    column = match.group(1)
    record = _record_for(rule, spec)
    field_name = rule.get("field")
    field = record.field(field_name) if record and field_name else None
    if field is None and record is not None:
        field_name = next((f.name for f in record.fields if f.name.upper() == column), None)
        field = record.field(field_name) if field_name else None
    sql = _sql(rule["id"], rule["check"], f"{column} > CURRENT_DATE()")
    content = None
    if record is not None and field is not None:
        future = SYNTHETIC_FUTURE_DATE.strftime(DATE_FORMATS.get((field.format or "YYYYMMDD").upper(), "%Y%m%d"))
        overrides = {field_name: future}
        if record.type == "detail":
            content = render_synthetic_file(spec, detail_lines=[render_record(record, overrides)])
        elif record.type == "header":
            content = render_synthetic_file(spec, header_overrides=overrides)
    return [EdgeCase(rule["id"], rule["kind"], "future", f"{field_name or column} is a future date", sql, f"{rule['id']}_future.dat" if content else None, content)]


BRANCH_GENERATORS = {
    "not_null": _not_null_case,
    "unique": _unique_case,
    "accepted_values": _accepted_values_case,
    "range": _range_case,
    "control_total": _control_total_case,
    "condition": _condition_case,
}


# -- the draft ---------------------------------------------------------------------


@dataclass(frozen=True)
class TestDraft:
    config_id: str
    spec_id: str
    spec_version: str
    rule_ids: tuple[str, ...]
    covered_rule_ids: tuple[str, ...]
    cases: tuple[EdgeCase, ...]

    @property
    def uncovered_rule_ids(self) -> tuple[str, ...]:
        covered = set(self.covered_rule_ids)
        return tuple(r for r in self.rule_ids if r not in covered)

    @property
    def coverage(self) -> float:
        return len(self.covered_rule_ids) / len(self.rule_ids) if self.rule_ids else 1.0

    @property
    def ok(self) -> bool:
        return not self.uncovered_rule_ids

    def to_dict(self) -> dict:
        return {
            "config_id": self.config_id,
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "rule_count": len(self.rule_ids),
            "covered_rule_ids": list(self.covered_rule_ids),
            "uncovered_rule_ids": list(self.uncovered_rule_ids),
            "coverage": round(self.coverage, 4),
            "cases": [{"rule_id": c.rule_id, "kind": c.kind, "branch": c.branch, "description": c.description, "has_file": c.has_file} for c in self.cases],
        }


def generate(config: dict, spec: SourceSpec) -> TestDraft:
    dq_rules = config.get("dq_rules") or []
    rule_ids = tuple(r["id"] for r in dq_rules)
    cases: list[EdgeCase] = []
    covered: list[str] = []
    for rule in dq_rules:
        generator = BRANCH_GENERATORS.get(rule["kind"])
        found = generator(rule, spec) if generator else []
        if found:
            covered.append(rule["id"])
        cases.extend(found)
    return TestDraft(
        config_id=config["source"]["id"],
        spec_id=spec.id,
        spec_version=spec.version,
        rule_ids=rule_ids,
        covered_rule_ids=tuple(covered),
        cases=tuple(cases),
    )


def run(config_path: Path, spec_path: Path) -> TestDraft:
    config = load_config(config_path)
    spec = load_spec(spec_path)
    return generate(config, spec)


# -- the report and the files ------------------------------------------------------


def render_markdown(draft: TestDraft) -> str:
    out = [f"# Test Generator draft: {draft.config_id}", ""]
    out.append(f"{len(draft.rule_ids)} rule(s) in the config's dq_rules. {len(draft.covered_rule_ids)} covered by at least one generated branch ({draft.coverage:.0%}).")
    out.append("")
    out.append("| Rule | Kind | Branch | Description | Edge file |")
    out.append("|---|---|---|---|---|")
    for c in draft.cases:
        out.append(f"| {c.rule_id} | {c.kind} | {c.branch} | {c.description} | {'yes' if c.has_file else 'no'} |")
    out.append("")
    if draft.uncovered_rule_ids:
        out.append("## Not covered")
        out.append("")
        out.append("This agent does not yet know how to synthesize a branch for these rules — a `condition` whose SQL is not the one recognized shape, or a kind this story does not cover:")
        out.append("")
        for rule_id in draft.uncovered_rule_ids:
            out.append(f"- {rule_id}")
        out.append("")
    return "\n".join(out)


def write_draft(draft: TestDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    unit_dir = out / "tests" / "unit"
    edge_dir = out / "tests" / "edge"
    unit_dir.mkdir(parents=True, exist_ok=True)
    edge_dir.mkdir(parents=True, exist_ok=True)
    for c in draft.cases:
        (unit_dir / f"{c.rule_id}_{c.branch}.sql").write_text(c.sql, encoding="utf-8", newline="\n")
        if c.has_file:
            (edge_dir / c.file_name).write_text(c.file_content, encoding="utf-8", newline="\n")
    report_path = out / "report.md"
    data_path = out / "report.json"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report_path, data_path
