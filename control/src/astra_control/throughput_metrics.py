"""Throughput and cost metrics: custodians live per week, agent acceptance and cost per custodian
per day, assembled into one weekly report for the client's own reporting cadence (S6.2.3, ADR
0072, product spec's own KPI table: "Agent acceptance rate... Cost per source per day... visible
weekly").

"Custodians live per week" is `astra_control.board.live_per_week`, called directly — already
built (S6.1.1), nothing to recompute.

**"Agent acceptance" is a real event ratio, not `astra_verification.agent_eval`'s own precision/
recall — a genuine naming collision in the product spec's own API sketch** (`GET /metrics/agents`
names both "acceptance" and "precision/recall by agent and tier" in one line, as if they were one
thing). The KPI table's own definition is "share of agent outputs approved without material edit"
— a real-world human review ratio. `agent_eval` measures a *different* thing: correctness against
a curated gold set, one report per agent build/tuning cycle, with no custodian or day dimension at
all. This module computes acceptance from the one real, already-built source that has an actual
per-custodian, per-day, confirmed-vs-not review outcome: `astra_control.audit_log.
rule_status_changes_from`'s own `AuditRecord`s, counting only the three real steward decisions
(`astra_control.rule_review.REVIEW_STATUSES`: confirmed, rejected, legacy_defect) — never a rule's
own initial `recovered` status, which is not a review decision at all. This is a real, narrow,
honestly-scoped definition — not a blend across every approval-shaped flow this plane has built,
several of which (drift approvals, guardrail changes, gate approvals, board moves) do not
represent an agent draft being accepted or rejected at all. `astra_verification.agent_eval`'s own
weekly precision/recall report, when a caller already has one, is surfaced here too, in its own
clearly separate section — real, already-built data, not recomputed, never conflated with
"acceptance."

**"Cost per custodian... from query tags" has no real source anywhere in this codebase.** Checked
exhaustively: no Terraform resource, no rendered SQL, sets a query tag anywhere; the product
spec's own FinOps agent ("query tags, warehouse metrics") is explicitly not part of this
repository's Agents-plane backlog. `astra_control.custodian_page`'s own `cost` field already
established the honest pattern this module follows exactly: caller-supplied only, `None` shown as
"no data" rather than a fabricated number — never a second, invented query-tag mechanism.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astra_control.audit_log import AuditRecord
from astra_control.board import Board, live_per_week

ACCEPTED_STATUS = "confirmed"
REJECTED_STATUSES = ("rejected", "legacy_defect")
COUNTED_STATUSES = (ACCEPTED_STATUS,) + REJECTED_STATUSES  # astra_control.rule_review.REVIEW_STATUSES, duplicated: never "recovered", not a review decision


def _business_date(at: str) -> str:
    return at.split("T", 1)[0]


# -- agent acceptance: a real event ratio, per custodian per day ---------------------------------


@dataclass(frozen=True)
class CustodianDayAcceptance:
    custodian: str
    business_date: str
    accepted: int
    rejected: int

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    @property
    def acceptance_rate(self) -> float | None:
        return None if self.total == 0 else self.accepted / self.total

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "business_date": self.business_date,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "acceptance_rate": round(self.acceptance_rate, 4) if self.acceptance_rate is not None else None,
        }


def acceptance_by_custodian_day(records: tuple[AuditRecord, ...]) -> tuple[CustodianDayAcceptance, ...]:
    """Every rule-status-change review decision, grouped by (custodian, business date) — the
    module docstring's own scope: confirmed vs rejected/legacy_defect only, never "recovered", and
    only records with a real custodian attributed (a rule with no `applies_to.custodians` is
    skipped — nothing to attribute the decision to)."""
    counts: dict[tuple[str, str], list[int]] = {}
    for r in records:
        if r.kind != "rule_status_change" or not r.custodian:
            continue
        status = r.subject.rsplit(" -> ", 1)[-1]
        if status not in COUNTED_STATUSES:
            continue
        key = (r.custodian, _business_date(r.at))
        bucket = counts.setdefault(key, [0, 0])
        if status == ACCEPTED_STATUS:
            bucket[0] += 1
        else:
            bucket[1] += 1
    return tuple(
        CustodianDayAcceptance(custodian, business_date, accepted, rejected)
        for (custodian, business_date), (accepted, rejected) in sorted(counts.items())
    )


# -- cost per custodian per day: caller-supplied only, honestly "no data" otherwise ---------------


@dataclass(frozen=True)
class CustodianDayCost:
    custodian: str
    business_date: str
    cost: float | None

    def to_dict(self) -> dict:
        return {"custodian": self.custodian, "business_date": self.business_date, "cost": self.cost}


# -- the weekly report: everything assembled, nothing recomputed ---------------------------------


@dataclass(frozen=True)
class ThroughputReport:
    generated_at: str
    custodians_live_per_week: dict[str, int]
    acceptance: tuple[CustodianDayAcceptance, ...]
    costs: tuple[CustodianDayCost, ...] = ()
    agent_eval_weekly: dict[str, Any] | None = None  # astra_verification.agent_eval.WeeklyReport.to_dict(), when a caller already has one -- a different metric, its own section (module docstring)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "custodians_live_per_week": dict(self.custodians_live_per_week),
            "acceptance": [a.to_dict() for a in self.acceptance],
            "costs": [c.to_dict() for c in self.costs],
            "agent_eval_weekly": self.agent_eval_weekly,
        }


def build_report(
    board: Board,
    audit_records: tuple[AuditRecord, ...],
    *,
    costs: dict[tuple[str, str], float] | None = None,
    agent_eval_weekly: dict[str, Any] | None = None,
    clock=lambda: datetime.now(timezone.utc),
) -> ThroughputReport:
    costs = costs or {}
    cost_rows = tuple(CustodianDayCost(custodian, business_date, cost) for (custodian, business_date), cost in sorted(costs.items()))
    return ThroughputReport(
        generated_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        custodians_live_per_week=live_per_week(board),
        acceptance=acceptance_by_custodian_day(audit_records),
        costs=cost_rows,
        agent_eval_weekly=agent_eval_weekly,
    )


# -- AC1: exported for the client cadence ----------------------------------------------------


def render_markdown(report: ThroughputReport) -> str:
    out = [f"# Throughput and cost — {report.generated_at}", ""]

    out.append("## Custodians live per week")
    out.append("")
    if report.custodians_live_per_week:
        out.append("| Week | Custodians live |")
        out.append("|---|---|")
        for week in sorted(report.custodians_live_per_week):
            out.append(f"| {week} | {report.custodians_live_per_week[week]} |")
    else:
        out.append("No custodian has reached cutover yet.")
    out.append("")

    out.append("## Agent acceptance, by custodian and day")
    out.append("")
    out.append('The real confirmed-vs-rejected ratio from the rule catalog\'s own review history — not `agent_eval`\'s own precision/recall (a different metric; see below when given).')
    out.append("")
    if report.acceptance:
        out.append("| Custodian | Business date | Accepted | Rejected | Acceptance rate |")
        out.append("|---|---|---|---|---|")
        for a in report.acceptance:
            rate = f"{a.acceptance_rate:.1%}" if a.acceptance_rate is not None else "n/a"
            out.append(f"| {a.custodian} | {a.business_date} | {a.accepted} | {a.rejected} | {rate} |")
    else:
        out.append("No review decisions recorded.")
    out.append("")

    out.append("## Cost per custodian per day")
    out.append("")
    if report.costs:
        out.append("| Custodian | Business date | Cost |")
        out.append("|---|---|---|")
        for c in report.costs:
            out.append(f"| {c.custodian} | {c.business_date} | {f'${c.cost:.2f}' if c.cost is not None else 'no data'} |")
    else:
        out.append("No cost data given — no query-tag mechanism exists anywhere in this repository yet; cost is caller-supplied only.")
    out.append("")

    if report.agent_eval_weekly is not None:
        out.append("## Agent evaluation (a different metric: precision/recall against each agent's own gold set)")
        out.append("")
        out.append(f"Agents scored: {', '.join(report.agent_eval_weekly.get('agents_scored') or []) or 'none'}. All passed: {report.agent_eval_weekly.get('all_passed')}.")
        out.append("")

    return "\n".join(out)


def write_report(report: ThroughputReport, out: Path) -> tuple[Path, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "weekly.md"
    data = out / "weekly.json"
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data
