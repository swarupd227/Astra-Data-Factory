"""Rule catalog browser and review: recovered rules beside their code citation, confirmed,
rejected or marked a legacy defect with a comment — Stage 1 confirmation as a screen, not a
spreadsheet (S6.3.5, ADR 0061, product spec Section 7.2: "Owners confirm or reject rules in the
catalog; rejected rules are recorded as legacy defects").

Nothing here re-implements a status change. `astra_knowledge.rules.set_status(rule, status, by,
note=None, at=None) -> Rule` already validates the change, appends the rule's own history entry,
and writes the rule's own file — the exact mechanism `astra-spec rules set-status` already
exposes at the Knowledge plane's own CLI (`astra_agents.rule_recovery`'s own docstring already
points a steward there). This module's only job is the screen: presenting a rule beside its
citation (AC1), filtering (AC3's first half), and a bulk operation over rules sharing the same
text (AC3's second half) — each one calling `set_status` once per rule, never reimplementing what
it already does correctly.

**"Filter by rejection code" has no dedicated field on a committed rule today.** A rejection code
only ever lands in a rule's own `tags`, as `rejection-<code>` — `astra_agents.rule_recovery`'s own
convention for a freshly-recovered rule — and no rule committed to this repository yet carries
one (every real committed rule was recovered before that tagging convention existed). This module
filters `tags` for that prefix honestly rather than inventing a schema field this repository does
not have; a rejection-tagged rule will simply show once one has actually been drafted, the same
honest gap this whole plane has already named for a live SSO provider (S6.3.1), a FinOps agent
and a live file-load log (S6.3.3).

**"Identical," for bulk confirm, is this module's own definition** — two rules with exactly the
same `text` (already whitespace-normalized when `astra_knowledge.rules` loads a rule, so the
comparison is stable across formatting differences in the source YAML) — not one this codebase
has established anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from astra_knowledge.rules import STATUSES, Catalog, Rule, StatusError, set_status

from astra_control.diff_review import citation_link

REJECTION_TAG_PREFIX = "rejection-"

# AC2's own three words -- "confirmed, rejected or legacy defect." `astra_knowledge.rules.
# STATUSES` has a fourth, `recovered`, which stays meaningful for filtering (AC3's own "filter by
# ... status") but is never a status this screen itself sets -- reverting a rule to recovered is
# not part of what a steward reviewing the catalog does.
REVIEW_STATUSES = ("confirmed", "rejected", "legacy_defect")


class RuleReviewError(RuntimeError):
    pass


# -- loading -----------------------------------------------------------------


def load_catalog(rules_dir: Path, root: Path | None = None) -> Catalog:
    catalog, problems = Catalog.load(Path(rules_dir), root)
    if problems:
        raise RuleReviewError("; ".join(p.format() for p in problems))
    return catalog


def _rule(catalog: Catalog, rule_id: str) -> Rule:
    rule = catalog.get(rule_id)
    if rule is None:
        raise RuleReviewError(f"no rule '{rule_id}' in this catalog")
    return rule


# -- rejection codes: read from tags, never a fabricated field ----------------------------------


def rejection_codes(rule: Rule) -> tuple[str, ...]:
    return tuple(sorted(t[len(REJECTION_TAG_PREFIX):].upper() for t in rule.tags if t.startswith(REJECTION_TAG_PREFIX)))


# -- AC1: a rule beside its citation --------------------------------------------------------


@dataclass(frozen=True)
class RuleEntry:
    id: str
    text: str
    class_: str
    status: str
    citation_text: str
    citation_link: str
    custodians: tuple[str, ...]
    rejection_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "class": self.class_,
            "status": self.status,
            "citation_text": self.citation_text,
            "citation_link": self.citation_link,
            "custodians": list(self.custodians),
            "rejection_codes": list(self.rejection_codes),
        }


def rule_entry(rule: Rule) -> RuleEntry:
    return RuleEntry(
        id=rule.id,
        text=rule.text,
        class_=rule.class_,
        status=rule.status,
        citation_text=rule.citation.text,
        citation_link=citation_link(rule.citation),
        custodians=rule.custodians,
        rejection_codes=rejection_codes(rule),
    )


# -- AC3, first half: filtering ------------------------------------------------------------


def filter_rules(
    catalog: Catalog,
    *,
    status: str | None = None,
    custodian: str | None = None,
    rejection_code: str | None = None,
) -> tuple[Rule, ...]:
    if status is not None and status not in STATUSES:
        raise RuleReviewError(f"'{status}' is not a status; statuses are {', '.join(STATUSES)}")
    rules = tuple(catalog.by_status(status)) if status else catalog.rules
    if custodian:
        rules = tuple(r for r in rules if custodian in r.custodians)
    if rejection_code:
        code = rejection_code.upper()
        rules = tuple(r for r in rules if code in rejection_codes(r))
    return rules


# -- AC2: status changes -- a thin wrapper over astra_knowledge.rules.set_status ------------------


def change_status(catalog: Catalog, rule_id: str, status: str, *, by: str, note: str | None = None, at: datetime | None = None) -> Rule:
    """Confirms, rejects or marks a rule a legacy defect (AC2's own three words -- STATUSES has a
    fourth, `recovered`, which is only ever a starting status, never one this screen sets). Who,
    when and the comment are exactly `set_status`'s own `by`, `at` and `note` -- this function adds
    no new record-keeping, it only forwards to the one that already exists."""
    if status not in REVIEW_STATUSES:
        raise RuleReviewError(f"'{status}' is not a status this screen sets; choices are {', '.join(REVIEW_STATUSES)}")
    rule = _rule(catalog, rule_id)
    try:
        return set_status(rule, status, by, note, at)
    except StatusError as exc:
        raise RuleReviewError(str(exc)) from exc


# -- AC3, second half: bulk confirm for identical rules ------------------------------------------


def identical_rules(catalog: Catalog, rule_id: str) -> tuple[Rule, ...]:
    """Every OTHER rule in the catalog with exactly the same text as the named rule (module
    docstring's own definition of "identical")."""
    target = _rule(catalog, rule_id)
    return tuple(r for r in catalog.rules if r.id != rule_id and r.text == target.text)


@dataclass(frozen=True)
class BulkResult:
    rule_id: str
    ok: bool
    message: str

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "ok": self.ok, "message": self.message}


