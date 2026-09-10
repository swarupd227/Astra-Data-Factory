"""Parity engine: row and field comparison between legacy golden output and lakehouse output (S4.2.1, ADR 0033).

A parity mapping (`golden/<custodian>/parity.yaml`, schema `parity-v0`)
says which golden capture output is the oracle for a custodian, which
lakehouse table it compares against, the columns that key a row on each
side, and the columns compared for value with a tolerance per field. The
same shape of column can differ across the two systems (a decimal from
SQL Server text versus a Snowflake NUMBER, for example), so every
comparison goes through the field's declared type and tolerance rather
than string equality.

`compare_rows` is pure: two lists of plain dict rows in, a `ParityResult`
out, with no I/O and no Snowflake, so the classification rules (missing,
extra, value mismatch by field) are fully covered without an account.
`run` wires it to real data: the legacy rows come from the golden store
(S4.1.2, the dataset captured for the business date being checked), the
lakehouse rows from a live query against the environment, and the match
rate is what the deployed lakehouse actually produced for that day, not
a rerun in a sandbox.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_data.bundle import Executor

from astra_verification.golden import ObjectStore, dataset_prefix, load_index

SCHEMA = "parity-v0.schema.json"
PARITY_FILE = "parity.yaml"
MATCH_RATE_TARGET = 0.995  # product spec's north-star: share of rows reaching parity


class ParityError(RuntimeError):
    pass


# -- the mapping --------------------------------------------------------------


@dataclass(frozen=True)
class Tolerance:
    kind: str = "exact"  # exact, decimal_places, absolute, relative
    places: int | None = None
    epsilon: float | None = None

    def matches(self, legacy: str | None, lakehouse: str | None) -> bool:
        if legacy is None and lakehouse is None:
            return True
        if legacy is None or lakehouse is None:
            return False
        if self.kind == "exact":
            return legacy == lakehouse
        try:
            a, b = Decimal(legacy), Decimal(lakehouse)
        except InvalidOperation:
            return legacy == lakehouse  # not numbers after all; fall back to exact
        if self.kind == "decimal_places":
            q = Decimal(1).scaleb(-self.places)
            return a.quantize(q, rounding=ROUND_HALF_UP) == b.quantize(q, rounding=ROUND_HALF_UP)
        if self.kind == "absolute":
            return abs(a - b) <= Decimal(str(self.epsilon))
        if self.kind == "relative":
            denom = max(abs(a), abs(b)) or Decimal(1)
            return abs(a - b) / denom <= Decimal(str(self.epsilon))
        raise ValueError(f"unknown tolerance kind {self.kind!r}")  # pragma: no cover - the schema restricts this

    def to_dict(self) -> dict:
        return {"kind": self.kind, "places": self.places, "epsilon": self.epsilon}


@dataclass(frozen=True)
class FieldMapping:
    legacy: str
    lakehouse: str
    type: str = "string"
    tolerance: Tolerance = field(default_factory=Tolerance)


@dataclass(frozen=True)
class ParityMapping:
    custodian: str
    description: str
    legacy_output: str
    schema: str
    table: str
    custodian_column: str
    business_date_column: str
    keys: tuple[FieldMapping, ...]
    fields: tuple[FieldMapping, ...]
    path: Path

    def key_of(self, row: dict[str, str | None], side: str) -> tuple[str | None, ...]:
        attr = "legacy" if side == "legacy" else "lakehouse"
        return tuple(row.get(getattr(k, attr)) for k in self.keys)


def _tolerance(raw: dict | None) -> Tolerance:
    if not raw:
        return Tolerance()
    return Tolerance(raw["kind"], raw.get("places"), raw.get("epsilon"))


def _field(raw: dict) -> FieldMapping:
    return FieldMapping(raw["legacy"], raw["lakehouse"], raw.get("type", "string"), _tolerance(raw.get("tolerance")))


def load_parity(path: Path, root: Path | None = None) -> tuple[ParityMapping | None, list[Problem]]:
    path = Path(path)
    display = display_path(path, root)
    if not path.is_file():
        return None, [Problem(display, None, "no such file")]
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    if data.get("parity_version") != 0:
        line = line_of(data, ["parity_version"]) if "parity_version" in data else 1
        return None, [Problem(display, line, f"parity_version must be 0; found {data.get('parity_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_verification.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    for i, raw in enumerate(data["keys"]):
        if raw.get("tolerance"):
            problems.append(Problem(display, line_of(data, ["keys", i, "tolerance"]), f"keys[{i}]: a key cannot have a tolerance; keys are compared exactly, and rows that differ only within a tolerance on a key are not the same row"))
    seen_legacy: dict[str, str] = {}
    seen_lakehouse: dict[str, str] = {}
    for where, raw, i in [("keys", r, i) for i, r in enumerate(data["keys"])] + [("fields", r, i) for i, r in enumerate(data["fields"])]:
        if raw["legacy"] in seen_legacy:
            problems.append(Problem(display, line_of(data, [where, i, "legacy"]), f"{where}[{i}]: legacy column '{raw['legacy']}' is already used by {seen_legacy[raw['legacy']]}"))
        seen_legacy[raw["legacy"]] = f"{where}[{i}]"
        if raw["lakehouse"] in seen_lakehouse:
            problems.append(Problem(display, line_of(data, [where, i, "lakehouse"]), f"{where}[{i}]: lakehouse column '{raw['lakehouse']}' is already used by {seen_lakehouse[raw['lakehouse']]}"))
        seen_lakehouse[raw["lakehouse"]] = f"{where}[{i}]"
    if problems:
        return None, problems

    mapping = ParityMapping(
        custodian=data["custodian"],
        description=" ".join(str(data.get("description", "")).split()),
        legacy_output=data["legacy"]["output"],
        schema=data["lakehouse"]["schema"],
        table=data["lakehouse"]["table"],
        custodian_column=data["lakehouse"]["custodian_column"],
        business_date_column=data["lakehouse"]["business_date_column"],
        keys=tuple(_field(r) for r in data["keys"]),
        fields=tuple(_field(r) for r in data["fields"]),
        path=path,
    )
    return mapping, []


def discover(golden_dir: Path) -> list[Path]:
    return sorted(p for p in Path(golden_dir).glob(f"*/{PARITY_FILE}") if p.is_file())


def check(golden_dir: Path, root: Path | None = None) -> tuple[list[ParityMapping], list[Problem]]:
    mappings: list[ParityMapping] = []
    problems: list[Problem] = []
    for path in discover(golden_dir):
        mapping, found = load_parity(path, root)
        problems.extend(found)
        if mapping is None:
            continue
        if mapping.custodian != path.parent.name:
            problems.append(Problem(display_path(path, root), None, f"custodian '{mapping.custodian}' must match the directory '{path.parent.name}'"))
        mappings.append(mapping)
    return mappings, problems


# -- the comparison engine, pure ----------------------------------------------


@dataclass(frozen=True)
class FieldMismatch:
    field: str
    legacy: str | None
    lakehouse: str | None


@dataclass(frozen=True)
class RowMismatch:
    key: tuple[str | None, ...]
    fields: tuple[FieldMismatch, ...]

    def to_dict(self) -> dict:
        return {"key": list(self.key), "fields": [{"field": f.field, "legacy": f.legacy, "lakehouse": f.lakehouse} for f in self.fields]}


@dataclass
class ParityResult:
    custodian: str
    business_date: str
    legacy_output: str
    table: str
    legacy_rows: int = 0
    lakehouse_rows: int = 0
    matched: int = 0
    missing: list[tuple] = field(default_factory=list)  # keys in legacy, absent from the lakehouse
    extra: list[tuple] = field(default_factory=list)  # keys in the lakehouse, absent from legacy
    mismatches: list[RowMismatch] = field(default_factory=list)  # keys in both, at least one field beyond tolerance
    duplicate_legacy_keys: list[tuple] = field(default_factory=list)
    duplicate_lakehouse_keys: list[tuple] = field(default_factory=list)

    @property
    def field_summary(self) -> dict[str, int]:
        """Rows mismatching on each field, so differences are grouped by field as well as listed per row."""
        counts: dict[str, int] = {}
        for row in self.mismatches:
            for f in row.fields:
                counts[f.field] = counts.get(f.field, 0) + 1
        return counts

    @property
    def match_rate(self) -> float:
        if self.legacy_rows == 0:
            return 1.0 if self.lakehouse_rows == 0 else 0.0
        return self.matched / self.legacy_rows

    @property
    def meets_target(self) -> bool:
        return self.match_rate >= MATCH_RATE_TARGET

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "business_date": self.business_date,
            "legacy_output": self.legacy_output,
            "table": self.table,
            "legacy_rows": self.legacy_rows,
            "lakehouse_rows": self.lakehouse_rows,
            "matched": self.matched,
            "match_rate": round(self.match_rate, 6),
            "meets_target": self.meets_target,
            "target": MATCH_RATE_TARGET,
            "missing": [list(k) for k in self.missing],
            "extra": [list(k) for k in self.extra],
            "mismatches": [m.to_dict() for m in self.mismatches],
            "field_summary": self.field_summary,
            "duplicate_legacy_keys": [list(k) for k in self.duplicate_legacy_keys],
            "duplicate_lakehouse_keys": [list(k) for k in self.duplicate_lakehouse_keys],
        }


def _index(rows: Iterable[dict[str, str | None]], mapping: ParityMapping, side: str) -> tuple[dict[tuple, dict], list[tuple]]:
    indexed: dict[tuple, dict] = {}
    duplicates: list[tuple] = []
    for row in rows:
        key = mapping.key_of(row, side)
        if key in indexed:
            duplicates.append(key)
            continue
        indexed[key] = row
    return indexed, duplicates


def compare_rows(mapping: ParityMapping, legacy_rows: Iterable[dict[str, str | None]], lakehouse_rows: Iterable[dict[str, str | None]], business_date: date | str) -> ParityResult:
    """Match legacy rows to lakehouse rows by key; classify every difference as missing, extra or a value mismatch by field."""
    legacy_index, legacy_dupes = _index(legacy_rows, mapping, "legacy")
    lakehouse_index, lakehouse_dupes = _index(lakehouse_rows, mapping, "lakehouse")
    result = ParityResult(mapping.custodian, str(business_date), mapping.legacy_output, mapping.table, len(legacy_index), len(lakehouse_index), duplicate_legacy_keys=legacy_dupes, duplicate_lakehouse_keys=lakehouse_dupes)

    for key, legacy_row in legacy_index.items():
        lakehouse_row = lakehouse_index.get(key)
        if lakehouse_row is None:
            result.missing.append(key)
            continue
        mismatched_fields = [
            FieldMismatch(f.legacy, legacy_row.get(f.legacy), lakehouse_row.get(f.lakehouse))
            for f in mapping.fields
            if not f.tolerance.matches(legacy_row.get(f.legacy), lakehouse_row.get(f.lakehouse))
        ]
        if mismatched_fields:
            result.mismatches.append(RowMismatch(key, tuple(mismatched_fields)))
        else:
            result.matched += 1
    for key in lakehouse_index:
        if key not in legacy_index:
            result.extra.append(key)
    return result


# -- loading real rows ----------------------------------------------------------


def load_legacy_rows(golden_dir: Path, store: ObjectStore, custodian: str, business_date: date, output: str, version: int | None = None) -> list[dict[str, str | None]]:
    """The golden dataset's captured rows of one output for the business date; the latest version unless one is named."""
    if version is None:
        index = load_index(golden_dir, custodian)
        entry = next((d for d in reversed(index["datasets"]) if d["business_date"] == business_date.isoformat()), None)
        if entry is None:
            raise ParityError(f"no golden dataset captured for {custodian} {business_date.isoformat()}; run astra-verify golden capture first")
        version = entry["version"]
    key = f"{dataset_prefix(custodian, business_date, version)}/outputs/{output}.csv"
    if not store.exists(key):
        raise ParityError(f"{key}: not in {store.uri}; the golden dataset for {custodian} {business_date.isoformat()} v{version} has no '{output}' output")
    text = store.get(key).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{k: (v if v else None) for k, v in row.items()} for row in rows]


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def lakehouse_columns(mapping: ParityMapping) -> list[str]:
    """Every lakehouse column the comparison reads, keys then fields, each named once."""
    return list(dict.fromkeys([k.lakehouse for k in mapping.keys] + [f.lakehouse for f in mapping.fields]))


