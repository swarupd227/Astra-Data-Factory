"""Docs & Runbook Writer: a per-source doc rendered from what is already approved
(S5.12.1, product spec Section 5.12).

Nothing here proposes anything for a person to review. A compiled config, its spec, its
domain pack's rejection taxonomy and its rule catalog entries are already registry data,
approved before this agent ever runs — this agent only presents them, the same way
`astra_knowledge.cdm.write_rendered`/`check_rendered` render the canonical model's own DDL
and tests straight from the model (S2.x): render, and let CI's own `render --check` catch a
doc that has gone stale. Every other agent in this plane writes a draft under `work/` for a
person to approve; this one writes straight into `docs/`, because there is nothing here to
approve, only to keep current — the same reason `astra-spec cdm render --check`,
`astra-data reference render --check` and `astra-data gold render --check` already run on
every pull request rather than landing in `work/` for a person to accept.

Two things this doc exists to answer, both templated from already-structured data, nothing
free text a model invented:

  - **What does the chaos drill's own generic runbook (docs/runbooks/chaos-drill.md) mean for
    THIS source?** Four fixed scenarios (`astra_verification.chaos.SCENARIOS`, ADR 0038): what
    each detects, the taxonomy code and its resolution text, the alert it raises. `late` and
    `truncated` have no fixed taxonomy code of their own — `late` is a timing fault the
    taxonomy does not name, and `truncated` ties to whichever `control_total` dq_rule this
    source's own config declares (a config without one cannot prove the scenario, and the doc
    says so instead of guessing).
  - **What alerts does this source raise, and what should a person do?** The config's own
    `delivery`/`alerts` blocks, read against ADR 0007's fixed alerting rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astra_core.problems import Problem, display_path
from astra_data.compiler import CompiledConfig, compile_paths
from astra_data.render.names import bundle_name
from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rejections import RejectionCode
from astra_knowledge.rules import Catalog

# astra_verification.chaos.SCENARIOS (ADR 0038); duplicated here as a plain tuple rather than
# importing chaos.py, which pulls in the sandbox/dry-run machinery for one constant. A test
# proves this stays equal to the real one.
CHAOS_SCENARIOS = ("late", "malformed", "duplicate", "truncated")
# astra_verification.chaos._run_scenario's own fixed scenario -> taxonomy code mapping.
CHAOS_REJECTION_CODE = {"malformed": "RECORD_TYPE_UNKNOWN", "duplicate": "MERGE_DUPLICATE_KEY"}
DOC_SUFFIX = ".md"


class DocsWriterError(RuntimeError):
    pass


# -- loading -------------------------------------------------------------------


def load_compiled_configs(
    paths,
    *,
    specs_dir: Path = Path("specs"),
    rules_dir: Path = Path("rules"),
    domains_dir: Path = Path("domains"),
    root: Path | None = None,
) -> tuple[CompiledConfig, ...]:
    registry, problems = Registry.load(Path(specs_dir), repo_root=root)
    if problems:
        raise DocsWriterError("; ".join(p.format() for p in problems))
    catalog, problems = Catalog.load(Path(rules_dir), root, registry)
    if problems:
        raise DocsWriterError("; ".join(p.format() for p in problems))
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        raise DocsWriterError("; ".join(p.format() for p in problems))
    compiled, problems = compile_paths(paths, registry=registry, catalog=catalog, packs=packs, root=root)
    if problems:
        raise DocsWriterError("; ".join(p.format() for p in problems))
    return tuple(compiled)


# -- the chaos scenarios, for this source ---------------------------------------


@dataclass(frozen=True)
class ChaosScenarioDoc:
    scenario: str
    detects: str
    code: str | None
    severity: str | None
    resolution: str
    alert_kind: str
    retryable: bool


def chaos_scenarios_for(config: CompiledConfig) -> tuple[ChaosScenarioDoc, ...]:
    control_total = next((r for r in config.dq_rules if r.kind == "control_total"), None)
    docs: list[ChaosScenarioDoc] = []
    for scenario in CHAOS_SCENARIOS:
        if scenario == "late":
            docs.append(
                ChaosScenarioDoc(
                    scenario="late",
                    detects="the file completes this source's expected set after the custodian's cutoff",
                    code=None,
                    severity="info",
                    resolution="Nothing to fix: the file's own content is correct, only its arrival was late. The scenario ends once the custodian_late_arrival alert is confirmed.",
                    alert_kind="custodian_late_arrival",
                    retryable=False,
                )
            )
        elif scenario == "truncated":
            if control_total is None:
                docs.append(
                    ChaosScenarioDoc(
                        scenario="truncated",
                        detects="no control_total dq_rule is configured for this source; the truncated scenario cannot be proven until one is added",
                        code=None,
                        severity=None,
                        resolution="Add a control_total dq_rule to this source's config before running the chaos drill's truncated scenario.",
                        alert_kind="chaos_scenario",
                        retryable=True,
                    )
                )
            else:
                docs.append(
                    ChaosScenarioDoc(
                        scenario="truncated",
                        detects=control_total.check,
                        code=control_total.id,
                        severity=control_total.severity,
                        resolution="Ask the custodian to resend a complete file; the trailer's own count no longer matches the file's detail records.",
                        alert_kind="chaos_scenario",
                        retryable=True,
                    )
                )
        else:  # malformed, duplicate
            code = CHAOS_REJECTION_CODE[scenario]
            rc = config.pack.rejections.code(code)
            if rc is None:
                raise DocsWriterError(f"{config.id}: the domain pack's rejection taxonomy has no {code!r} code, which the {scenario} chaos scenario needs")
            docs.append(
                ChaosScenarioDoc(
                    scenario=scenario,
                    detects=rc.description,
                    code=rc.code,
                    severity=rc.severity,
                    resolution=rc.resolution,
                    alert_kind="chaos_scenario",
                    retryable=True,
                )
            )
    return tuple(docs)


# -- the exceptions this source can raise -----------------------------------------


def resolution_rejection_codes(config: CompiledConfig) -> tuple[RejectionCode, ...]:
    """Active rejection codes whose entity this source's own `resolution` block actually resolves."""
    entities: set[str] = set()
    if config.resolution.account is not None:
        entities.add("Account")
    if config.resolution.security is not None:
        entities.add("Security")
    if config.resolution.transaction_code is not None:
        entities.add("Transaction")
    if config.resolution.price is not None:
        entities.add("Price")
    return tuple(c for c in config.pack.rejections.active() if c.entity in entities)


