"""The exception workflow pattern: states, transitions, owners, history (S7.1.4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path

import pytest

from astra_knowledge.cdm import DomainPack, load_pack
from astra_knowledge.patterns.exception_workflow import (
    INITIAL,
    KINDS,
    RULES,
    STATES,
    TERMINAL,
    TRANSITIONS,
    Event,
    ExceptionStore,
    WorkflowError,
)

REPO = Path(__file__).resolve().parents[2]
CUSTODIAL = REPO / "domains" / "custodial"
T0 = datetime(2026, 9, 19, 9, 0, 0, tzinfo=timezone.utc)

# Real codes of the custodial taxonomy: two whitelisted for auto-resolve, three that are not, one per owner kind.
WHITELISTED = "PRICE_MISSING"  # owner steward
NOT_WHITELISTED = "ACCOUNT_NOT_FOUND"  # owner steward
CUSTODIAN_OWNED = "SECURITY_IDENTIFIER_INVALID"
PLATFORM_OWNED = "FIRM_NOT_FOUND"


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == [], [p.format() for p in problems]
    return pack


class Clock:
    """A clock that advances one minute each time it is read, so every event has its own time."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def store(pack) -> ExceptionStore:
    return ExceptionStore(pack.rejections, clock=Clock())


def _new(store: ExceptionStore, code: str = NOT_WHITELISTED, exception_id: str = "EX1", source_id: str = "pershing_position"):
    return store.raise_exception(source_id, exception_id, code, raised_at=T0)


def _in_state(store: ExceptionStore, state: str, code: str = WHITELISTED):
    """An exception brought to `state` by the one legal route: NEW, or NEW then one move."""
    _new(store, code)
    if state != INITIAL:
        store.transition("pershing_position", "EX1", state, "steward@example.com", "done")
    return store.item("pershing_position", "EX1")


# ---------------------------------------------------------------- the definition


def test_the_states_are_exactly_the_cdms_own_exception_status_codes(pack):
    """Adding a state would be a breaking model change; the workflow must never drift from the model."""
    status = pack.latest.entity("Exception").column("STATUS")
    assert tuple(c.value for c in status.codes) == STATES


def test_every_state_is_defined_and_only_new_has_a_way_out():
    assert set(TRANSITIONS) == set(STATES)
    assert INITIAL == "NEW" and TRANSITIONS["NEW"] == {"RESOLVED", "AUTO_RESOLVED", "DISMISSED"}
    assert TERMINAL == ("RESOLVED", "AUTO_RESOLVED", "DISMISSED")
    assert all(TRANSITIONS[s] == frozenset() for s in TERMINAL)
    assert all(target in STATES for targets in TRANSITIONS.values() for target in targets)


def test_every_rule_is_named_once():
    assert len(set(RULES)) == len(RULES) and KINDS == ("transition", "assign")


# ---------------------------------------------------------------- raising and owners


def test_an_exception_is_raised_new_and_owned_by_its_codes_taxonomy_owner(store):
    item = _new(store, NOT_WHITELISTED)
    assert (item.status, item.owner, item.assignee, item.resolution, item.resolved_by, item.resolved_at) == ("NEW", "steward", None, None, None, None)
    assert _new(store, CUSTODIAN_OWNED, "EX2").owner == "custodian"
    assert _new(store, PLATFORM_OWNED, "EX3").owner == "platform"


def test_a_code_outside_the_taxonomy_cannot_be_raised(store):
    with pytest.raises(WorkflowError, match="not a code in the custodial rejection taxonomy") as excinfo:
        store.raise_exception("pershing_position", "EX1", "NO_SUCH_CODE")
    assert excinfo.value.rule == "unknown_code"


def test_the_same_exception_id_in_two_sources_is_two_exceptions(store):
    store.raise_exception("a", "EX1", NOT_WHITELISTED, raised_at=T0)
    store.raise_exception("b", "EX1", NOT_WHITELISTED, raised_at=T0)
    store.transition("a", "EX1", "RESOLVED", "steward@example.com", "fixed")
    assert store.item("a", "EX1").status == "RESOLVED" and store.item("b", "EX1").status == "NEW"


# ---------------------------------------------------------------- transitions: every pair of states, valid or not


@pytest.mark.parametrize(("current", "target"), list(product(STATES, STATES)))
def test_a_move_succeeds_exactly_when_the_definition_allows_it(pack, current, target):
    """The whole 4x4 matrix: every allowed edge moves, every other edge is refused, changing nothing."""
    store = ExceptionStore(pack.rejections, clock=Clock())
    before = _in_state(store, current)
    events_before = list(store.history)
    if target in TRANSITIONS[current]:
        after = store.transition("pershing_position", "EX1", target, "steward@example.com", "done")
        assert after.status == target
    else:
        with pytest.raises(WorkflowError) as excinfo:
            store.transition("pershing_position", "EX1", target, "steward@example.com", "done")
        assert excinfo.value.rule == "illegal_transition"
        assert store.item("pershing_position", "EX1") == before and store.history == events_before


