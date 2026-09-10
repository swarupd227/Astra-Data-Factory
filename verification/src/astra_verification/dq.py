"""DQ runner: DMF results collected into a score per entity per business date (S4.2.3, ADR 0035).

A source's config already carries its dq_rules (astra_data.dq), and the
generation plane already renders one data metric function, or one system
function association, per rule (astra_data.render.dq); deployed, those run
inside Snowflake on their own schedule and record every measurement to
SNOWFLAKE.LOCAL.DATA_QUALITY_MONITORING_RESULTS. Every one of those
functions returns a number that is zero when the rule holds and positive
when it does not (ADR 0025) — the same convention whether the rule is a
system NULL_COUNT, a custom row-failure count, or a control total's gap.
This module does not re-run any check; it reads what the DMFs already
measured for one business date, and folds it into one number per entity:

  entity   the record a rule measures — the physical detail record for a
           record-level rule, the Silver logical record for a pair- or
           business-level rule (astra_data.dq.CompiledDqRule.record)
  score    the severity-weighted share of that day's measurements that
           held: an error-severity rule failing costs the score four
           times what an info-severity rule failing costs, so one broken
           business rule can breach the target the way a scattering of
           informational nits cannot
  target   DQ_SCORE_TARGET by default; a client engagement's own number
           overrides it per run, never invented here (the same discipline
           the DQ Generator agent already applies to its rules' own
           thresholds)

A rule with no measurement for the date (its DMF has not triggered, most
often because nothing changed) does not count against the score; an
entity with no measured rule at all is reported as unscored, not zero.
An entity whose score is below target raises one alert per business date
into CONTROL.ALERTS — the table ADR 0007's detectors already write and
DISPATCH_ALERTS already delivers, so a DQ breach reaches the same Slack,
Jira and email channels as a failed task or a late custodian without any
new delivery path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from astra_core.problems import Problem

from astra_data.bundle import Executor, Target
from astra_data.compiler import CompileError, CompiledConfig, compile_config
from astra_data.dq import CompiledDqRule
from astra_data.render.dq import dmf_name
from astra_data.render.names import file_metadata_table, record_table, silver_table

DQ_SCORE_TARGET = 0.98  # platform default entity score target; a source overrides it with --target

SEVERITY_WEIGHT = {"info": 1, "warning": 2, "error": 4}
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


class DqError(RuntimeError):
    """The config could not be compiled. Carries the problems found."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.format() for p in problems))


def compile_for_dq(config: Path, repo: Path) -> CompiledConfig:
    """The config compiled exactly as the deploy pipeline would, for its dq_rules alone; nothing is rendered or written."""
    from astra_knowledge.cdm import load_packs
    from astra_knowledge.registry import Registry
    from astra_knowledge.rules import Catalog

    registry, problems = Registry.load(repo / "specs", repo)
    if problems:
        raise DqError(problems)
    catalog, problems = Catalog.load(repo / "rules", repo, registry)
    if problems:
        raise DqError(problems)
    packs, problems = load_packs(repo / "domains", repo)
    if problems:
        raise DqError(problems)
    try:
        return compile_config(config, registry=registry, catalog=catalog, packs=packs, root=repo)
    except CompileError as exc:
        raise DqError(exc.problems) from exc


# -- scoring, pure over collected values ---------------------------------------


@dataclass(frozen=True)
class RuleScore:
    rule_id: str
    kind: str
    level: str
    severity: str
    check: str
    measurements: int = 0
    failing: int = 0  # measurements with a non-zero value

    @property
    def pass_rate(self) -> float | None:
        return None if self.measurements == 0 else (self.measurements - self.failing) / self.measurements

    @property
    def holds(self) -> bool | None:
        return None if self.measurements == 0 else self.failing == 0

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind,
            "level": self.level,
            "severity": self.severity,
            "check": self.check,
            "measurements": self.measurements,
            "failing": self.failing,
            "pass_rate": None if self.pass_rate is None else round(self.pass_rate, 6),
            "holds": self.holds,
        }