# -- the doc ---------------------------------------------------------------------


def render_markdown(config: CompiledConfig) -> str:
    source = config.source
    out = [f"# {source['custodian']}: {source.get('description') or source['id']}", ""]
    out.append(
        f"Source `{source['id']}` ({source['file_type']}), tier `{source['tier']}`, family `{source['family']}`. "
        f"Spec `{config.spec.id}` version `{config.spec.version}`. Effective from {config.effective_from.isoformat()}. "
        f"Owner: {config.owner['name']} <{config.owner['email']}>."
    )
    out.append("")

    out.append("## Delivery and alerting")
    out.append("")
    if config.delivery is None:
        out.append("No `delivery` block is configured for this source; it has a task-failure severity but never goes late (ADR 0007).")
        out.append("")
    else:
        d = config.delivery
        out.append(
            f"Cutoff **{d['cutoff_time']} {d['timezone']}**, on {', '.join(d['business_days'])}. Late means the cutoff has passed "
            "on one of these days and at least one expected file pattern below has no arrival for the business date (ADR 0007 point 7)."
        )
        out.append("")
        out.append("| Expected file | Description |")
        out.append("|---|---|")
        for f in d.get("files", []):
            out.append(f"| `{f['pattern']}` | {f.get('description', '')} |")
        out.append("")
    if config.alerts is None:
        out.append("No `alerts` block is configured; a task failure for this source alerts at the platform default (ADR 0007).")
        out.append("")
    else:
        a = config.alerts
        out.append(f"- **late**: severity `{a.get('late', 'not configured')}`")
        out.append(f"- **task_failure**: severity `{a.get('task_failure', 'not configured')}`")
        out.append("")

    out.append("## Chaos drill: what each scenario means for this source")
    out.append("")
    out.append("How to run the drill: [docs/runbooks/chaos-drill.md](../chaos-drill.md). What each scenario means here:")
    out.append("")
    out.append("| Scenario | Detects | Code | Severity | Alert | Retryable |")
    out.append("|---|---|---|---|---|---|")
    for s in chaos_scenarios_for(config):
        out.append(f"| {s.scenario} | {s.detects} | `{s.code or '-'}` | {s.severity or '-'} | {s.alert_kind} | {'yes' if s.retryable else 'no'} |")
    out.append("")
    out.append("Resolution, per scenario:")
    out.append("")
    for s in chaos_scenarios_for(config):
        out.append(f"- **{s.scenario}**: {s.resolution}")
    out.append("")

    out.append("## Data quality rules")
    out.append("")
    if config.dq_rules:
        out.append("| Rule | Kind | Check | Severity | Owner |")
        out.append("|---|---|---|---|---|")
        for r in config.dq_rules:
            out.append(f"| `{r.id}` | {r.kind} | {r.check} | {r.severity} | {r.owner or '-'} |")
        out.append("")
    else:
        out.append("No dq_rules are configured for this source.")
        out.append("")

    out.append("## Exceptions this source can raise")
    out.append("")
    codes = resolution_rejection_codes(config)
    if codes:
        out.append("| Code | Name | Severity | Owner | Resolution |")
        out.append("|---|---|---|---|---|")
        for c in codes:
            out.append(f"| `{c.code}` | {c.name} | {c.severity} | {c.owner} | {c.resolution} |")
        out.append("")
    else:
        out.append("This source's config resolves no account, security, transaction code or price, so it raises no resolution exceptions of its own.")
        out.append("")

    out.append("## Rules")
    out.append("")
    if config.rules:
        out.append("| Rule | Status | Citation | Text |")
        out.append("|---|---|---|---|")
        for r in config.rules:
            out.append(f"| `{r.id}` | {r.status} | {r.citation.text} | {r.text} |")
        out.append("")
    else:
        out.append("This source's config cites no rule catalog entries.")
        out.append("")

    out.append("---")
    out.append("")
    out.append("Generated by `astra-agents docs-writer render`. Regenerate rather than editing by hand; `docs-writer render --check` fails a pull request that lets this go stale.")
    out.append("")
    return "\n".join(out)


