"""Run status dashboard: per custodian, file and run — expected, arrived, parsed, resolved,
published, timed against the 20-minute window, so late or slow custodians are seen at a glance
(S6.3.8, ADR 0064, product spec's own repeated requirement that post-arrival work "fits the
20-minute window," `docs/backlog-v0.2.md` S3.2.6, S4.x, S7.2.4).

"Expected" is the config's own `delivery.files` and `delivery.cutoff_time` — the exact same block
`astra_control.custodian_page` already reads (S6.3.3), reused the same way here. Every other
stage — arrived, parsed, resolved, published — has **no live source this module can query**:

  arrived        `CONTROL.FILE_LOAD_LOG.OBSERVED_AT` (infra/terraform/foundation/landing.tf) is a
                 real Snowflake table, but no environment this repository runs in has a live
                 connection — the same "a real, working stand-in for a live query" gap
                 `astra_control.custodian_page`'s own `arrivals` argument already named for this
                 exact table (S6.3.3's own module docstring).
  parsed         `parse.sql` is a real named step (`astra_data.render.tasks.STEP_ORDER`), but no
                 timestamp column for it exists anywhere in this repository's schema.
  resolved       `resolve.sql` is likewise a real named step with no logged completion time.
  published      `CONTROL.GOLD_PUBLISH_LOG.PUBLISHED_AT` is a real column with a real start/end
                 pair, again unreachable without a live connection.

So every stage past "expected" is caller-supplied, in one small run file per custodian — the same
shape `astra_control.custodian_page`'s own `arrivals.yaml` already established, extended from one
timestamp (arrival) to four (arrived, parsed, resolved, published) plus the run's own business
date and, when one exists, a reference to its own run log.

**AC3's "click-through to the run log" has no real artifact to point to.** Nothing in this
repository — not `astra_data`, not `astra_verification`, not `control/` itself — produces or
stores anything shaped like a browsable run/execution/job log; `CONTROL.CUSTODIAN_RUNS` and
`CONTROL.GOLD_PUBLISH_LOG` are raw Snowflake tables with no CLI command or renderer over them
(confirmed by searching every CLI in this repository). This module reads a caller-given `run_log`
reference from the same run file — a real link if a platform (Airflow, a Snowflake worksheet)
already has one — and says so honestly when none is given, rather than inventing a screen.

**"20-minute window" is grounded in the backlog's own repeated requirement, never a schema
constant anywhere in this repository** (checked exhaustively) — `RUN_WINDOW_BUDGET_MINUTES` is
this module's own first place it becomes a real number, the same way `astra_verification.parity.
MATCH_RATE_TARGET` is the product spec's own north-star turned into a constant, not invented.
Elapsed time is measured from the **latest file's own arrival** to the **latest file's own
publish** — S4.x's own wording is "end-to-end ≤ 20 minutes after the last file"
(`docs/backlog-v0.2.md`), the same start point `CONTROL.CUSTODIAN_RUNS.LATEST_ARRIVAL_AT` already
uses.

**S7.2.3 "Status table and watermark" (F7.2, not yet built) names this exact same five-word
stage list** ("Expected vs arrived vs parsed vs resolved vs published shown," `docs/backlog-
v0.2.md`), but it is a different, later story about the Gold read path's own watermark — this
module is S6.3.8's own Control-plane dashboard, not S7.2.3's.

This story's actor, "operations user or SRE," names a role ("SRE") outside the six closed roles
this plane has (steward, bsa, engineer, ops, pm, auditor) — moot here as it was for S6.3.7's "QE
engineer," since every action this module adds is a read, available to every role uniformly; the
ops reconciler persona's own three grounded daily tasks (`docs/ux/personas.md`) do not include
run/lateness monitoring either, a second honest gap this module's own design does not paper over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from astra_core.yamlsource import SourceError, load
from astra_data.compiler import CompileError, CompiledConfig, compile_config
from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

RUN_WINDOW_BUDGET_MINUTES = 20  # module docstring: the backlog's own repeated requirement (S2.x, S3.2.6, S4.x, S7.2.4), never a schema constant elsewhere in this repository
STAGES = ("expected", "arrived", "parsed", "resolved", "published")  # the story's own five words


class RunStatusError(RuntimeError):
    pass


def _load_context(specs_dir: Path, rules_dir: Path, domains_dir: Path, root: Path | None):
    registry, problems = Registry.load(Path(specs_dir), repo_root=root)
    if problems:
        raise RunStatusError("; ".join(p.format() for p in problems))
    catalog, problems = Catalog.load(Path(rules_dir), root, registry)
    if problems:
        raise RunStatusError("; ".join(p.format() for p in problems))
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        raise RunStatusError("; ".join(p.format() for p in problems))
    return registry, catalog, packs


def _compile(path: Path, registry, catalog, packs, root: Path | None) -> CompiledConfig:
    path = Path(path)
    if not path.is_file():
        raise RunStatusError(f"config file not found: {path}")
    try:
        return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=root)
    except CompileError as exc:
        raise RunStatusError("; ".join(p.format() for p in exc.problems)) from exc


def _parse_at(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# -- the run input: a real, working stand-in for a live query this environment does not have ----


@dataclass(frozen=True)
class RunInput:
    business_date: str | None
    run_log: str | None
    stages: dict[str, dict[str, str]]  # pattern -> {arrived_at, parsed_at, resolved_at, published_at}


def load_run(path: Path | None) -> RunInput:
    if path is None:
        return RunInput(None, None, {})
    path = Path(path)
    if not path.is_file():
        return RunInput(None, None, {})
    try:
        data = load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SourceError) as exc:
        raise RunStatusError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunStatusError(f"{path}: expected a mapping")
    return RunInput(
        business_date=data.get("business_date"),
        run_log=data.get("run_log"),
        stages={str(pattern): dict(stages or {}) for pattern, stages in (data.get("files") or {}).items()},
    )


# -- expected, arrived, parsed, resolved, published, per file ------------------------------------


@dataclass(frozen=True)
class FileStage:
    pattern: str
    description: str
    arrived_at: str | None
    parsed_at: str | None
    resolved_at: str | None
    published_at: str | None

    @property
    def current_stage(self) -> str:
        for stage, at in (("published", self.published_at), ("resolved", self.resolved_at), ("parsed", self.parsed_at), ("arrived", self.arrived_at)):
            if at:
                return stage
        return "expected"

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "description": self.description,
            "current_stage": self.current_stage,
            "arrived_at": self.arrived_at,
            "parsed_at": self.parsed_at,
            "resolved_at": self.resolved_at,
            "published_at": self.published_at,
        }


@dataclass(frozen=True)
class CustodianRunStatus:
    custodian_id: str
    business_date: str | None
    cutoff_time: str | None
    timezone: str | None
    files: tuple[FileStage, ...]
    run_log: str | None

    @property
    def missing_files(self) -> tuple[FileStage, ...]:
        return tuple(f for f in self.files if f.arrived_at is None)

    @property
    def cutoff_at(self) -> datetime | None:
        """A real, timezone-aware cutoff instant, from the config's own cutoff_time/timezone and
        the run's own business_date -- None when any of the three is missing (no run given yet)."""
        if not (self.business_date and self.cutoff_time and self.timezone):
            return None
        hour, minute = (int(x) for x in self.cutoff_time.split(":"))
        return datetime.combine(date.fromisoformat(self.business_date), time(hour, minute), tzinfo=ZoneInfo(self.timezone))

    def is_late(self, as_of: datetime) -> bool:
        """AC1: past the cutoff, with at least one expected file still missing."""
        cutoff = self.cutoff_at
        if cutoff is None:
            return False
        return as_of > cutoff and bool(self.missing_files)

    @property
    def latest_arrival(self) -> datetime | None:
        times = [_parse_at(f.arrived_at) for f in self.files if f.arrived_at]
        return max(times) if times else None

    @property
    def latest_publish(self) -> datetime | None:
        times = [_parse_at(f.published_at) for f in self.files if f.published_at]
        return max(times) if times else None

    @property
    def elapsed_minutes(self) -> float | None:
        """AC2's own "timing bar": from the latest file's own arrival to the latest file's own
        publish -- S4.x's own "end-to-end... after the last file" (module docstring). None while
        the run has not reached both ends yet."""
        if self.latest_arrival is None or self.latest_publish is None:
            return None
        return (self.latest_publish - self.latest_arrival).total_seconds() / 60

    @property
    def within_budget(self) -> bool | None:
        elapsed = self.elapsed_minutes
        return None if elapsed is None else elapsed <= RUN_WINDOW_BUDGET_MINUTES

    def to_dict(self) -> dict:
        return {
            "custodian_id": self.custodian_id,
            "business_date": self.business_date,
            "cutoff_time": self.cutoff_time,
            "timezone": self.timezone,
            "files": [f.to_dict() for f in self.files],
            "missing_files": [f.pattern for f in self.missing_files],
            "elapsed_minutes": round(self.elapsed_minutes, 2) if self.elapsed_minutes is not None else None,
            "within_budget": self.within_budget,
            "run_log": self.run_log,
        }