@dataclass
class EntityScore:
    entity: str
    custodian: str
    business_date: str
    target: float
    rules: tuple[RuleScore, ...] = field(default_factory=tuple)

    @property
    def scored_rules(self) -> tuple[RuleScore, ...]:
        return tuple(r for r in self.rules if r.measurements > 0)

    @property
    def scored(self) -> bool:
        return bool(self.scored_rules)

    @property
    def score(self) -> float | None:
        rules = self.scored_rules
        if not rules:
            return None
        weight = sum(SEVERITY_WEIGHT[r.severity] for r in rules)
        return sum(SEVERITY_WEIGHT[r.severity] * r.pass_rate for r in rules) / weight

    @property
    def meets_target(self) -> bool:
        return self.scored and self.score >= self.target

    @property
    def breached_rules(self) -> tuple[RuleScore, ...]:
        return tuple(r for r in self.scored_rules if not r.holds)

    @property
    def worst_breached_severity(self) -> str | None:
        breached = self.breached_rules
        if not breached:
            return None
        return max((r.severity for r in breached), key=lambda s: SEVERITY_RANK[s])

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "custodian": self.custodian,
            "business_date": self.business_date,
            "target": self.target,
            "scored": self.scored,
            "score": None if self.score is None else round(self.score, 6),
            "meets_target": self.meets_target,
            "rules": [r.to_dict() for r in self.rules],
            "breached": [r.rule_id for r in self.breached_rules],
        }


# -- collecting DMF results ------------------------------------------------------


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_identity(compiled: CompiledConfig, rule: CompiledDqRule, database: str) -> tuple[str, str, str]:
    """Database, schema and table the rule's DMF is attached to, resolved against a real database (not the bundle's {{ DATABASE }} template)."""
    if rule.table == "metadata":
        return database, "BRONZE", file_metadata_table(compiled)
    if rule.table == "record":
        return database, "BRONZE", record_table(compiled, rule.record)
    return database, "SILVER", silver_table(compiled)


def _metric_identity(compiled: CompiledConfig, rule: CompiledDqRule, database: str) -> tuple[str, str, str, list[str]]:
    """Database, schema, name and argument columns of the rule's DMF: a system function, or the custom one rendered for it."""
    columns = [c.name for c in rule.columns]
    if rule.system_function:
        return "SNOWFLAKE", "CORE", rule.system_function, columns
    return database, "CONTROL", dmf_name(compiled, rule), columns


def measurement_query(compiled: CompiledConfig, rule: CompiledDqRule, database: str, business_date: date) -> str:
    """Every value the rule's DMF recorded for the business date; zero rows when it has not triggered that day."""
    table_db, table_schema, table = _table_identity(compiled, rule, database)
    metric_db, metric_schema, metric, columns = _metric_identity(compiled, rule, database)
    return (
        "SELECT VALUE::NUMBER(38,6)\n"
        "FROM SNOWFLAKE.LOCAL.DATA_QUALITY_MONITORING_RESULTS\n"
        f"WHERE TABLE_DATABASE = {_lit(table_db)} AND TABLE_SCHEMA = {_lit(table_schema)} AND TABLE_NAME = {_lit(table)}\n"
        f"  AND METRIC_DATABASE = {_lit(metric_db)} AND METRIC_SCHEMA = {_lit(metric_schema)} AND METRIC_NAME = {_lit(metric)}\n"
        f"  AND ARRAY_TO_STRING(ARGUMENT_NAMES, ',') = {_lit(','.join(columns))}\n"
        f"  AND MEASUREMENT_TIME::DATE = {_lit(business_date.isoformat())}\n"
        "ORDER BY MEASUREMENT_TIME"
    )


def collect(executor: Executor, compiled: CompiledConfig, rule: CompiledDqRule, database: str, business_date: date) -> list[float]:
    rows = executor.query(measurement_query(compiled, rule, database, business_date))
    return [float(row[0]) for row in rows]


def run(compiled: CompiledConfig, executor: Executor, database: str, business_date: date, *, score_target: float = DQ_SCORE_TARGET) -> list[EntityScore]:
    """Collect every dq_rule's DMF results for the business date and fold them into a score per entity, oldest-declared rule first."""
    by_entity: dict[str, list[RuleScore]] = {}
    for rule in compiled.dq_rules:
        values = collect(executor, compiled, rule, database, business_date)
        failing = sum(1 for v in values if v != 0)
        by_entity.setdefault(rule.record, []).append(RuleScore(rule.id, rule.kind, rule.level, rule.severity, rule.check, len(values), failing))
    custodian = compiled.source["custodian"]
    return [EntityScore(entity, custodian, business_date.isoformat(), score_target, tuple(rules)) for entity, rules in sorted(by_entity.items())]