def bulk_confirm(catalog: Catalog, rule_id: str, *, by: str, note: str | None = None) -> tuple[BulkResult, ...]:
    """Confirms the named rule and every rule identical to it (AC3's own words -- bulk CONFIRM;
    a bulk reject or bulk legacy-defect is not part of this story and is not built here). Each
    rule's own `set_status` call is independent, so one rule's own validation failure (already
    confirmed, say) never blocks the others -- every rule in the batch is attempted, and the
    result says which succeeded."""
    targets = (rule_id,) + tuple(r.id for r in identical_rules(catalog, rule_id))
    results = []
    for rid in targets:
        try:
            change_status(catalog, rid, "confirmed", by=by, note=note)
            results.append(BulkResult(rid, True, "confirmed"))
        except RuleReviewError as exc:
            results.append(BulkResult(rid, False, str(exc)))
    return tuple(results)


# -- the view --------------------------------------------------------------------------


def render_markdown(entries: tuple[RuleEntry, ...]) -> str:
    out = ["# Rule catalog", ""]
    if not entries:
        out += ["No rules match.", ""]
        return "\n".join(out)
    out.append("| ID | Status | Class | Text | Citation | Open | Custodians |")
    out.append("|---|---|---|---|---|---|---|")
    for e in entries:
        custodians = ", ".join(e.custodians) if e.custodians else "-"
        out.append(f"| {e.id} | {e.status} | {e.class_} | {e.text} | {e.citation_text} | `{e.citation_link}` | {custodians} |")
    out.append("")
    return "\n".join(out)


def render_bulk_result(results: tuple[BulkResult, ...]) -> str:
    out = ["# Bulk confirm", ""]
    out.append("| Rule | Result |")
    out.append("|---|---|")
    for r in results:
        out.append(f"| {r.rule_id} | {'confirmed' if r.ok else 'failed: ' + r.message} |")
    out.append("")
    return "\n".join(out)
