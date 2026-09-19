"""Pattern: the exception workflow -- where an exception may move, who owns it, and the record of every move.

An exception (the CDM's Exception entity; one row per rejection, written NEW by a pipeline stage)
is worked by a person or, for a whitelisted code, by a rule. The states are the CDM's own
`Exception.STATUS` codes, unchanged -- adding one would be a breaking model change, and every
consumer already reads them (Exception Triage works only on NEW; the ageing report counts NEW):

  NEW            written by a pipeline stage, waiting for triage
  RESOLVED       resolved by a person
  AUTO_RESOLVED  resolved by a whitelisted rule, with audit
  DISMISSED      closed without a change

An exception leaves NEW exactly once, to one of the other three; those are terminal. Every move
needs an actor and the explanation of what was done (or why it was dismissed), and AUTO_RESOLVED
is open only to a code the taxonomy whitelists (`auto_resolve`, the field whose schema text names
Exception Triage). Every move is kept: who, when, from and to.

Ownership has two parts. The owner is the responsible party the taxonomy already names for the
code (custodian, steward, data_engineer, platform), fixed when the exception is raised. The
assignee is a person, set and changed while the exception is NEW; a closed exception cannot be
assigned. Assignments are history events too.

An invalid move is refused outright, nothing changed and nothing recorded, with the name of the
rule it broke. The rendered SQL (generation plane, `astra_data.exception_store`) enforces the same
rules in the same order from these same constants; this module is what it is checked against.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from astra_knowledge.rejections import Taxonomy

STATES = ("NEW", "RESOLVED", "AUTO_RESOLVED", "DISMISSED")
INITIAL = "NEW"
TRANSITIONS: dict[str, frozenset[str]] = {
    "NEW": frozenset({"RESOLVED", "AUTO_RESOLVED", "DISMISSED"}),
    "RESOLVED": frozenset(),
    "AUTO_RESOLVED": frozenset(),
    "DISMISSED": frozenset(),
}
TERMINAL = tuple(s for s in STATES if not TRANSITIONS[s])
KINDS = ("transition", "assign")

# Every way a move or an assignment is refused, named. The order is the order the checks run in.
# The rendered SQL declares one exception per name, numbered by position.
RULES = (
    "exception_not_found",
    "not_a_state",
    "actor_required",
    "illegal_transition",
    "resolution_required",
    "not_whitelisted",
    "assignee_required",
    "not_new",
    "already_assigned",
)
TWIN_ONLY_RULES = ("unknown_code",)


class WorkflowError(ValueError):
    """A move or an assignment that the workflow refuses. `rule` names the rule it broke."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(message)


@dataclass(frozen=True)
class ExceptionItem:
    source_id: str
    exception_id: str
    rejection_code: str
    owner: str
    raised_at: datetime
    status: str = INITIAL
    assignee: str | None = None
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class Event:
    """One row of the history. A transition has a from and a to status; an assignment has the
    assignee it set. `note` carries the explanation of a transition."""

    source_id: str
    exception_id: str
    rejection_code: str
    owner: str
    kind: str
    at: datetime
    actor: str
    from_status: str | None = None
    to_status: str | None = None
    assignee: str | None = None
    note: str | None = None


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


@dataclass
class ExceptionStore:
    """The exceptions of a domain pack with their workflow, in memory."""

    taxonomy: Taxonomy
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    items: dict[tuple[str, str], ExceptionItem] = field(default_factory=dict)
    history: list[Event] = field(default_factory=list)

    def raise_exception(self, source_id: str, exception_id: str, rejection_code: str, raised_at: datetime | None = None) -> ExceptionItem:
        """What a pipeline stage does: write the exception NEW, owned by the code's taxonomy owner."""
        code = self.taxonomy.code(rejection_code)
        if code is None:
            raise WorkflowError("unknown_code", f"'{rejection_code}' is not a code in the {self.taxonomy.domain} rejection taxonomy")
        item = ExceptionItem(source_id, exception_id, rejection_code, code.owner, raised_at or self.clock())
        self.items[(source_id, exception_id)] = item
        return item

    def item(self, source_id: str, exception_id: str) -> ExceptionItem:
        item = self.items.get((source_id, exception_id))
        if item is None:
            raise WorkflowError("exception_not_found", f"no exception {exception_id} in source {source_id}")
        return item

    def transition(self, source_id: str, exception_id: str, to: str, by: str, resolution: str | None = None) -> ExceptionItem:
        """Move an exception out of NEW. Refused, changing nothing, if the move breaks a rule."""
        item = self.item(source_id, exception_id)
        if to not in STATES:
            raise WorkflowError("not_a_state", f"'{to}' is not a state; states are {', '.join(STATES)}")
        if _blank(by):
            raise WorkflowError("actor_required", "who is making the move must be given")
        if to not in TRANSITIONS[item.status]:
            allowed = ", ".join(sorted(TRANSITIONS[item.status])) or "none"
            raise WorkflowError("illegal_transition", f"exception {exception_id} is {item.status} and cannot move to {to}; from {item.status} it can move to: {allowed}")
        if _blank(resolution):
            raise WorkflowError("resolution_required", f"moving to {to} needs what was done, or why it was dismissed")
        code = self.taxonomy.code(item.rejection_code)
        if to == "AUTO_RESOLVED" and not (code and code.auto_resolve):
            raise WorkflowError("not_whitelisted", f"{item.rejection_code} is not whitelisted for auto-resolve in the {self.taxonomy.domain} taxonomy")
        at = self.clock()
        updated = replace(item, status=to, resolution=resolution.strip(), resolved_by=by.strip(), resolved_at=at)
        self.items[(source_id, exception_id)] = updated
        self.history.append(Event(source_id, exception_id, item.rejection_code, item.owner, "transition", at, by.strip(), item.status, to, None, resolution.strip()))
        return updated

    def assign(self, source_id: str, exception_id: str, assignee: str, by: str) -> ExceptionItem:
        """Give a NEW exception to a person, or to a different one. Refused if it is already closed."""
        item = self.item(source_id, exception_id)
        if _blank(by):
            raise WorkflowError("actor_required", "who is making the assignment must be given")
        if _blank(assignee):
            raise WorkflowError("assignee_required", "who the exception is assigned to must be given")
        if item.status != INITIAL:
            raise WorkflowError("not_new", f"exception {exception_id} is {item.status}; only a NEW exception can be assigned")
        if item.assignee == assignee.strip():
            raise WorkflowError("already_assigned", f"exception {exception_id} is already assigned to {assignee.strip()}")
        at = self.clock()
        updated = replace(item, assignee=assignee.strip())
        self.items[(source_id, exception_id)] = updated
        self.history.append(Event(source_id, exception_id, item.rejection_code, item.owner, "assign", at, by.strip(), assignee=assignee.strip()))
        return updated

    def history_of(self, source_id: str, exception_id: str) -> list[Event]:
        return [e for e in self.history if e.source_id == source_id and e.exception_id == exception_id]

    def assigned_to(self, assignee: str) -> list[ExceptionItem]:
        """The NEW exceptions currently assigned to a person."""
        return [i for i in self.items.values() if i.assignee == assignee and i.status == INITIAL]