# -- alerting: a breach reuses ADR 0007's alert table and dispatcher ------------


def alert_statements(scores: Iterable[EntityScore], target: Target) -> list[str]:
    """One idempotent INSERT per entity below target, deduplicated the same way every other detector dedupes (ADR 0007): by SOURCE_KEY."""
    alerts = f'"{target.environment_database}"."CONTROL"."ALERTS"'
    statements = []
    for s in scores:
        if not s.scored or s.meets_target:
            continue
        severity = s.worst_breached_severity
        detail = "; ".join(f"{r.rule_id} ({r.severity}, {r.kind}) failed {r.failing}/{r.measurements} measurement(s): {r.check}" for r in s.breached_rules)
        title = f"{s.custodian} {s.entity}: DQ score {s.score:.1%} is below target {s.target:.1%}"
        body = (
            f"Entity '{s.entity}' of {s.custodian} scored {s.score:.1%} against a target of {s.target:.1%} for {s.business_date}, "
            f"from DMF results collected that day. Rules breached: {detail}."
        )
        key = f"dq:{s.custodian}:{s.entity}:{s.business_date}"
        statements.append(
            f"INSERT INTO {alerts} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE) "
            f"SELECT UUID_STRING(), SYSDATE(), 'dq_score_breach', {_lit(severity)}, {_lit(s.custodian)}, {_lit(title)}, {_lit(body)}, {_lit(key)}, {_lit(s.business_date)}::DATE "
            f"WHERE NOT EXISTS (SELECT 1 FROM {alerts} WHERE SOURCE_KEY = {_lit(key)})"
        )
    return statements


def raise_alerts(scores: Iterable[EntityScore], executor: Executor, target: Target) -> list[str]:
    statements = alert_statements(scores, target)
    if statements:
        executor.execute_script(";\n".join(statements) + ";")
    return statements


# -- the report ------------------------------------------------------------------


def render_markdown(scores: list[EntityScore]) -> str:
    if not scores:
        return "# DQ score\n\nNo dq_rules to score.\n"
    out = [f"# DQ score: {scores[0].custodian} {scores[0].business_date}", ""]
    out.append("| Entity | Score | Target | Rules scored | Status |")
    out.append("|---|---|---|---|---|")
    for s in scores:
        score_text = f"{s.score:.1%}" if s.scored else "no data"
        status = "no data" if not s.scored else ("meets target" if s.meets_target else "below target")
        out.append(f"| `{s.entity}` | {score_text} | {s.target:.1%} | {len(s.scored_rules)}/{len(s.rules)} | {status} |")
    out.append("")
    for s in scores:
        out.append(f"## {s.entity}")
        out.append("")
        out.append("| Rule | Kind | Level | Severity | Measurements | Failing | Holds |")
        out.append("|---|---|---|---|---|---|---|")
        for r in s.rules:
            holds = "-" if r.holds is None else ("yes" if r.holds else "no")
            out.append(f"| `{r.rule_id}` | {r.kind} | {r.level} | {r.severity} | {r.measurements} | {r.failing} | {holds} |")
        out.append("")
        if s.breached_rules:
            out.append("Breached: " + "; ".join(f"`{r.rule_id}` ({r.severity}) failed {r.failing}/{r.measurements}: {r.check}" for r in s.breached_rules))
            out.append("")
    return "\n".join(out)


def write_report(scores: list[EntityScore], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "dq.md"
    data = out / "dq.json"
    markdown.write_text(render_markdown(scores), encoding="utf-8", newline="\n")
    data.write_text(json.dumps([s.to_dict() for s in scores], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return markdown, data


def run_and_alert(
    compiled: CompiledConfig,
    executor: Executor,
    database: str,
    target: Target,
    business_date: date,
    *,
    score_target: float = DQ_SCORE_TARGET,
    alert: bool = True,
    out: Path | None = None,
) -> tuple[list[EntityScore], list[str]]:
    """Score every entity for the business date, write the working report, and raise an alert for every entity below target."""
    scores = run(compiled, executor, database, business_date, score_target=score_target)
    if out is not None:
        write_report(scores, out)
    alerts = raise_alerts(scores, executor, target) if alert else []
    return scores, alerts