def lakehouse_query(mapping: ParityMapping, database: str, business_date: date) -> str:
    columns = ", ".join(f'"{c}"' for c in lakehouse_columns(mapping))
    keys = ", ".join(f'"{k.lakehouse}"' for k in mapping.keys)
    return (
        f"SELECT {columns}\n"
        f'FROM "{database}"."{mapping.schema}"."{mapping.table}"\n'
        f'WHERE "{mapping.custodian_column}" = {_lit(mapping.custodian)} AND "{mapping.business_date_column}" = {_lit(business_date.isoformat())}\n'
        f"ORDER BY {keys}"
    )


def load_lakehouse_rows(executor: Executor, mapping: ParityMapping, database: str, business_date: date) -> list[dict[str, str | None]]:
    columns = lakehouse_columns(mapping)
    rows = executor.query(lakehouse_query(mapping, database, business_date))
    return [{col: (None if value is None else str(value)) for col, value in zip(columns, row)} for row in rows]


def run(mapping: ParityMapping, golden_dir: Path, store: ObjectStore, executor: Executor, database: str, business_date: date, *, legacy_version: int | None = None, out: Path | None = None) -> ParityResult:
    legacy_rows = load_legacy_rows(golden_dir, store, mapping.custodian, business_date, mapping.legacy_output, legacy_version)
    lakehouse_rows = load_lakehouse_rows(executor, mapping, database, business_date)
    result = compare_rows(mapping, legacy_rows, lakehouse_rows, business_date)
    if out is not None:
        write_report(result, out)
    return result