def test_an_illegal_move_names_where_the_exception_can_go(store):
    _in_state(store, "RESOLVED")
    with pytest.raises(WorkflowError, match=r"is RESOLVED and cannot move to NEW; from RESOLVED it can move to: none"):
        store.transition("pershing_position", "EX1", "NEW", "steward@example.com", "reopen")
    _new(store, exception_id="EX2")
    with pytest.raises(WorkflowError, match=r"is NEW and cannot move to NEW; from NEW it can move to: AUTO_RESOLVED, DISMISSED, RESOLVED"):
        store.transition("pershing_position", "EX2", "NEW", "steward@example.com", "x")


def test_a_terminal_exception_can_never_be_moved_again(store):
    for state in TERMINAL:
        s = ExceptionStore(store.taxonomy, clock=Clock())
        _in_state(s, state)
        for target in STATES:
            with pytest.raises(WorkflowError) as excinfo:
                s.transition("pershing_position", "EX1", target, "steward@example.com", "again")
            assert excinfo.value.rule == "illegal_transition"


# ---------------------------------------------------------------- transitions: the other rules


def test_a_move_records_who_when_from_and_to_and_what_was_done(store):
    _new(store)
    item = store.transition("pershing_position", "EX1", "RESOLVED", " steward@example.com ", " Added the account to the cross-reference. ")
    assert (item.status, item.resolved_by, item.resolution) == ("RESOLVED", "steward@example.com", "Added the account to the cross-reference.")
    assert item.resolved_at == T0 + timedelta(minutes=1)
    (event,) = store.history_of("pershing_position", "EX1")
    assert event == Event("pershing_position", "EX1", NOT_WHITELISTED, "steward", "transition", item.resolved_at, "steward@example.com", "NEW", "RESOLVED", None, "Added the account to the cross-reference.")


def test_an_unknown_state_is_refused(store):
    _new(store)
    with pytest.raises(WorkflowError, match="'CLOSED' is not a state; states are NEW, RESOLVED, AUTO_RESOLVED, DISMISSED") as excinfo:
        store.transition("pershing_position", "EX1", "CLOSED", "steward@example.com", "x")
    assert excinfo.value.rule == "not_a_state"


def test_an_unknown_exception_is_refused(store):
    with pytest.raises(WorkflowError, match="no exception EX9 in source pershing_position") as excinfo:
        store.transition("pershing_position", "EX9", "RESOLVED", "steward@example.com", "x")
    assert excinfo.value.rule == "exception_not_found"
    with pytest.raises(WorkflowError) as excinfo:
        store.assign("pershing_position", "EX9", "ops@example.com", "pm@example.com")
    assert excinfo.value.rule == "exception_not_found"


@pytest.mark.parametrize("by", [None, "", "   "])
def test_a_move_needs_an_actor(store, by):
    _new(store)
    with pytest.raises(WorkflowError) as excinfo:
        store.transition("pershing_position", "EX1", "RESOLVED", by, "done")
    assert excinfo.value.rule == "actor_required"
    assert store.item("pershing_position", "EX1").status == "NEW" and store.history == []


@pytest.mark.parametrize("target", ["RESOLVED", "DISMISSED"])
@pytest.mark.parametrize("resolution", [None, "", "  "])
def test_every_terminal_move_needs_what_was_done_or_why(pack, target, resolution):
    store = ExceptionStore(pack.rejections, clock=Clock())
    _new(store, WHITELISTED)
    with pytest.raises(WorkflowError) as excinfo:
        store.transition("pershing_position", "EX1", target, "steward@example.com", resolution)
    assert excinfo.value.rule == "resolution_required"
    assert store.item("pershing_position", "EX1").status == "NEW"


def test_auto_resolve_needs_a_whitelisted_code_and_a_resolution(store):
    _new(store, NOT_WHITELISTED, "EX1")
    with pytest.raises(WorkflowError, match="ACCOUNT_NOT_FOUND is not whitelisted for auto-resolve") as excinfo:
        store.transition("pershing_position", "EX1", "AUTO_RESOLVED", "exception-triage", "applied")
    assert excinfo.value.rule == "not_whitelisted"
    assert store.item("pershing_position", "EX1").status == "NEW"

    _new(store, WHITELISTED, "EX2")
    with pytest.raises(WorkflowError) as excinfo:
        store.transition("pershing_position", "EX2", "AUTO_RESOLVED", "exception-triage", "")
    assert excinfo.value.rule == "resolution_required"
    done = store.transition("pershing_position", "EX2", "AUTO_RESOLVED", "exception-triage", "Carried the last price forward.")
    assert done.status == "AUTO_RESOLVED" and done.resolved_by == "exception-triage"


