"""Dual-run and parity viewer: match rate by day, break groups by rule and field, and drill-down
to a record pair with its differing fields highlighted, so gate evidence can be inspected, not
just read (S6.3.7, ADR 0063, product spec Section 6: "Parity / Break Explainer... Grouped
explanations: rule, field, source").

Reads two files this repository already produces, recomputing neither:

  trend (AC1)          `astra_verification.parity_report.ParityReport`'s own `by_cycle` — one
                        entry per business date, each already carrying that day's match rate
                        (S4.2.2). `astra_control.custodian_page` already reads this same report's
                        four summary keys (S6.3.3); this reads `by_cycle` too, the per-day series
                        AC1's trend chart needs, and drops nothing this module does not use.
  break groups (AC2)    `astra_agents.break_explainer`'s own `report.json` `explanations` list,
                        grouped by (rule id, field, cause) and counted.
  record pairs (AC3)    the same `explanations` list, grouped instead by `key` — one row per
                        record, its own differing fields (legacy value, lakehouse value, cause,
                        rule) together: "a record pair... with differing fields highlighted."

This module never imports `astra_agents` or recomputes a parity comparison — the same "no new
agent import, read the report file" boundary `astra_control.queue` and `astra_control.
agent_review` already established, extended here to the Verification plane's own parity report.

**No real multi-day parity report is committed anywhere in this repository.** `golden/` holds only
the parity mapping config (`golden/pershing/parity.yaml` — the real keys and field tolerances this
module's own tests build fixtures from), never a captured dataset or a run report; producing one
needs `astra-verify golden capture` against a real golden bucket, which no environment here has —
the same honest gap `golden/README.md` and `agents/break_explainer/eval.yaml` both already name.
This module's own examples build a real `ParityReport` with `astra_verification.parity.
compare_rows` and `astra_verification.parity_report.aggregate` from the real mapping, rather than
hand-writing multi-day numbers with no real comparison behind them; the break-report example
reuses the real, already-committed `control/examples/queue/break-explainer/pershing/2026-09-01/
report.json` as-is.

Every action here is a read — this story adds no write action to `astra_control.permissions`. Its
own actor, "steward or QE engineer," names a role ("QE engineer") outside the six closed roles
this plane has (steward, bsa, engineer, ops, pm, auditor) — moot for this story specifically,
since every role already reads every read action uniformly; a future write here (if one is ever
added) would need to resolve that mapping, this one does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ParityViewerError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ParityViewerError(f"file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParityViewerError(f"{path}: {exc}") from exc


def _explanations(data: dict) -> tuple[dict, ...]:
    return tuple(data.get("explanations") or ())


# -- AC1: trend chart of match rate per custodian, per day --------------------------------------


@dataclass(frozen=True)
class TrendPoint:
    business_date: str
    match_rate: float
    meets_target: bool
    legacy_rows: int
    lakehouse_rows: int
    matched: int

    def to_dict(self) -> dict:
        return {
            "business_date": self.business_date,
            "match_rate": self.match_rate,
            "meets_target": self.meets_target,
            "legacy_rows": self.legacy_rows,
            "lakehouse_rows": self.lakehouse_rows,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class Trend:
    custodian: str
    trend: str  # improving, declining, flat, n/a: one cycle
    match_rate: float  # the window's own overall rate
    meets_target: bool
    points: tuple[TrendPoint, ...]  # oldest to newest

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "trend": self.trend,
            "match_rate": self.match_rate,
            "meets_target": self.meets_target,
            "points": [p.to_dict() for p in self.points],
        }


def load_trend(path: Path) -> Trend:
    data = _read_json(path)
    if "by_cycle" not in data:
        raise ParityViewerError(f"{path}: not a parity report (astra_verification.parity_report.ParityReport) — no 'by_cycle'")
    points = tuple(
        TrendPoint(
            business_date=c["business_date"],
            match_rate=c["match_rate"],
            meets_target=c["meets_target"],
            legacy_rows=c["legacy_rows"],
            lakehouse_rows=c["lakehouse_rows"],
            matched=c["matched"],
        )
        for c in data["by_cycle"]
    )
    return Trend(
        custodian=data.get("custodian", ""),
        trend=data.get("trend", "n/a: one cycle"),
        match_rate=data.get("match_rate", 0.0),
        meets_target=data.get("meets_target", False),
        points=points,
    )


# -- AC2: break groups by rule and field, with counts --------------------------------------------


@dataclass(frozen=True)
class BreakGroup:
    rule_id: str | None
    field: str
    cause: str
    count: int

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "field": self.field, "cause": self.cause, "count": self.count}


def break_groups(path: Path) -> tuple[BreakGroup, ...]:
    """Every (rule, field, cause) an explanation touches, counted — busiest group first."""
    data = _read_json(path)
    counts: dict[tuple[str | None, str, str], int] = {}
    for e in _explanations(data):
        key = (e.get("rule_id"), e["field"], e["cause"])
        counts[key] = counts.get(key, 0) + 1
    groups = tuple(BreakGroup(rule_id, field, cause, count) for (rule_id, field, cause), count in counts.items())
    return tuple(sorted(groups, key=lambda g: (-g.count, g.field, g.cause)))


# -- AC3: a record pair, legacy vs lakehouse, differing fields highlighted -----------------------


@dataclass(frozen=True)
class FieldDiff:
    field: str
    legacy: str | None
    lakehouse: str | None
    cause: str
    rule_id: str | None
    description: str

    def to_dict(self) -> dict:
        return {"field": self.field, "legacy": self.legacy, "lakehouse": self.lakehouse, "cause": self.cause, "rule_id": self.rule_id, "description": self.description}


@dataclass(frozen=True)
class RecordPair:
    key: tuple[str, ...]
    fields: tuple[FieldDiff, ...]

    def to_dict(self) -> dict:
        return {"key": list(self.key), "fields": [f.to_dict() for f in self.fields]}


def record_pairs(path: Path) -> tuple[RecordPair, ...]:
    """Every differing record, its own explanations grouped by key — first-seen order."""
    data = _read_json(path)
    by_key: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for e in _explanations(data):
        key = tuple(e["key"])
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(e)
    return tuple(
        RecordPair(key, tuple(FieldDiff(e["field"], e.get("legacy"), e.get("lakehouse"), e["cause"], e.get("rule_id"), e.get("description", "")) for e in by_key[key]))
        for key in order
    )


def record_pair(path: Path, key: tuple[str, ...]) -> RecordPair:
    """One record pair by its own key — the drill-down AC3 names."""
    key = tuple(key)
    pair = next((p for p in record_pairs(path) if p.key == key), None)
    if pair is None:
        raise ParityViewerError(f"no record with key {list(key)} in {path}")
    return pair


# -- the view --------------------------------------------------------------------------


def render_trend_markdown(trend: Trend) -> str:
    out = [f"# Parity trend: {trend.custodian}", ""]
    out.append(f"**Overall match rate: {trend.match_rate:.4%}** — {'meets target' if trend.meets_target else 'below target'}. Trend: {trend.trend}.")
    out.append("")
    if not trend.points:
        out += ["No cycles in this report.", ""]
        return "\n".join(out)
    out.append("| Business date | Match rate | Meets target | Legacy rows | Lakehouse rows | Matched |")
    out.append("|---|---|---|---|---|---|")
    for p in trend.points:
        out.append(f"| {p.business_date} | {p.match_rate:.4%} | {'yes' if p.meets_target else 'no'} | {p.legacy_rows} | {p.lakehouse_rows} | {p.matched} |")
    out.append("")
    return "\n".join(out)


def render_break_groups_markdown(groups: tuple[BreakGroup, ...]) -> str:
    out = ["# Break groups", ""]
    if not groups:
        out += ["No differences.", ""]
        return "\n".join(out)
    out.append("| Rule | Field | Cause | Count |")
    out.append("|---|---|---|---|")
    for g in groups:
        out.append(f"| {g.rule_id or '(no rule)'} | `{g.field}` | {g.cause} | {g.count} |")
    out.append("")
    return "\n".join(out)


def render_record_pairs_markdown(pairs: tuple[RecordPair, ...]) -> str:
    out = ["# Record pairs", ""]
    if not pairs:
        out += ["No differing records.", ""]
        return "\n".join(out)
    for p in pairs:
        out.append(f"## {list(p.key)}")
        out.append("")
        out.append("| Field | Legacy | Lakehouse | Cause | Rule |")
        out.append("|---|---|---|---|---|")
        for f in p.fields:
            out.append(f"| **`{f.field}`** | {f.legacy} | {f.lakehouse} | {f.cause} | {f.rule_id or '-'} |")
        out.append("")
    return "\n".join(out)


def render_record_pair_markdown(pair: RecordPair) -> str:
    out = [f"# Record: {list(pair.key)}", ""]
    out.append("| Field | Legacy | Lakehouse | Cause | Rule | Description |")
    out.append("|---|---|---|---|---|---|")
    for f in pair.fields:
        out.append(f"| **`{f.field}`** | {f.legacy} | {f.lakehouse} | {f.cause} | {f.rule_id or '-'} | {f.description} |")
    out.append("")
    return "\n".join(out)
