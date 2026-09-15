"""Agent suggestion review: an agent's draft shown beside its own source evidence, edited with
the original kept for comparison, and every accept or reject decision turned automatically into a
new case in that agent's own gold set (S6.3.6, ADR 0062, product spec Section 9: "Evaluation
sets: tighten: every human correction becomes a test case for the agent that missed it").

This module never imports `astra_agents` — the same "no new agent import" boundary `astra_control.
queue` already established (S6.3.2's own module docstring): it reads whatever an agent already
wrote to its own draft file, never the agent's own Python object model. Rule Recovery is this
story's one concrete integration, because its draft is the cleanest fit for what AC1 asks for side
by side: a rule's `text` is already a plain-language reasoning, its `citation` is already a real
`astra_knowledge.rules.Citation` pointing at a file and line of legacy code (AC1's own "source
file:line"), and a drafted rule is always `status="recovered"`, never auto-confirmed by the agent
(`astra_agents.rule_recovery`'s own structural guardrail — the tool schema given to the model has
no `status` field at all). A draft rule file is a real `rules/<group>/<name>.yaml`-shaped file
Rule Recovery already wrote under `work/rule-recovery/`, so it loads with the exact same
`astra_knowledge.rules.load_rule_file` this plane already uses for the real catalog
(`astra_control.rule_review`) — no second parser.

**AC2, "edit keeps the original for comparison," is a small, purpose-built diff over `ReviewItem`
fields, not a reuse of an existing comparator.** Neither `astra_verification.replay.config_diff`
(needs two compiled configs) nor `astra_control.spec_viewer.compare` (needs two `SourceSpec`
objects) operates on a rule's own text and citation — both are narrow, shape-specific comparators
by this plane's own established convention (ADR 0060's own `compare()`, built fresh for the same
reason). `Edited.diff()` compares `draft_text`, `citation_text` and `expected` between the
original and the edited `ReviewItem`, field by field.

**AC3, "accept / reject feeds the agent evaluation set automatically," calls
`astra_verification.agent_eval.append_case` directly** — the one function that writes a gold set,
added by this same story — rather than a second gold-set writer living here. `expected` is this
module's own new vocabulary decision for Rule Recovery's own drafts: a single canonical item,
`"rule:<id>"`, naming which rule id should be recovered from the reviewed input — reusing the
`"rule:<id>"` item shape `agents/break_explainer/eval.yaml`'s own real gold set already uses to
cite a rule by id, not invented from nothing. This tracks *which rule id* the agent should recover
from a given input, never the literal wording of `text` — an edit to `text` changes what the
reviewer sees and compares (AC2), but not what gets scored (AC3), matching the harness's own
"canonical string items," never free text (`astra_verification.agent_eval`'s own module
docstring). A reject records `expected=()` — "the agent should produce nothing here" — which is
only honest when the reviewed input is as narrow as the draft's own citation span; a reviewer
rejecting a draft recovered from a broad, multi-rule span should give a narrower `--input`
override, not rely on the draft's own citation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from astra_knowledge.rules import Citation, Rule, load_rule_file
from astra_verification.agent_eval import AgentEvalError, Case, GoldSet, TIERS, append_case, load_gold_set

from astra_control.diff_review import citation_link

RULE_RECOVERY_AGENT = "rule_recovery"


class AgentReviewError(RuntimeError):
    pass


# -- loading a draft ------------------------------------------------------------


def load_draft_rule(path: Path, root: Path | None = None) -> Rule:
    """A draft Rule Recovery already wrote under work/rule-recovery/ — the same parser
    astra_control.rule_review already uses for the real catalog, no second one (module
    docstring). No registry is given: a draft's own citation is legacy code, never a spec page,
    so there is nothing to check it against."""
    path = Path(path)
    if not path.is_file():
        raise AgentReviewError(f"draft rule file not found: {path}")
    rule, problems = load_rule_file(path, root)
    if problems:
        raise AgentReviewError("; ".join(p.format() for p in problems))
    return rule


# -- AC1: a draft beside its source evidence -------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    agent: str
    tier: str
    case_input: str
    draft_text: str
    citation_text: str
    citation_link: str
    expected: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "tier": self.tier,
            "case_input": self.case_input,
            "draft_text": self.draft_text,
            "citation_text": self.citation_text,
            "citation_link": self.citation_link,
            "expected": list(self.expected),
        }


def rule_recovery_item(rule: Rule, *, case_input: str, tier: str) -> ReviewItem:
    """A Rule Recovery draft's own text (reasoning) and citation (source evidence), plus the one
    canonical item accepting it, as-is, would mean: this rule id should be recovered from
    `case_input` (module docstring)."""
    if tier not in TIERS:
        raise AgentReviewError(f"'{tier}' is not a tier; tiers are {', '.join(TIERS)}")
    return ReviewItem(
        agent=RULE_RECOVERY_AGENT,
        tier=tier,
        case_input=case_input,
        draft_text=rule.text,
        citation_text=rule.citation.text,
        citation_link=citation_link(rule.citation),
        expected=(f"rule:{rule.id}",),
    )


# -- AC2: edit keeps the original for comparison -------------------------------------------


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: str
    after: str

    def to_dict(self) -> dict:
        return {"field": self.field, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class Edited:
    original: ReviewItem
    edited: ReviewItem

    def diff(self) -> tuple[FieldChange, ...]:
        changes = []
        for field in ("draft_text", "citation_text", "expected"):
            before, after = getattr(self.original, field), getattr(self.edited, field)
            if before != after:
                changes.append(FieldChange(field, str(before), str(after)))
        return tuple(changes)

    def to_dict(self) -> dict:
        return {"original": self.original.to_dict(), "edited": self.edited.to_dict(), "diff": [c.to_dict() for c in self.diff()]}


def edit_text(item: ReviewItem, text: str) -> Edited:
    """A corrected draft_text -- the rule id (and so `expected`) is unchanged; a rewritten
    citation is a separate function (edit_citation), since the two are independent corrections a
    reviewer might make on their own."""
    return Edited(original=item, edited=replace(item, draft_text=text))


def edit_citation(item: ReviewItem, rule: Rule, citation: Citation) -> Edited:
    """A corrected citation -- rendered the same way rule_recovery_item renders the original, so
    the diff compares like with like."""
    return Edited(original=item, edited=replace(item, citation_text=citation.text, citation_link=citation_link(citation)))


# -- AC3: accept / reject feeds the agent evaluation set automatically --------------------------


def _load_gold_set_for(gold_set_path: Path, item: ReviewItem, root: Path | None) -> GoldSet:
    gold, problems = load_gold_set(Path(gold_set_path), root)
    if problems:
        raise AgentReviewError("; ".join(p.format() for p in problems))
    if gold.agent != item.agent:
        raise AgentReviewError(f"{gold_set_path}: this gold set is for '{gold.agent}', not '{item.agent}'")
    return gold


def accept(gold_set_path: Path, item: ReviewItem, *, case_id: str, root: Path | None = None) -> Case:
    """The draft (as reviewed, possibly edited) is correct: its own `expected` items are recorded
    as the right answer for `case_input`."""
    _load_gold_set_for(gold_set_path, item, root)
    try:
        gold = append_case(Path(gold_set_path), Case(case_id, item.tier, item.case_input, frozenset(item.expected)), root=root)
    except AgentEvalError as exc:
        raise AgentReviewError(str(exc)) from exc
    return gold.case(case_id)


def reject(gold_set_path: Path, item: ReviewItem, *, case_id: str, root: Path | None = None) -> Case:
    """The draft is wrong: the agent should have produced nothing for `case_input` (module
    docstring's own caveat about a citation-scoped input)."""
    _load_gold_set_for(gold_set_path, item, root)
    try:
        gold = append_case(Path(gold_set_path), Case(case_id, item.tier, item.case_input, frozenset()), root=root)
    except AgentEvalError as exc:
        raise AgentReviewError(str(exc)) from exc
    return gold.case(case_id)


# -- the view --------------------------------------------------------------------------


def render_markdown(item: ReviewItem) -> str:
    out = [f"# Agent draft review: {item.agent} ({item.tier})", ""]
    out.append(f"**Input**: {item.case_input}")
    out.append("")
    out.append("## Draft (reasoning)")
    out.append("")
    out.append(item.draft_text)
    out.append("")
    out.append("## Source evidence")
    out.append("")
    out.append(f"{item.citation_text}  —  `{item.citation_link}`")
    out.append("")
    out.append(f"If accepted, records: {', '.join(item.expected) if item.expected else '(nothing)'}")
    out.append("")
    return "\n".join(out)


def render_diff_markdown(edited: Edited) -> str:
    out = ["# Draft edit", ""]
    changes = edited.diff()
    if not changes:
        out += ["No changes.", ""]
        return "\n".join(out)
    out.append("| Field | Original | Edited |")
    out.append("|---|---|---|")
    for c in changes:
        out.append(f"| {c.field} | {c.before} | {c.after} |")
    out.append("")
    return "\n".join(out)
