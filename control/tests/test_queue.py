from __future__ import annotations

import json
from pathlib import Path

from astra_control.permissions import Role
from astra_control.queue import (
    KIND_ROLES,
    QueueItemKind,
    QueueSources,
    approvals_from,
    breaks_from,
    build_queue,
    counts_by_kind,
    drift_from,
    exceptions_from,
    for_role,
    render_markdown,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "control" / "examples" / "queue"
EXCEPTION_REPORT = EXAMPLE / "exception-triage" / "report.json"
BREAK_REPORT = EXAMPLE / "break-explainer" / "pershing" / "2026-09-01" / "report.json"
DRIFT_REPORT = EXAMPLE / "drift-watcher" / "pershing_gcus" / "2017-07-25" / "report.json"
GATE_PACK = EXAMPLE / "gate-evidence-compiler" / "pershing_position-2026-09-13" / "gate_pack.json"


# ---------------------------------------------------------------- each source, against real committed fixtures


def test_exceptions_from_the_real_fixture_excludes_the_auto_applying_one():
    items = exceptions_from(EXCEPTION_REPORT)
    data = json.loads(EXCEPTION_REPORT.read_text(encoding="utf-8"))
    assert len(data["suggestions"]) == 9
    auto_applying = sum(1 for s in data["suggestions"] if s["auto_apply"])
    assert auto_applying == 1
    assert len(items) == 9 - auto_applying == 8
    assert all(i.kind is QueueItemKind.EXCEPTION for i in items)


def test_exceptions_from_includes_the_code_with_no_taxonomy_entry():
    items = exceptions_from(EXCEPTION_REPORT)
    assert any("UNKNOWN_LOCAL_CODE" in i.title for i in items)


def test_breaks_from_the_real_fixture_is_empty_because_everything_was_explained():
    """Honest, not a gap in the code: the real Break Explainer example happens to explain 100% of
    its differences, so there is genuinely nothing to queue from it."""
    data = json.loads(BREAK_REPORT.read_text(encoding="utf-8"))
    assert data["explained_rate"] == 1.0
    assert breaks_from(BREAK_REPORT) == ()


def test_breaks_from_a_constructed_unexplained_case():
    import tempfile

    data = {"custodian": "pershing", "business_date": "2026-09-01", "explanations": [
        {"key": ["ACC1"], "field": "MARKET_VALUE", "legacy": "100", "lakehouse": "99", "cause": "unexplained", "explained": False, "rule_id": None, "rule_text": None, "description": "nothing explains this"},
        {"key": ["ACC2"], "field": "PRICE", "legacy": "10", "lakehouse": "10", "cause": "transform", "explained": True, "rule_id": None, "rule_text": None, "description": "explained"},
    ]}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "report.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        items = breaks_from(path)
    assert len(items) == 1
    assert "MARKET_VALUE" in items[0].title


def test_drift_from_the_real_fixture_has_two_findings():
    items = drift_from(DRIFT_REPORT)
    assert len(items) == 2
    assert all(i.kind is QueueItemKind.DRIFT for i in items)
    assert any("record_length" in i.title for i in items)
    assert any("new_code" in i.title for i in items)


def test_approvals_from_the_real_fixture_flags_the_missing_approval():
    items = approvals_from(GATE_PACK)
    assert len(items) == 1
    assert items[0].kind is QueueItemKind.APPROVAL
    assert "pershing_position-2026-09-13" in items[0].title


def test_approvals_from_returns_nothing_when_approval_is_already_met(tmp_path):
    data = {"release": "r", "all_met": True, "criteria": [{"id": "approval", "met": True}, {"id": "dq_score", "met": True}]}
    path = tmp_path / "gate_pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert approvals_from(path) == ()


def test_approvals_from_returns_nothing_when_another_criterion_is_also_unmet(tmp_path):
    """Everything else has to be ready -- an approval that is not actually the only thing left is
    not queued here; it is a different problem for whoever owns the missing evidence."""
    data = {"release": "r", "all_met": False, "criteria": [{"id": "approval", "met": False}, {"id": "dq_score", "met": False}]}
    path = tmp_path / "gate_pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert approvals_from(path) == ()


# ---------------------------------------------------------------- missing / unreadable reports never raise


def test_every_source_function_returns_empty_for_a_missing_file(tmp_path):
    missing = tmp_path / "missing.json"
    assert exceptions_from(missing) == ()
    assert breaks_from(missing) == ()
    assert drift_from(missing) == ()
    assert approvals_from(missing) == ()


def test_every_source_function_returns_empty_for_unreadable_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert exceptions_from(bad) == ()
    assert breaks_from(bad) == ()
    assert drift_from(bad) == ()
    assert approvals_from(bad) == ()


# ---------------------------------------------------------------- build_queue / for_role


def _sources():
    return QueueSources(
        gate_packs=(GATE_PACK,),
        exception_triage_reports=(EXCEPTION_REPORT,),
        break_explainer_reports=(BREAK_REPORT,),
        drift_watcher_reports=(DRIFT_REPORT,),
    )


def test_build_queue_aggregates_all_four_sources():
    items = build_queue(_sources())
    kinds = {i.kind for i in items}
    assert kinds == {QueueItemKind.APPROVAL, QueueItemKind.EXCEPTION, QueueItemKind.DRIFT}  # no BREAK, real fixture explains everything
    assert len(items) == 1 + 8 + 2  # approval + exceptions + drift


def test_for_role_filters_to_only_that_roles_kinds():
    items = build_queue(_sources())
    auditor_items = for_role(items, Role.AUDITOR)
    assert auditor_items == ()  # auditor is in no KIND_ROLES set at all

    ops_items = for_role(items, Role.OPS)
    assert all(i.kind in (QueueItemKind.EXCEPTION, QueueItemKind.BREAK) for i in ops_items)
    assert len(ops_items) == 8  # exceptions only, in this fixture set


def test_for_role_accepts_a_string_role():
    items = build_queue(_sources())
    assert for_role(items, "pm") == for_role(items, Role.PM)


def test_kind_roles_covers_every_kind():
    assert set(KIND_ROLES) == set(QueueItemKind)


# ---------------------------------------------------------------- counts / render


def test_counts_by_kind():
    items = build_queue(_sources())
    counts = counts_by_kind(items)
    assert counts == {"approval": 1, "exception": 8, "drift": 2}


def test_render_markdown_with_no_items_says_so():
    assert "Nothing needs you today." in render_markdown(())


def test_render_markdown_includes_counts_and_role():
    items = build_queue(_sources())
    text = render_markdown(for_role(items, Role.PM), role=Role.PM)
    assert "pm" in text
    assert "1 approval" in text


# ---------------------------------------------------------------- CLI


def test_cli_queue_show_against_the_real_examples(capsys):
    import astra_control.cli as cli

    code = cli.main([
        "queue", "show",
        "--gate-pack", str(GATE_PACK),
        "--exception-report", str(EXCEPTION_REPORT),
        "--break-report", str(BREAK_REPORT),
        "--drift-report", str(DRIFT_REPORT),
    ])
    assert code == 1  # non-empty queue
    text = capsys.readouterr().out
    assert "approval" in text and "exception" in text and "drift" in text


def test_cli_queue_show_filtered_by_role(capsys):
    import astra_control.cli as cli

    code = cli.main([
        "queue", "show",
        "--gate-pack", str(GATE_PACK),
        "--exception-report", str(EXCEPTION_REPORT),
        "--drift-report", str(DRIFT_REPORT),
        "--role", "auditor",
    ])
    assert code == 0  # nothing for auditor
    assert "Nothing needs you today." in capsys.readouterr().out


def test_cli_queue_show_json(capsys):
    import astra_control.cli as cli

    code = cli.main(["queue", "show", "--gate-pack", str(GATE_PACK), "--role", "pm", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data[0]["kind"] == "approval"
    assert data[0]["roles"] == ["pm", "steward"]


def test_cli_queue_show_no_sources_is_empty(capsys):
    import astra_control.cli as cli

    code = cli.main(["queue", "show"])
    assert code == 0
    assert "Nothing needs you today." in capsys.readouterr().out


def test_cli_queue_show_invalid_role(capsys):
    import astra_control.cli as cli

    code = cli.main(["queue", "show", "--role", "wizard"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S6.3.2: the queue is filtered by role (AC1) and every item traces back to a real source a
    person could open (AC3's own server-side half -- the client-side 'one click' is the published
    artifact, not this test)."""
    items = build_queue(_sources())

    # filtered by role
    ops_items = for_role(items, Role.OPS)
    pm_items = for_role(items, Role.PM)
    assert ops_items != pm_items
    assert all(Role.OPS in i.roles for i in ops_items)
    assert all(Role.PM in i.roles for i in pm_items)

    # every item traces back to the real report it came from
    for i in items:
        assert Path(i.source).is_file()
