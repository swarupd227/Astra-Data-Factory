"""Replay a config change against N days of history: a difference report grouped by rule and field (S4.1.3, ADR 0032).

A steward drafts a change to an already-promoted config. Before asking to
promote it, they want to know what changes for real history, not just
whether it compiles. Replay runs the old config and the new config
through the pipeline's own path (the same sandbox mechanics as the dry
run, S4.1.1) against the same historical files, then diffs the two runs:

  config diff   every mapping, rule, DQ rule and resolution part that
                differs between the two configs, from the compiled
                models alone, no data involved
  data diff     the canonical entity's rows after each run, compared by
                key; every differing column is attributed to the mapping,
                rule or resolution part that produced it
  exception diff  rejection code counts, before and after
  test diff     which rendered tests changed pass/fail

The historical files are the ones already captured for the custodian
(S4.1.2): replay reads the golden index for N business days and fetches
the same files the capture used, so a replay and a capture agree on what
"history" means. A change with no row, exception or test difference is
reported as eligible for promotion without SME review; any difference is
listed under the rule or field responsible, not just as a raw row dump.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from astra_data.bundle import Executor
from astra_data.compiler import CompiledConfig

from astra_verification.dryrun import DryRunReport, compile_and_render, dry_run
from astra_verification.golden import CAPTURE_FILE, MIN_DAYS, file_source, load_capture, load_index
from astra_verification.sandbox import SandboxSpec


class ReplayError(RuntimeError):
    pass


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# -- the config diff: from the two compiled models, no data involved --------


@dataclass(frozen=True)
class FieldDiff:
    group: str  # rule id ("pershing_gcus.quantity_sign"), "mapping: <record>.<field>", "constant: <column>", "dq:<id>", "rule:<id>" or "resolution:<part>"
    column: str | None
    kind: str  # added, removed, changed
    before: object
    after: object
    description: str


@dataclass
class ConfigDiff:
    fields: list[FieldDiff] = field(default_factory=list)
    other: list[str] = field(default_factory=list)  # config differences outside mappings, rules, dq_rules and resolution

    @property
    def changed(self) -> bool:
        return bool(self.fields or self.other)

    def to_dict(self) -> dict:
        return {"fields": [{"group": f.group, "column": f.column, "kind": f.kind, "before": f.before, "after": f.after, "description": f.description} for f in self.fields], "other": self.other}


def _attribution(mapping) -> str:
    if mapping.rule:
        return mapping.rule.id
    if mapping.source:
        return f"mapping: {mapping.record}.{mapping.source.name}"
    return f"constant: {mapping.column.name}"


def _origin(mapping) -> str:
    base = f"{mapping.record}.{mapping.source.name} ({mapping.source_type})" if mapping.source else f"constant {mapping.constant}"
    return f"{base} via {mapping.transform.text}" if mapping.transform else base


def config_diff(old: CompiledConfig, new: CompiledConfig) -> ConfigDiff:
    """Every mapping, rule, DQ rule and resolution part that differs, grouped the same way the data diff groups rows."""
    diff = ConfigDiff()

    old_map = {(m.entity.table, m.column.name): m for m in old.mappings}
    new_map = {(m.entity.table, m.column.name): m for m in new.mappings}
    for table, column in sorted(set(old_map) | set(new_map)):
        om, nm = old_map.get((table, column)), new_map.get((table, column))
        if om is None:
            diff.fields.append(FieldDiff(_attribution(nm), column, "added", None, nm.to_dict(), f"{table}.{column} is now mapped ({_origin(nm)})"))
        elif nm is None:
            diff.fields.append(FieldDiff(_attribution(om), column, "removed", om.to_dict(), None, f"{table}.{column} is no longer mapped (was {_origin(om)})"))
        elif om.to_dict() != nm.to_dict():
            diff.fields.append(FieldDiff(_attribution(nm), column, "changed", om.to_dict(), nm.to_dict(), f"{table}.{column}: {_origin(om)} -> {_origin(nm)}"))

    old_dq = {r.id: r for r in old.dq_rules}
    new_dq = {r.id: r for r in new.dq_rules}
    for rid in sorted(set(old_dq) | set(new_dq)):
        o, n = old_dq.get(rid), new_dq.get(rid)
        if o is None:
            diff.fields.append(FieldDiff(f"dq:{rid}", None, "added", None, n.to_dict(), f"dq_rules.{rid} added ({n.kind}, {n.level})"))
        elif n is None:
            diff.fields.append(FieldDiff(f"dq:{rid}", None, "removed", o.to_dict(), None, f"dq_rules.{rid} removed"))
        elif o.to_dict() != n.to_dict():
            diff.fields.append(FieldDiff(f"dq:{rid}", None, "changed", o.to_dict(), n.to_dict(), f"dq_rules.{rid} changed"))

    old_rules = {r.id: r for r in old.rules}
    new_rules = {r.id: r for r in new.rules}
    for rid in sorted(set(old_rules) | set(new_rules)):
        o, n = old_rules.get(rid), new_rules.get(rid)
        if o is None:
            diff.fields.append(FieldDiff(f"rule:{rid}", None, "added", None, {"status": n.status, "text": n.text}, f"rule {rid} is now referenced (status {n.status})"))
        elif n is None:
            diff.fields.append(FieldDiff(f"rule:{rid}", None, "removed", {"status": o.status, "text": o.text}, None, f"rule {rid} is no longer referenced"))
        elif o.status != n.status or o.text != n.text:
            diff.fields.append(FieldDiff(f"rule:{rid}", None, "changed", {"status": o.status, "text": o.text}, {"status": n.status, "text": n.text}, f"rule {rid} changed ({o.status} -> {n.status})" if o.status != n.status else f"rule {rid} text changed"))

    old_res, new_res = old.resolution.to_dict(), new.resolution.to_dict()
    for part in sorted(set(old_res) | set(new_res)):
        if old_res.get(part) != new_res.get(part):
            diff.fields.append(FieldDiff(f"resolution:{part}", None, "changed", old_res.get(part), new_res.get(part), f"resolution.{part} changed"))

    old_d, new_d = old.to_dict(), new.to_dict()
    for key in ("delivery", "alerts", "processing", "pattern", "target_profile", "effective_from", "owner", "domain_pack"):
        if old_d.get(key) != new_d.get(key):
            diff.other.append(f"{key}: {old_d.get(key)!r} -> {new_d.get(key)!r}")

    return diff


# -- the canonical rows, read while the sandbox still exists ----------------


@dataclass(frozen=True)
class CanonicalSnapshot:
    entity: str | None
    key: tuple[str, ...]
    rows: dict[tuple[str | None, ...], dict[str, str | None]]


def _table_columns(executor: Executor, database: str, schema: str, table: str) -> list[str]:
    rows = executor.query(f'SELECT "COLUMN_NAME" FROM "{database}".INFORMATION_SCHEMA.COLUMNS WHERE "TABLE_SCHEMA" = {_lit(schema)} AND "TABLE_NAME" = {_lit(table)} ORDER BY "ORDINAL_POSITION"')
    return [r[0] for r in rows]


def canonical_row_query(spec: SandboxSpec, compiled: CompiledConfig) -> str | None:
    entity = compiled.target_entity
    if entity is None:
        return None
    keys = ", ".join(f'"{k}"' for k in entity.key)
    return f'SELECT * FROM "{spec.database}"."SILVER"."{entity.table}" WHERE "CUSTODIAN_ID" = {_lit(compiled.source["custodian"])} ORDER BY {keys}'


def capture_canonical_snapshot(executor: Executor, spec: SandboxSpec, compiled: CompiledConfig) -> CanonicalSnapshot:
    """Every row of the canonical entity for this custodian, keyed, lineage columns left out (they differ between any two runs by construction)."""
    entity = compiled.target_entity
    if entity is None:
        return CanonicalSnapshot(None, (), {})
    query = canonical_row_query(spec, compiled)
    columns = _table_columns(executor, spec.database, "SILVER", entity.table)
    exclude = {c.name for c in compiled.model.lineage}
    kept = [c for c in columns if c not in exclude]
    rows: dict[tuple[str | None, ...], dict[str, str | None]] = {}
    for row in executor.query(query):
        values = {col: (None if v is None else str(v)) for col, v in zip(columns, row)}
        key = tuple(values[k] for k in entity.key)
        rows[key] = {c: values[c] for c in kept}
    return CanonicalSnapshot(entity.table, entity.key, rows)


def _capture_hook(holder: dict) -> Callable[[Executor, SandboxSpec, CompiledConfig, DryRunReport], None]:
    def hook(executor: Executor, spec: SandboxSpec, compiled: CompiledConfig, report: DryRunReport) -> None:
        holder["snapshot"] = capture_canonical_snapshot(executor, spec, compiled)

    return hook


def replay_config(
    config: Path,
    samples: list[Path],
    spec: SandboxSpec,
    executor: Executor,
    *,
    repo: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    cdm_ddl: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[DryRunReport, CanonicalSnapshot]:
    """One side of a replay: a dry run whose canonical rows are captured before the sandbox is destroyed."""
    holder: dict = {}
    report = dry_run(config, samples, spec, executor, repo=repo, out=out, extra_bundles=extra_bundles, cdm_ddl=cdm_ddl, clock=clock, monotonic=monotonic, on_before_destroy=_capture_hook(holder))
    return report, holder.get("snapshot", CanonicalSnapshot(None, (), {}))


# -- the data diff ------------------------------------------------------------


@dataclass(frozen=True)
class RowDiff:
    key: tuple[str | None, ...]
    kind: str  # added, removed, changed
    columns: dict[str, tuple[str | None, str | None]]  # changed columns only; before, after


@dataclass
class Group:
    keys: set = field(default_factory=set)
    samples: list[dict] = field(default_factory=list)

    def add(self, key, column, before, after, limit: int = 5) -> None:
        self.keys.add(key)
        if len(self.samples) < limit:
            self.samples.append({"key": list(key), "column": column, "before": before, "after": after})

    def to_dict(self) -> dict:
        return {"rows_affected": len(self.keys), "samples": self.samples}


def diff_rows(old: CanonicalSnapshot, new: CanonicalSnapshot, new_config: CompiledConfig) -> tuple[list[RowDiff], dict[str, Group]]:
    """Every row that differs between the two snapshots, and the same differences grouped by the rule or field responsible."""
    attributions = {m.column.name: _attribution(m) for m in new_config.mappings}
    row_diffs: list[RowDiff] = []
    groups: dict[str, Group] = {}

    def bump(attr: str, key, column: str | None, before, after) -> None:
        groups.setdefault(attr, Group()).add(key, column, before, after)

    for key in sorted(set(old.rows) | set(new.rows), key=lambda k: tuple("" if v is None else v for v in k)):
        o, n = old.rows.get(key), new.rows.get(key)
        if o is None:
            row_diffs.append(RowDiff(key, "added", {}))
            for column, value in n.items():
                bump(attributions.get(column, "unmapped"), key, column, None, value)
        elif n is None:
            row_diffs.append(RowDiff(key, "removed", {}))
            for column, value in o.items():
                bump(attributions.get(column, "unmapped"), key, column, value, None)
        else:
            changed = {c: (o.get(c), n.get(c)) for c in sorted(set(o) | set(n)) if o.get(c) != n.get(c)}
            if changed:
                row_diffs.append(RowDiff(key, "changed", changed))
                for column, (before, after) in changed.items():
                    bump(attributions.get(column, "unmapped"), key, column, before, after)
    return row_diffs, groups


def exception_delta(old_report: DryRunReport, new_report: DryRunReport) -> list[dict]:
    def index(report: DryRunReport) -> dict:
        return {(e["code"], e["level"], e["stage"]): e["rows"] for e in report.exceptions}

    o, n = index(old_report), index(new_report)
    out = []
    for key in sorted(set(o) | set(n)):
        ov, nv = o.get(key, 0), n.get(key, 0)
        if ov != nv:
            out.append({"code": key[0], "level": key[1], "stage": key[2], "old_rows": ov, "new_rows": nv, "delta": nv - ov})
    return out


def test_delta(old_report: DryRunReport, new_report: DryRunReport) -> list[dict]:
    o = {t["test"]: t["passed"] for t in old_report.tests}
    n = {t["test"]: t["passed"] for t in new_report.tests}
    out = []
    for test in sorted(set(o) | set(n)):
        if o.get(test) != n.get(test):
            out.append({"test": test, "old_passed": o.get(test), "new_passed": n.get(test)})
    return out


# -- the replay run -----------------------------------------------------------


@dataclass
class ReplayReport:
    old_config: str
    new_config: str
    custodian: str
    started_at: str
    business_days: list[str]
    sample_files: list[str]
    config_diff: ConfigDiff
    old_run: DryRunReport
    new_run: DryRunReport
    rows_compared: int = 0
    row_diffs: list[RowDiff] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    exception_delta: list[dict] = field(default_factory=list)
    test_delta: list[dict] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def rows_added(self) -> int:
        return sum(1 for d in self.row_diffs if d.kind == "added")

    @property
    def rows_removed(self) -> int:
        return sum(1 for d in self.row_diffs if d.kind == "removed")

    @property
    def rows_changed(self) -> int:
        return sum(1 for d in self.row_diffs if d.kind == "changed")

    @property
    def data_identical(self) -> bool:
        return not self.row_diffs and not self.exception_delta and not self.test_delta

    @property
    def both_ran(self) -> bool:
        return self.old_run.status == "ran" and self.new_run.status == "ran"

    @property
    def auto_promotable(self) -> bool:
        """Zero differences: the change may be promoted without SME review."""
        return self.both_ran and not self.config_diff.changed and self.data_identical

    def to_dict(self) -> dict:
        return {
            "old_config": self.old_config,
            "new_config": self.new_config,
            "custodian": self.custodian,
            "started_at": self.started_at,
            "seconds": round(self.seconds, 1),
            "business_days": self.business_days,
            "sample_files": self.sample_files,
            "auto_promotable": self.auto_promotable,
            "config_diff": self.config_diff.to_dict(),
            "old_run": self.old_run.to_dict(),
            "new_run": self.new_run.to_dict(),
            "rows_compared": self.rows_compared,
            "rows_added": self.rows_added,
            "rows_removed": self.rows_removed,
            "rows_changed": self.rows_changed,
            "groups": {k: v.to_dict() for k, v in self.groups.items()},
            "exception_delta": self.exception_delta,
            "test_delta": self.test_delta,
        }


def business_days_from_golden(golden_dir: Path, custodian: str, days: int) -> list[dict]:
    """The N most recent business days already captured for the custodian, latest version of each date."""
    index = load_index(golden_dir, custodian)
    latest_by_date: dict[str, dict] = {}
    for entry in index["datasets"]:
        latest_by_date[entry["business_date"]] = entry  # datasets are appended in (date, version) order; the last write per date is its latest version
    dates = sorted(latest_by_date)
    if not dates:
        raise ReplayError(f"no golden datasets captured for {custodian}; run astra-verify golden capture first (golden/{custodian}/datasets.json is empty or missing)")
    chosen = dates[-days:] if days else dates
    return [latest_by_date[d] for d in chosen]


def old_config_from_git(new_config: Path, ref: str, repo: Path, work_dir: Path) -> Path:
    """The config as it read at `ref`, written to a work file. Raises ReplayError if git cannot produce it."""
    relative = Path(new_config).resolve().relative_to(Path(repo).resolve()).as_posix()
    result = subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        raise ReplayError(f"git show {ref}:{relative} failed: {result.stderr.strip() or 'no such path at that ref'}")
    path = Path(work_dir) / f"old-{ref.replace('/', '_')}-{Path(new_config).name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.stdout, encoding="utf-8")
    return path


def replay(
    old_config: Path,
    new_config: Path,
    custodian: str,
    environment: str,
    executor: Executor,
    *,
    repo: Path,
    golden_dir: Path,
    days: int = MIN_DAYS,
    prefix: str = "ASTRA",
    work_dir: Path,
    out: Path,
    extra_bundles: list[Path] = (),
    cdm_ddl: Path | None = None,
    task_id: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
) -> ReplayReport:
    """Run the old and the new config against the same N days of already-captured history, and diff the two runs."""
    started = monotonic()
    started_at = clock()
    capture_path = Path(golden_dir) / custodian / CAPTURE_FILE
    capture, problems = load_capture(capture_path, repo)
    if capture is None:
        if not capture_path.is_file():
            raise ReplayError(f"no golden datasets captured for {custodian}; run astra-verify golden capture first (golden/{custodian}/capture.yaml does not exist)")
        raise ReplayError("; ".join(p.format() for p in problems))

    chosen = business_days_from_golden(Path(golden_dir), custodian, days)
    files = file_source(capture.files_location)
    file_dir = Path(work_dir) / "files"
    sample_paths: list[Path] = []
    seen: set[str] = set()
    for entry in chosen:
        for name in entry["source_files"]:
            if name not in seen:
                seen.add(name)
                sample_paths.append(files.path(name, file_dir))

    compiled_old, _ = compile_and_render(Path(old_config), repo, Path(work_dir) / "compiled" / "old")
    compiled_new, _ = compile_and_render(Path(new_config), repo, Path(work_dir) / "compiled" / "new")
    diff = config_diff(compiled_old, compiled_new)

    task = task_id or f"replay-{Path(new_config).stem}-{started_at.strftime('%Y%m%d%H%M%S')}"
    old_report, old_snapshot = replay_config(
        Path(old_config), sample_paths, SandboxSpec(f"{task}-old", environment, prefix=prefix), executor, repo=repo, out=Path(out) / "old", extra_bundles=extra_bundles, cdm_ddl=cdm_ddl, clock=clock, monotonic=monotonic
    )
    new_report, new_snapshot = replay_config(
        Path(new_config), sample_paths, SandboxSpec(f"{task}-new", environment, prefix=prefix), executor, repo=repo, out=Path(out) / "new", extra_bundles=extra_bundles, cdm_ddl=cdm_ddl, clock=clock, monotonic=monotonic
    )

    both_ran = old_report.status == "ran" and new_report.status == "ran"
    row_diffs, groups = diff_rows(old_snapshot, new_snapshot, compiled_new) if both_ran else ([], {})
    rows_compared = len(set(old_snapshot.rows) | set(new_snapshot.rows)) if both_ran else 0
    report = ReplayReport(
        old_config=str(old_config),
        new_config=str(new_config),
        custodian=custodian,
        started_at=started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        business_days=[e["business_date"] for e in chosen],
        sample_files=[p.name for p in sample_paths],
        config_diff=diff,
        old_run=old_report,
        new_run=new_report,
        rows_compared=rows_compared,
        row_diffs=row_diffs,
        groups=groups,
        exception_delta=exception_delta(old_report, new_report) if both_ran else [],
        test_delta=test_delta(old_report, new_report) if both_ran else [],
    )
    report.seconds = monotonic() - started
    write_report(report, Path(out))
    return report


# -- the report ----------------------------------------------------------------


def render_markdown(report: ReplayReport) -> str:
    out = [f"# Replay: {report.custodian}", ""]
    out.append(f"Old `{report.old_config}` vs new `{report.new_config}`, {len(report.business_days)} business day(s) ({report.business_days[0] if report.business_days else '?'} to {report.business_days[-1] if report.business_days else '?'}), {len(report.sample_files)} file(s). {report.seconds:.1f} s.")
    out.append("")
    out.append(f"**{'Eligible for promotion without SME review: no differences.' if report.auto_promotable else 'SME review needed.'}**")
    out.append("")
    out.append(f"Old run: {report.old_run.status}, {report.old_run.canonical_rows if report.old_run.canonical_rows is not None else 'n/a'} canonical rows. New run: {report.new_run.status}, {report.new_run.canonical_rows if report.new_run.canonical_rows is not None else 'n/a'} canonical rows.")
    out.append("")
    if report.old_run.error:
        out.append(f"**Old run stopped:** {report.old_run.error}")
        out.append("")
    if report.new_run.error:
        out.append(f"**New run stopped:** {report.new_run.error}")
        out.append("")

    out.append("## Config differences")
    out.append("")
    if report.config_diff.fields:
        out.append("| Group | Kind | Column | Description |")
        out.append("|---|---|---|---|")
        for f in report.config_diff.fields:
            out.append(f"| `{f.group}` | {f.kind} | {f.column or ''} | {f.description} |")
    else:
        out.append("No mapping, rule, DQ rule or resolution difference.")
    if report.config_diff.other:
        out.append("")
        out.append("Other config differences: " + "; ".join(report.config_diff.other) + ".")
    out.append("")

    out.append("## Data differences")
    out.append("")
    if not report.both_ran:
        out.append("Not measured: one of the two runs did not complete.")
    elif not report.row_diffs:
        out.append(f"{report.rows_compared} canonical row(s) compared across the {len(report.business_days)} business day(s) replayed; none differ.")
    else:
        out.append(f"{report.rows_compared} canonical row(s) compared.")
        out.append(f"{report.rows_added} row(s) added, {report.rows_removed} removed, {report.rows_changed} changed.")
        out.append("")
        out.append("Grouped by the rule or field responsible:")
        out.append("")
        out.append("| Group | Rows affected | Sample |")
        out.append("|---|---|---|")
        for name, group in sorted(report.groups.items(), key=lambda kv: -len(kv[1].keys)):
            sample = group.samples[0] if group.samples else None
            sample_text = f"key {sample['key']}, `{sample['column']}`: {sample['before']!r} -> {sample['after']!r}" if sample else ""
            out.append(f"| `{name}` | {len(group.keys)} | {sample_text} |")
    out.append("")

    out.append("## Exceptions")
    out.append("")
    if report.exception_delta:
        out.append("| Code | Level | Stage | Old rows | New rows | Delta |")
        out.append("|---|---|---|---|---|---|")
        for e in report.exception_delta:
            out.append(f"| `{e['code']}` | {e['level']} | {e['stage']} | {e['old_rows']} | {e['new_rows']} | {e['delta']:+d} |")
    else:
        out.append("No change in rejection counts." if report.both_ran else "Not measured.")
    out.append("")

    out.append("## DQ tests")
    out.append("")
    if report.test_delta:
        out.append("| Test | Old | New |")
        out.append("|---|---|---|")
        for t in report.test_delta:
            out.append(f"| `{t['test']}` | {'pass' if t['old_passed'] else 'FAIL'} | {'pass' if t['new_passed'] else 'FAIL'} |")
    else:
        out.append("No test changed pass/fail." if report.both_ran else "Not measured.")
    out.append("")
    return "\n".join(out)


def write_report(report: ReplayReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "replay.md"
    data = out / "replay.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
