"""Golden dataset viewer: which business days are captured per custodian, with hashes and gaps,
so parity runs are known to be complete (S6.3.10, ADR 0066, product spec's own "Golden Dataset |
Captured legacy outputs... | Object storage, versioned, hashed" and backlog S4.1.2's own "Each
dataset has a hash and the source file list").

Nothing here re-captures a golden dataset, and nothing here writes to the golden store or its
repository index. `astra_verification.golden.load_index` already reads a custodian's own
`datasets.json` — never raising, an empty list when nothing has been captured yet, exactly the
tolerance a calendar view needs. `astra_verification.parity_report.captured_business_dates`, by
contrast, *raises* on zero captures — it is built for "give me dates to run parity against," not
"show me the calendar, possibly empty" — so this module reads the index directly instead, and
never collapses to only the latest version per day the way that function does: this viewer's own
"hashes" (plural, AC1) means every captured version of every day, superseded ones included.
`astra_verification.golden.Capture.business_days_between(start, end)` — the same function the
real `astra-verify golden capture` CLI itself walks — is the one source of "which business days
*should* have been captured" this module needs; a gap is simply one of those days with no entry
in the index. `astra_verification.golden.coverage` (S4.1.2's own "30 to 60 business days"
verdict) is reused directly for this viewer's own summary, not recomputed.

**"Capture can be requested from the screen" composes and shows the real `astra-verify golden
capture` command for a gap — it never runs one.** A live capture needs a real non-prod Loader
database connection, a real historical-file location, and a real `loader-replay` subprocess
(`astra_verification.golden`'s own `cmd_golden_capture` requirements) — no environment this
codebase runs in has any of the three, the same reason S6.3.7/S6.3.8/S6.3.9 each stopped short of
a live verification or deployment action of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from astra_verification.golden import Capture, coverage, load_capture, load_index


class GoldenViewerError(RuntimeError):
    pass


def _load_capture(path: Path, root: Path | None) -> Capture:
    capture, problems = load_capture(Path(path), root)
    if problems:
        raise GoldenViewerError("; ".join(p.format() for p in problems))
    return capture


def _capture_command(capture_path: Path, start: date, end: date, store: str | None) -> str:
    store_arg = store or "<golden store, e.g. s3://bucket/prefix>"
    return f"astra-verify golden capture {capture_path} --from {start.isoformat()} --to {end.isoformat()} --store {store_arg}"


# -- AC1: every captured version, with its own hash ----------------------------------------------


@dataclass(frozen=True)
class CapturedDay:
    business_date: str
    version: int
    hash: str
    captured_at: str
    rows: dict[str, int]
    source_files: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "business_date": self.business_date,
            "version": self.version,
            "hash": self.hash,
            "captured_at": self.captured_at,
            "rows": dict(self.rows),
            "source_files": list(self.source_files),
        }


# -- AC2: a gap, with the command that would fill it ----------------------------------------------


@dataclass(frozen=True)
class Gap:
    business_date: str
    capture_command: str

    def to_dict(self) -> dict:
        return {"business_date": self.business_date, "capture_command": self.capture_command}


# -- the calendar: every expected business day in a window, captured or a gap --------------------


@dataclass(frozen=True)
class GoldenCalendar:
    custodian: str
    start: str
    end: str
    captured: tuple[CapturedDay, ...]
    gaps: tuple[Gap, ...]
    coverage_days: int
    coverage_versions: int
    within_range: bool

    @property
    def complete(self) -> bool:
        """AC's own "parity runs are complete": no gap in the requested window."""
        return not self.gaps

    def to_dict(self) -> dict:
        return {
            "custodian": self.custodian,
            "start": self.start,
            "end": self.end,
            "captured": [c.to_dict() for c in self.captured],
            "gaps": [g.to_dict() for g in self.gaps],
            "coverage_days": self.coverage_days,
            "coverage_versions": self.coverage_versions,
            "within_range": self.within_range,
            "complete": self.complete,
        }


def build(
    capture_path: Path,
    golden_dir: Path,
    *,
    start: date,
    end: date,
    store: str | None = None,
    root: Path | None = None,
) -> GoldenCalendar:
    if start > end:
        raise GoldenViewerError(f"--from {start.isoformat()} is after --to {end.isoformat()}")
    capture = _load_capture(capture_path, root)
    index = load_index(Path(golden_dir), capture.custodian)
    captured_dates = {d["business_date"] for d in index["datasets"]}

    expected_days = capture.business_days_between(start, end)
    gaps = tuple(Gap(day.isoformat(), _capture_command(capture_path, day, day, store)) for day in expected_days if day.isoformat() not in captured_dates)
    captured = tuple(
        CapturedDay(d["business_date"], d["version"], d["hash"], d["captured_at"], dict(d.get("rows") or {}), tuple(d.get("source_files") or ()))
        for d in sorted(index["datasets"], key=lambda d: (d["business_date"], d["version"]))
        if start.isoformat() <= d["business_date"] <= end.isoformat()
    )
    cov = coverage(Path(golden_dir), capture.custodian)

    return GoldenCalendar(
        custodian=capture.custodian,
        start=start.isoformat(),
        end=end.isoformat(),
        captured=captured,
        gaps=gaps,
        coverage_days=cov.days,
        coverage_versions=cov.versions,
        within_range=cov.within_range,
    )


# -- the view --------------------------------------------------------------------------


def render_markdown(calendar: GoldenCalendar) -> str:
    out = [f"# Golden dataset calendar: {calendar.custodian}", ""]
    out.append(f"{calendar.start} to {calendar.end}. Overall coverage: {calendar.coverage_days} business day(s) captured, {calendar.coverage_versions} version(s) total" + (" — within 30 to 60" if calendar.within_range else " — outside 30 to 60") + ".")
    out.append("")

    out.append("## Captured")
    out.append("")
    if calendar.captured:
        out.append("| Business date | Version | Hash | Captured at | Rows |")
        out.append("|---|---|---|---|---|")
        for c in calendar.captured:
            rows = ", ".join(f"{k}: {v}" for k, v in c.rows.items()) or "-"
            out.append(f"| {c.business_date} | v{c.version} | `{c.hash[:12]}` | {c.captured_at} | {rows} |")
    else:
        out.append("Nothing captured in this window.")
    out.append("")

    out.append("## Gaps")
    out.append("")
    if calendar.gaps:
        out.append("| Business date | Request capture |")
        out.append("|---|---|")
        for g in calendar.gaps:
            out.append(f"| **{g.business_date}** | `{g.capture_command}` |")
    else:
        out.append("No gaps in this window.")
    out.append("")
    return "\n".join(out)