def build(
    config_path: Path,
    *,
    run_path: Path | None = None,
    specs_dir: Path = Path("specs"),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
    root: Path | None = None,
) -> CustodianRunStatus:
    registry, catalog, packs = _load_context(specs_dir, rules_dir, domains_dir, root)
    config = _compile(config_path, registry, catalog, packs, root)
    run_input = load_run(run_path)

    files = tuple(
        FileStage(
            pattern=f["pattern"],
            description=f.get("description", ""),
            arrived_at=(run_input.stages.get(f["pattern"]) or {}).get("arrived_at"),
            parsed_at=(run_input.stages.get(f["pattern"]) or {}).get("parsed_at"),
            resolved_at=(run_input.stages.get(f["pattern"]) or {}).get("resolved_at"),
            published_at=(run_input.stages.get(f["pattern"]) or {}).get("published_at"),
        )
        for f in (config.delivery or {}).get("files", [])
    )

    return CustodianRunStatus(
        custodian_id=config.source["custodian"],
        business_date=run_input.business_date,
        cutoff_time=(config.delivery or {}).get("cutoff_time"),
        timezone=(config.delivery or {}).get("timezone"),
        files=files,
        run_log=run_input.run_log,
    )


# -- the dashboard: many custodians together ------------------------------------------------


