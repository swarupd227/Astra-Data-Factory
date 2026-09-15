"""Agent evaluation harness: every agent scored against its own gold set, by tier, weekly published (S4.3.4, ADR 0040, product spec Section 11).

A gold set (`agents/<agent>/eval.yaml`) is reviewed inputs and their
correct output, one case at a time, each case tagged with the tier its
input belongs to (the same simple/medium/complex vocabulary a source
config's own `tier` already uses). An output is a set of canonical
string items — a mapping, a spec field, a rule citation, whatever the
agent produces — so this harness never has to know what any particular
agent's output *means*, only how to compare two sets of items an
agent's own wrapper already canonicalized the same way. Precision and
recall are computed the classic way (true positives over predicted,
true positives over expected) and rolled up per tier by *summing* true
positives, predicted and expected across every case of the tier before
dividing — not averaging the cases' own rates — the same reason S4.2.2's
parity report is weighted by rows rather than averaged by cycle: a tier
with one large, hard case should not be drowned out by three small,
easy ones, or the reverse.

A gold set's thresholds are set by the agent engineer from the pilot
baseline (the backlog's own convention for every `[T]` placeholder,
docs/backlog-v0.2.md line 11) — never invented here. `append_case`
(S6.3.6) is the one function that writes a gold set rather than only
reading one: a human's own accept/reject decision on an agent's draft,
turned into one new case, refused outright if its tier has no
threshold already set. `agent-eval score`
is the per-change gate: a tier whose measured precision or recall falls
below its threshold, or a gold set case with no prediction at all, fails
the check and blocks release. `agent-eval report` is the weekly one:
every agent that has both a gold set and a predictions file scores, and
the results roll up by tier across every agent — the "metrics visible
to the client: per agent, per tier, per week" the product spec asks for.

No agent exists yet in this repository (the Agents plane, E5, has not
started); this harness is deliberately agent-agnostic so every future
agent's own "build and evaluate" story only has to produce a
predictions.yaml in the shape this module already scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

EVAL_SCHEMA = "eval-v0.schema.json"
EVAL_FILE = "eval.yaml"
PREDICTIONS_SCHEMA = "predictions-v0.schema.json"
PREDICTIONS_FILE = "predictions.yaml"
TIERS = ("simple", "medium", "complex")


class AgentEvalError(RuntimeError):
    pass


# -- the gold set and its thresholds --------------------------------------------


@dataclass(frozen=True)
class Threshold:
    precision: float
    recall: float

    def to_dict(self) -> dict:
        return {"precision": self.precision, "recall": self.recall}


@dataclass(frozen=True)
class Case:
    id: str
    tier: str
    input: str
    expected: frozenset[str]


@dataclass(frozen=True)
class GoldSet:
    agent: str
    description: str
    thresholds: dict[str, Threshold]
    cases: tuple[Case, ...]
    path: Path

    def case(self, case_id: str) -> Case | None:
        return next((c for c in self.cases if c.id == case_id), None)


def load_gold_set(path: Path, root: Path | None = None) -> tuple[GoldSet | None, list[Problem]]:
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
    if data.get("eval_version") != 0:
        line = line_of(data, ["eval_version"]) if "eval_version" in data else 1
        return None, [Problem(display, line, f"eval_version must be 0; found {data.get('eval_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_verification.schemas", EVAL_SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    seen_ids: dict[str, int] = {}
    for i, raw in enumerate(data["cases"]):
        if raw["id"] in seen_ids:
            problems.append(Problem(display, line_of(data, ["cases", i, "id"]), f"cases[{i}]: id '{raw['id']}' is already used by cases[{seen_ids[raw['id']]}]"))
        seen_ids[raw["id"]] = i
        tier = raw["tier"]
        if tier not in data["thresholds"]:
            problems.append(Problem(display, line_of(data, ["cases", i, "tier"]), f"cases[{i}]: tier '{tier}' has no threshold in thresholds; every tier a case uses needs one"))
    if problems:
        return None, dedupe(problems)

    gold = GoldSet(
        agent=data["agent"],
        description=" ".join(str(data.get("description", "")).split()),
        thresholds={tier: Threshold(t["precision"], t["recall"]) for tier, t in data["thresholds"].items()},
        cases=tuple(Case(c["id"], c["tier"], c["input"], frozenset(c["expected"])) for c in data["cases"]),
        path=path,
    )
    return gold, []


def _yaml_string(value: str) -> str:
    return json.dumps(value)


def _render_case_block(case: Case) -> str:
    lines = [
        f"  - id: {case.id}",
        f"    tier: {case.tier}",
        f"    input: {_yaml_string(case.input)}",
    ]
    if case.expected:
        lines.append("    expected:")
        lines.extend(f"      - {_yaml_string(item)}" for item in sorted(case.expected))
    else:
        lines.append("    expected: []")
    return "\n".join(lines) + "\n"


def append_case(path: Path, case: Case, *, root: Path | None = None) -> GoldSet:
    """Appends one new case to an already-existing gold set file (S6.3.6's own "accept / reject
    feeds the agent evaluation set automatically"), textually — preserving everything already in
    the file, including its own leading comment header, which `GoldSet` itself does not capture,
    rather than reloading the file into a `GoldSet` and re-rendering the whole document from that
    (which would silently drop the header). This assumes `cases:` is the file's last top-level
    key, true of every gold set committed today; if that assumption were ever wrong, or this
    function's own rendering were wrong, the reload-and-validate step below catches it and rolls
    the file back rather than leaving a broken gold set on disk.

    Refuses outright, writing nothing, when the case id already exists (case ids are unique per
    file) or the case's own tier has no threshold in this gold set — a tier's thresholds are set
    from the pilot baseline, never invented here (module docstring), so a case for an unthresholded
    tier needs a human to add that threshold first, not a fabricated one from this function.
    """
    path = Path(path)
    gold, problems = load_gold_set(path, root)
    if problems:
        raise AgentEvalError("; ".join(p.format() for p in problems))
    if gold.case(case.id) is not None:
        raise AgentEvalError(f"{display_path(path, root)}: case '{case.id}' already exists in this gold set")
    if case.tier not in gold.thresholds:
        raise AgentEvalError(f"{display_path(path, root)}: tier '{case.tier}' has no threshold in this gold set; add one first (thresholds are set from the pilot baseline, never invented here)")

    original = path.read_text(encoding="utf-8")
    path.write_text(original.rstrip("\n") + "\n\n" + _render_case_block(case), encoding="utf-8", newline="\n")

    reloaded, reload_problems = load_gold_set(path, root)
    if reload_problems:
        path.write_text(original, encoding="utf-8", newline="\n")
        raise AgentEvalError(f"appending case '{case.id}' produced an invalid gold set, rolled back: " + "; ".join(p.format() for p in reload_problems))
    return reloaded


def discover(agents_dir: Path) -> list[Path]:
    return sorted(p for p in Path(agents_dir).glob(f"*/{EVAL_FILE}") if p.is_file())


def check(agents_dir: Path, root: Path | None = None) -> tuple[list[GoldSet], list[Problem]]:
    gold_sets: list[GoldSet] = []
    problems: list[Problem] = []
    for path in discover(agents_dir):
        gold, found = load_gold_set(path, root)
        problems.extend(found)
        if gold is None:
            continue
        if gold.agent != path.parent.name:
            problems.append(Problem(display_path(path, root), None, f"agent '{gold.agent}' must match the directory '{path.parent.name}'"))
        gold_sets.append(gold)
    return gold_sets, problems


# -- predictions: what the agent actually produced ------------------------------


@dataclass(frozen=True)
class Predictions:
    agent: str
    run: str
    items: dict[str, frozenset[str]]
    path: Path


def load_predictions(path: Path, root: Path | None = None) -> tuple[Predictions | None, list[Problem]]:
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
    if data.get("predictions_version") != 0:
        line = line_of(data, ["predictions_version"]) if "predictions_version" in data else 1
        return None, [Problem(display, line, f"predictions_version must be 0; found {data.get('predictions_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_verification.schemas", PREDICTIONS_SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    predictions = Predictions(
        agent=data["agent"],
        run=data["run"],
        items={case_id: frozenset(items) for case_id, items in data["predictions"].items()},
        path=path,
    )
    return predictions, []


# -- scoring, pure ------------------------------------------------------------


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    tier: str
    expected: frozenset[str]
    predicted: frozenset[str]

    @property
    def true_positives(self) -> frozenset[str]:
        return self.expected & self.predicted

    @property
    def false_positives(self) -> frozenset[str]:
        return self.predicted - self.expected

    @property
    def false_negatives(self) -> frozenset[str]:
        return self.expected - self.predicted

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "tier": self.tier,
            "expected": sorted(self.expected),
            "predicted": sorted(self.predicted),
            "false_positives": sorted(self.false_positives),
            "false_negatives": sorted(self.false_negatives),
        }


@dataclass
class TierScore:
    tier: str
    cases: int = 0
    true_positives: int = 0
    predicted: int = 0
    expected: int = 0

    @property
    def precision(self) -> float | None:
        return None if self.predicted == 0 else self.true_positives / self.predicted

    @property
    def recall(self) -> float | None:
        return None if self.expected == 0 else self.true_positives / self.expected

    def meets(self, threshold: Threshold) -> bool:
        return self.precision is not None and self.recall is not None and self.precision >= threshold.precision and self.recall >= threshold.recall

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "cases": self.cases,
            "precision": None if self.precision is None else round(self.precision, 6),
            "recall": None if self.recall is None else round(self.recall, 6),
        }


def _tier_scores(cases: list[CaseScore]) -> dict[str, TierScore]:
    """Micro-averaged: true positives, predicted and expected summed across every case of a tier before dividing, not the mean of each case's own rate — a tier's harder or larger cases weigh proportionally more."""
    by_tier: dict[str, TierScore] = {}
    for c in cases:
        s = by_tier.setdefault(c.tier, TierScore(c.tier))
        s.cases += 1
        s.true_positives += len(c.true_positives)
        s.predicted += len(c.predicted)
        s.expected += len(c.expected)
    return by_tier


@dataclass
class AgentEvalResult:
    agent: str
    run: str
    thresholds: dict[str, Threshold]
    cases: tuple[CaseScore, ...] = ()
    missing_cases: tuple[str, ...] = ()  # gold set case ids with no prediction at all

    @property
    def by_tier(self) -> dict[str, TierScore]:
        return _tier_scores(list(self.cases))

    @property
    def passed(self) -> bool:
        if self.missing_cases:
            return False
        return all(s.meets(self.thresholds[tier]) for tier, s in self.by_tier.items() if tier in self.thresholds)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "run": self.run,
            "thresholds": {t: v.to_dict() for t, v in self.thresholds.items()},
            "missing_cases": list(self.missing_cases),
            "by_tier": {t: s.to_dict() for t, s in self.by_tier.items()},
            "cases": [c.to_dict() for c in self.cases],
            "passed": self.passed,
        }


def score(gold: GoldSet, predictions: Predictions) -> AgentEvalResult:
    if predictions.agent != gold.agent:
        raise AgentEvalError(f"predictions are for agent '{predictions.agent}', not '{gold.agent}'")
    cases: list[CaseScore] = []
    missing: list[str] = []
    for c in gold.cases:
        if c.id not in predictions.items:
            missing.append(c.id)
            continue
        cases.append(CaseScore(c.id, c.tier, c.expected, predictions.items[c.id]))
    return AgentEvalResult(gold.agent, predictions.run, gold.thresholds, tuple(cases), tuple(missing))


# -- the per-change report -----------------------------------------------------


def render_markdown(result: AgentEvalResult) -> str:
    out = [f"# Agent evaluation: {result.agent}", ""]
    out.append(f"Run `{result.run}`. {len(result.cases)} case(s) scored" + (f", {len(result.missing_cases)} missing" if result.missing_cases else "") + f". Passed: {'yes' if result.passed else 'no'}.")
    out.append("")
    if result.missing_cases:
        out.append("**Missing predictions** (not scored, counted as a failure): " + ", ".join(f"`{c}`" for c in result.missing_cases))
        out.append("")
    out.append("| Tier | Cases | Precision | Recall | Threshold | Meets |")
    out.append("|---|---|---|---|---|---|")
    for tier in TIERS:
        if tier not in result.by_tier:
            continue
        s = result.by_tier[tier]
        t = result.thresholds.get(tier)
        p = f"{s.precision:.1%}" if s.precision is not None else "n/a"
        r = f"{s.recall:.1%}" if s.recall is not None else "n/a"
        target = f"P≥{t.precision:.0%} R≥{t.recall:.0%}" if t else "-"
        meets = "yes" if t and s.meets(t) else "NO"
        out.append(f"| {tier} | {s.cases} | {p} | {r} | {target} | {meets} |")
    out.append("")
    if result.cases:
        out.append("## False positives and false negatives, by case")
        out.append("")
        out.append("| Case | Tier | False positives | False negatives |")
        out.append("|---|---|---|---|")
        for c in result.cases:
            if c.false_positives or c.false_negatives:
                out.append(f"| `{c.case_id}` | {c.tier} | {', '.join(sorted(c.false_positives)) or '-'} | {', '.join(sorted(c.false_negatives)) or '-'} |")
        out.append("")
    return "\n".join(out)


def write_report(result: AgentEvalResult, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "eval.md"
    data = out / "eval.json"
    markdown.write_text(render_markdown(result), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data


# -- the weekly report, across every agent --------------------------------------


@dataclass
class WeeklyReport:
    generated_at: str
    results: tuple[AgentEvalResult, ...] = ()
    skipped: tuple[str, ...] = ()  # agents with a gold set but no predictions yet

    @property
    def by_tier(self) -> dict[str, TierScore]:
        return _tier_scores([c for r in self.results for c in r.cases])

    @property
    def all_passed(self) -> bool:
        """True when every agent actually scored this week passed; an agent with no predictions yet is skipped, not failed, and never makes this false on its own."""
        return all(r.passed for r in self.results)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "agents_scored": [r.agent for r in self.results],
            "skipped": list(self.skipped),
            "by_tier": {t: s.to_dict() for t, s in self.by_tier.items()},
            "results": [r.to_dict() for r in self.results],
            "all_passed": self.all_passed,
        }


def render_weekly_markdown(report: WeeklyReport) -> str:
    out = [f"# Agent evaluation, weekly: {report.generated_at}", ""]
    out.append(f"{len(report.results)} agent(s) scored" + (f", {len(report.skipped)} skipped (no predictions yet): {', '.join(report.skipped)}" if report.skipped else "") + ".")
    out.append("")
    out.append("## By tier, across every agent")
    out.append("")
    out.append("| Tier | Cases | Precision | Recall |")
    out.append("|---|---|---|---|")
    for tier in TIERS:
        if tier not in report.by_tier:
            continue
        s = report.by_tier[tier]
        p = f"{s.precision:.1%}" if s.precision is not None else "n/a"
        r = f"{s.recall:.1%}" if s.recall is not None else "n/a"
        out.append(f"| {tier} | {s.cases} | {p} | {r} |")
    out.append("")
    out.append("## By agent")
    out.append("")
    out.append("| Agent | Run | Cases | Passed |")
    out.append("|---|---|---|---|")
    for r in report.results:
        out.append(f"| {r.agent} | {r.run} | {len(r.cases)} | {'yes' if r.passed else 'NO'} |")
    out.append("")
    return "\n".join(out)


def write_weekly_report(report: WeeklyReport, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "weekly.md"
    data = out / "weekly.json"
    markdown.write_text(render_weekly_markdown(report), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data


def run_weekly(agents_dir: Path, root: Path | None = None, *, clock=lambda: datetime.now(timezone.utc)) -> tuple[WeeklyReport, list[Problem]]:
    """Score every agent that has both a gold set and a predictions file; an agent with only a gold set is skipped, named, not failed — it has not been run yet."""
    gold_sets, problems = check(agents_dir, root)
    results: list[AgentEvalResult] = []
    skipped: list[str] = []
    for gold in gold_sets:
        predictions_path = gold.path.parent / PREDICTIONS_FILE
        if not predictions_path.is_file():
            skipped.append(gold.agent)
            continue
        predictions, found = load_predictions(predictions_path, root)
        problems.extend(found)
        if predictions is None:
            continue
        results.append(score(gold, predictions))
    report = WeeklyReport(clock().strftime("%Y-%m-%dT%H:%M:%SZ"), tuple(results), tuple(skipped))
    return report, problems
