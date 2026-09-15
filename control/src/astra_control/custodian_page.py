"""Custodian page: one page per custodian — family, tier, config version, current station, files
today, parity trend, open exceptions, cost, so all context is in one place (S6.3.3, ADR 0059,
product spec Section 3, the ops persona's own daily task; `docs/ux/personas.md`'s own custodian
page wireframe, S6.0.2).

Every field is read from something this repository already built, never re-derived:

  family, tier          the compiled config's own `source` block (astra_data.compiler)
  config version         `effective_from` (the same date-based versioning astra_knowledge's own
                         spec registry already uses) alongside the config's own content hash
                         (`provenance.config.sha256`) — together, "which exact version is in
                         force," not a version number this schema does not have
  current station        astra_control.board (S6.1.1) — read straight off the board, never a
                         second tracker
  files today             the config's own `delivery.files` (expected) against `arrivals`, a
                         caller-supplied map from pattern to arrival time — a real, working
                         stand-in for a live file-load-log query this environment does not have,
                         the same shape astra_control.board.yaml already stands in for Postgres
  parity trend            a real astra_verification.parity_report.ParityReport, read as JSON —
                         never recomputed
  open exceptions         astra_control.queue.exceptions_from (S6.3.2) — the exact same count a
                         person's own queue would show for this source, not a second definition
                         of "open"
  cost                    no live source exists — no FinOps agent has been built (the product
                         spec's own agent table names one, "Query tags, warehouse metrics," but
                         it is not part of this repository's E5 backlog) — caller-supplied only,
                         `None` when not given, shown honestly as "no data" rather than a
                         fabricated number

AC2's four links are real references where a real one exists, and an honest note where it does
not: `spec` and `config` are real file paths; `parity_viewer` and `exceptions` point at the real
report a caller gave (its own data, inspectable today) with a note that the *viewer* screens
themselves (S6.3.7, S6.2.5) are not built yet — the same honesty `astra_control.diff_review`'s
own citation links and `astra_control.queue`'s own per-item targets already practice, never a
link to a screen that does not exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra_core.yamlsource import SourceError, load
from astra_data.compiler import CompileError, CompiledConfig, compile_config
from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_control.board import Board, load_board
from astra_control.queue import exceptions_from


class CustodianPageError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def _load_context(specs_dir: Path, rules_dir: Path, domains_dir: Path, root: Path | None):
    registry, problems = Registry.load(Path(specs_dir), repo_root=root)
    if problems:
        raise CustodianPageError("; ".join(p.format() for p in problems))
    catalog, problems = Catalog.load(Path(rules_dir), root, registry)
    if problems:
        raise CustodianPageError("; ".join(p.format() for p in problems))
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        raise CustodianPageError("; ".join(p.format() for p in problems))
    return registry, catalog, packs


def _compile(path: Path, registry, catalog, packs, root: Path | None) -> CompiledConfig:
    path = Path(path)
    if not path.is_file():
        raise CustodianPageError(f"config file not found: {path}")
    try:
        return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=root)
    except CompileError as exc:
        raise CustodianPageError("; ".join(p.format() for p in exc.problems)) from exc


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def load_arrivals(path: Path | None) -> dict[str, str]:
    """A real, working stand-in for a live file-load-log query: pattern -> ISO arrival time,
    a platform administrator or an integration would populate this file for real; this module
    never invents an arrival that was not given."""
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SourceError) as exc:
        raise CustodianPageError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CustodianPageError(f"{path}: expected a mapping of pattern to arrival time")
    return {str(k): str(v) for k, v in (data.get("arrivals") or data).items()}


# -- the page -------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedFile:
    pattern: str
    description: str
    arrived_at: str | None

    @property
    def arrived(self) -> bool:
        return self.arrived_at is not None

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "description": self.description, "arrived": self.arrived, "arrived_at": self.arrived_at}


@dataclass(frozen=True)
class CustodianPage:
    custodian_id: str
    family: str
    tier: str
    effective_from: str
    config_sha256: str
    station: str | None
    cutoff_time: str | None
    timezone: str | None
    expected_files: tuple[ExpectedFile, ...]
    parity: dict[str, Any] | None
    open_exceptions: int
    cost: float | None
    links: dict[str, str]

    @property
    def all_arrived(self) -> bool:
        return all(f.arrived for f in self.expected_files)

    def to_dict(self) -> dict:
        return {
            "custodian_id": self.custodian_id,
            "family": self.family,
            "tier": self.tier,
            "effective_from": self.effective_from,
            "config_sha256": self.config_sha256,
            "station": self.station,
            "cutoff_time": self.cutoff_time,
            "timezone": self.timezone,
            "expected_files": [f.to_dict() for f in self.expected_files],
            "all_arrived": self.all_arrived,
            "parity": self.parity,
            "open_exceptions": self.open_exceptions,
            "cost": self.cost,
            "links": dict(self.links),
        }


def build(
    config_path: Path,
    *,
    board_path: Path | None = None,
    parity_report: Path | None = None,
    exception_report: Path | None = None,
    arrivals: dict[str, str] | None = None,
    cost: float | None = None,
    specs_dir: Path = Path("specs"),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
    root: Path | None = None,
) -> CustodianPage:
    registry, catalog, packs = _load_context(specs_dir, rules_dir, domains_dir, root)
    config = _compile(config_path, registry, catalog, packs, root)
    arrivals = arrivals or {}

    board: Board | None = load_board(Path(board_path)) if board_path else None
    card = board.card(config.source["custodian"]) if board else None

    expected = tuple(
        ExpectedFile(pattern=f["pattern"], description=f.get("description", ""), arrived_at=arrivals.get(f["pattern"]))
        for f in (config.delivery or {}).get("files", [])
    )

    parity_data = _read_json(parity_report) if parity_report else None
    parity = None
    if parity_data is not None:
        parity = {k: parity_data[k] for k in ("match_rate", "target", "meets_target", "trend") if k in parity_data}

    open_exceptions = len(exceptions_from(exception_report)) if exception_report else 0

    links = {
        "spec": f"specs/{config.spec.id}/{config.spec.version}.yaml",
        "config": str(config.path),
        "config_diff": f"astra-control diff-review run --old <an earlier {Path(config_path).name}> --new {config_path}",
        "parity_viewer": str(parity_report) if parity_report else "no parity report given",
        "parity_viewer_note": "S6.3.7 (dual-run and parity viewer) is not built yet" if not parity_report else "S6.3.7 is not built yet; this is the real report the viewer would render",
        "exceptions": str(exception_report) if exception_report else "no exception report given",
        "exceptions_note": "S6.2.5 (ops exception UI) is not built yet" if not exception_report else "S6.2.5 is not built yet; this is the real report the screen would render",
    }

    return CustodianPage(
        custodian_id=config.source["custodian"],
        family=config.source.get("family", ""),
        tier=config.source.get("tier", ""),
        effective_from=config.effective_from.isoformat(),
        config_sha256=config.provenance.get("config", {}).get("sha256", ""),
        station=card.station.value if card else None,
        cutoff_time=(config.delivery or {}).get("cutoff_time"),
        timezone=(config.delivery or {}).get("timezone"),
        expected_files=expected,
        parity=parity,
        open_exceptions=open_exceptions,
        cost=cost,
        links=links,
    )


# -- the view --------------------------------------------------------------------------


def render_markdown(page: CustodianPage) -> str:
    out = [f"# {page.custodian_id}", ""]
    out.append(f"Family `{page.family}` · tier `{page.tier}` · in force since {page.effective_from} (`{page.config_sha256[:12]}`)")
    out.append(f"Current station: **{page.station or 'not on the board'}**")
    out.append("")

    out.append("## Files today" + (f" — cutoff {page.cutoff_time} {page.timezone}" if page.cutoff_time else ""))
    out.append("")
    if page.expected_files:
        out.append("| Pattern | Description | Arrived |")
        out.append("|---|---|---|")
        for f in page.expected_files:
            out.append(f"| `{f.pattern}` | {f.description} | {f.arrived_at or 'not yet'} |")
    else:
        out.append("No delivery block configured for this source.")
    out.append("")

    out.append("## Parity trend")
    out.append("")
    if page.parity:
        p = page.parity
        out.append(f"{p.get('match_rate', 0):.1%} match rate (target {p.get('target', 0):.1%}) — {p.get('trend', 'unknown')}, {'meets target' if p.get('meets_target') else 'below target'}")
    else:
        out.append("No parity report given.")
    out.append("")

    out.append(f"## Open exceptions: {page.open_exceptions}")
    out.append("")

    out.append(f"## Cost: {f'${page.cost:.2f}/day' if page.cost is not None else 'no data — the FinOps agent is not built yet'}")
    out.append("")

    out.append("## Links")
    out.append("")
    out.append(f"- Spec: `{page.links['spec']}`")
    out.append(f"- Config: `{page.links['config']}`")
    out.append(f"- Config diff: `{page.links['config_diff']}`")
    out.append(f"- Parity viewer: `{page.links['parity_viewer']}` — {page.links['parity_viewer_note']}")
    out.append(f"- Exceptions: `{page.links['exceptions']}` — {page.links['exceptions_note']}")
    out.append("")

    return "\n".join(out)