@dataclass(frozen=True)
class Dashboard:
    as_of: str
    statuses: tuple[CustodianRunStatus, ...]

    @property
    def late(self) -> tuple[CustodianRunStatus, ...]:
        as_of_dt = _parse_at(self.as_of)
        return tuple(s for s in self.statuses if s.is_late(as_of_dt))

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "statuses": [s.to_dict() for s in self.statuses], "late": [s.custodian_id for s in self.late]}


def build_dashboard(statuses: tuple[CustodianRunStatus, ...], *, as_of: datetime | None = None) -> Dashboard:
    as_of = as_of or datetime.now(timezone.utc)
    return Dashboard(as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), tuple(statuses))


# -- the view --------------------------------------------------------------------------


def _bar(elapsed: float | None, budget: int = RUN_WINDOW_BUDGET_MINUTES, width: int = 20) -> str:
    if elapsed is None:
        return "(in progress)"
    filled = min(width, round(width * elapsed / budget))
    return "[" + "#" * filled + "." * (width - filled) + f"] {elapsed:.1f}m / {budget}m"


def render_status_markdown(status: CustodianRunStatus) -> str:
    out = [f"# {status.custodian_id}" + (f" — {status.business_date}" if status.business_date else ""), ""]
    out.append(f"Cutoff {status.cutoff_time or '?'} {status.timezone or ''}".rstrip())
    out.append("")
    out.append("| File | Stage | Arrived | Parsed | Resolved | Published |")
    out.append("|---|---|---|---|---|---|")
    for f in status.files:
        out.append(f"| `{f.pattern}` | **{f.current_stage}** | {f.arrived_at or '-'} | {f.parsed_at or '-'} | {f.resolved_at or '-'} | {f.published_at or '-'} |")
    out.append("")
    if status.missing_files:
        out.append(f"**Missing**: {', '.join(f.pattern for f in status.missing_files)}")
        out.append("")
    out.append(f"Timing: {_bar(status.elapsed_minutes)}" + ("" if status.within_budget is None else (" — within budget" if status.within_budget else " — OVER BUDGET")))
    out.append("")
    out.append(f"Run log: {status.run_log or 'no run log given'}")
    out.append("")
    return "\n".join(out)


def render_dashboard_markdown(dashboard: Dashboard) -> str:
    out = [f"# Run status dashboard — as of {dashboard.as_of}", ""]
    if dashboard.late:
        out.append(f"**Late**: {', '.join(s.custodian_id for s in dashboard.late)}")
        out.append("")
    out.append("| Custodian | Missing | Timing | Run log |")
    out.append("|---|---|---|---|")
    for s in dashboard.statuses:
        missing = ", ".join(f.pattern for f in s.missing_files) or "-"
        timing = _bar(s.elapsed_minutes)
        out.append(f"| {s.custodian_id} | {missing} | {timing} | {s.run_log or 'no run log given'} |")
    out.append("")
    return "\n".join(out)