def test_a_whitelisted_code_may_still_be_resolved_by_a_person(store):
    _new(store, WHITELISTED)
    assert store.transition("pershing_position", "EX1", "RESOLVED", "steward@example.com", "Obtained a price.").status == "RESOLVED"


def test_the_rules_run_in_a_fixed_order(store):
    """An unknown exception before a bad state before a missing actor before an illegal edge before
    a missing resolution before a code that is not whitelisted -- the order the SQL runs them in."""
    with pytest.raises(WorkflowError) as excinfo:
        store.transition("pershing_position", "EX1", "CLOSED", "", None)
    assert excinfo.value.rule == "exception_not_found"
    _new(store, NOT_WHITELISTED)
    for args, rule in (
        (("CLOSED", "", None), "not_a_state"),
        (("AUTO_RESOLVED", "", None), "actor_required"),
        (("AUTO_RESOLVED", "who", None), "resolution_required"),
        (("AUTO_RESOLVED", "who", "did it"), "not_whitelisted"),
    ):
        with pytest.raises(WorkflowError) as excinfo:
            store.transition("pershing_position", "EX1", *args)
        assert excinfo.value.rule == rule


# ---------------------------------------------------------------- assignment


def test_a_new_exception_can_be_assigned_and_reassigned_and_each_is_history(store):
    _new(store)
    assert store.assign("pershing_position", "EX1", "ops@example.com", "pm@example.com").assignee == "ops@example.com"
    assert store.assign("pershing_position", "EX1", "bsa@example.com", "pm@example.com").assignee == "bsa@example.com"
    first, second = store.history_of("pershing_position", "EX1")
    assert (first.kind, first.assignee, first.actor, first.from_status, first.to_status) == ("assign", "ops@example.com", "pm@example.com", None, None)
    assert (second.assignee, second.owner, second.rejection_code) == ("bsa@example.com", "steward", NOT_WHITELISTED)
    assert first.at < second.at
    assert [i.exception_id for i in store.assigned_to("bsa@example.com")] == ["EX1"] and store.assigned_to("ops@example.com") == []


def test_assigning_to_the_current_assignee_is_refused(store):
    _new(store)
    store.assign("pershing_position", "EX1", "ops@example.com", "pm@example.com")
    with pytest.raises(WorkflowError, match="already assigned to ops@example.com") as excinfo:
        store.assign("pershing_position", "EX1", " ops@example.com ", "pm@example.com")
    assert excinfo.value.rule == "already_assigned" and len(store.history) == 1


def test_a_closed_exception_cannot_be_assigned(pack):
    for state in TERMINAL:
        store = ExceptionStore(pack.rejections, clock=Clock())
        _in_state(store, state)
        with pytest.raises(WorkflowError, match=f"is {state}; only a NEW exception can be assigned") as excinfo:
            store.assign("pershing_position", "EX1", "ops@example.com", "pm@example.com")
        assert excinfo.value.rule == "not_new"


@pytest.mark.parametrize(("assignee", "by", "rule"), [("", "pm@example.com", "assignee_required"), (None, "pm@example.com", "assignee_required"), ("ops@example.com", "  ", "actor_required")])
def test_an_assignment_needs_an_assignee_and_an_actor(store, assignee, by, rule):
    _new(store)
    with pytest.raises(WorkflowError) as excinfo:
        store.assign("pershing_position", "EX1", assignee, by)
    assert excinfo.value.rule == rule and store.history == []


def test_a_closed_exception_leaves_the_assigned_queue(store):
    _new(store)
    store.assign("pershing_position", "EX1", "ops@example.com", "pm@example.com")
    assert len(store.assigned_to("ops@example.com")) == 1
    store.transition("pershing_position", "EX1", "DISMISSED", "ops@example.com", "Duplicate of EX0.")
    assert store.assigned_to("ops@example.com") == []


def test_a_full_life_is_assign_then_one_move_and_history_is_in_order(store):
    _new(store, WHITELISTED)
    store.assign("pershing_position", "EX1", "ops@example.com", "pm@example.com")
    store.transition("pershing_position", "EX1", "RESOLVED", "ops@example.com", "Obtained a current price.")
    events = store.history_of("pershing_position", "EX1")
    assert [e.kind for e in events] == ["assign", "transition"] and events[0].at < events[1].at
    assert all(e.owner == "steward" and e.rejection_code == WHITELISTED for e in events)