def rendered_path(config: CompiledConfig, out: Path) -> Path:
    return Path(out) / f"{bundle_name(config)}{DOC_SUFFIX}"


def rendered_files(configs) -> dict[str, str]:
    """Every doc this set of configs renders, keyed by file name relative to `out`."""
    return {f"{bundle_name(c)}{DOC_SUFFIX}": render_markdown(c) for c in configs}


def write_rendered(configs, out: Path) -> list[Path]:
    """Write every source's doc, removing stale ones no longer produced. Returns the paths written."""
    out = Path(out)
    files = rendered_files(configs)
    out.mkdir(parents=True, exist_ok=True)
    for existing in sorted(p for p in out.glob(f"*{DOC_SUFFIX}") if p.is_file()):
        if existing.name not in files:
            existing.unlink()
    written = []
    for name, text in files.items():
        path = out / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def check_rendered(configs, out: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every doc that is missing, stale, or no longer produced."""
    out = Path(out)
    files = rendered_files(configs)
    problems: list[Problem] = []
    for name, text in files.items():
        path = out / name
        if not path.is_file():
            problems.append(Problem(display_path(path, repo_root), None, "not rendered; run astra-agents docs-writer render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(display_path(path, repo_root), None, "stale: the source changed since it was rendered; run astra-agents docs-writer render"))
    if out.is_dir():
        for existing in sorted(p for p in out.glob(f"*{DOC_SUFFIX}") if p.is_file()):
            if existing.name not in files:
                problems.append(Problem(display_path(existing, repo_root), None, "no longer produced by any source config; run astra-agents docs-writer render to remove it"))
    return problems