# -- the report ------------------------------------------------------------------


def render_markdown(result: ParityResult) -> str:
    out = [f"# Parity: {result.custodian} {result.business_date}", ""]
    out.append(f"Legacy `{result.legacy_output}` vs lakehouse `{result.table}`. {result.legacy_rows} legacy row(s), {result.lakehouse_rows} lakehouse row(s), {result.matched} matched.")
    out.append("")
    out.append(f"**Match rate: {result.match_rate:.4%}** (target {MATCH_RATE_TARGET:.1%}) — {'meets target' if result.meets_target else 'below target'}.")
    out.append("")
    out.append("## Differences")
    out.append("")
    out.append(f"Missing (in legacy, not the lakehouse): {len(result.missing)}. Extra (in the lakehouse, not legacy): {len(result.extra)}. Value mismatch: {len(result.mismatches)} row(s).")
    out.append("")
    if result.field_summary:
        out.append("| Field | Rows mismatching |")
        out.append("|---|---|")
        for name, count in sorted(result.field_summary.items(), key=lambda kv: -kv[1]):
            out.append(f"| `{name}` | {count} |")
        out.append("")
    if result.mismatches:
        out.append("Sample mismatches:")
        out.append("")
        out.append("| Key | Field | Legacy | Lakehouse |")
        out.append("|---|---|---|---|")
        for row in result.mismatches[:10]:
            for f in row.fields:
                out.append(f"| {list(row.key)} | `{f.field}` | {f.legacy} | {f.lakehouse} |")
        out.append("")
    if result.missing:
        out.append(f"Sample missing keys: {[list(k) for k in result.missing[:10]]}")
        out.append("")
    if result.extra:
        out.append(f"Sample extra keys: {[list(k) for k in result.extra[:10]]}")
        out.append("")
    if result.duplicate_legacy_keys or result.duplicate_lakehouse_keys:
        out.append(f"Duplicate keys (the first row was compared, the rest ignored): {len(result.duplicate_legacy_keys)} in legacy, {len(result.duplicate_lakehouse_keys)} in the lakehouse.")
        out.append("")
    return "\n".join(out)


def write_report(result: ParityResult, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "parity.md"
    data = out / "parity.json"
    markdown.write_text(render_markdown(result), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
