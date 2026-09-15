from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.rules import Citation

from astra_control.rule_review import (
    REVIEW_STATUSES,
    RuleReviewError,
    bulk_confirm,
    change_status,
    filter_rules,
    identical_rules,
    load_catalog,
    rejection_codes,
    render_bulk_result,
    render_markdown,
    rule_entry,
)

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "rules"

QUANTITY_SIGN = "pershing_gcus.quantity_sign"  # real, committed: status confirmed, spec citation
REFRESH_MODE = "pershing_gcus.refresh_mode"  # real, committed: status recovered, spec citation


def _catalog():
    return load_catalog(RULES, root=REPO)


def _copy_catalog(tmp_path: Path) -> Path:
    """A private, writable copy of the real rule catalog -- write tests change status here, never
    against the actual committed files under rules/."""
    dest = tmp_path / "rules"
    shutil.copytree(RULES, dest)
    return dest


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- loading


def test_load_catalog_reads_the_real_rule_catalog():
    catalog = _catalog()
    assert {r.id for r in catalog.rules} == {
        "pershing_gcus.quantity_sign",
        "pershing_gcus.refresh_mode",
        "drip_transaction_example.drip_split",
        "drip_transaction_example.cancel_correct",
    }


def test_load_catalog_rejects_a_bad_dir(tmp_path):
    with pytest.raises(RuleReviewError):
        load_catalog(tmp_path / "missing")


# ---------------------------------------------------------------- rejection codes: tags only, never a fabricated field


def test_rejection_codes_empty_for_every_real_committed_rule():
    """None of the four real rules carry a rejection-<code> tag today (module docstring) -- the
    tagging convention postdates them."""
    catalog = _catalog()
    for rule in catalog.rules:
        assert rejection_codes(rule) == ()


def test_rejection_codes_reads_the_rejection_prefixed_tag():
    catalog = _catalog()
    base = catalog.get(QUANTITY_SIGN)
    tagged = replace(base, tags=base.tags + ("rejection-r017",))
    assert rejection_codes(tagged) == ("R017",)


def test_rejection_codes_ignores_non_rejection_tags():
    catalog = _catalog()
    base = catalog.get(QUANTITY_SIGN)
    assert "sign" in base.tags
    assert rejection_codes(base) == ()


# ---------------------------------------------------------------- AC1: rule beside its citation


def test_rule_entry_for_the_real_confirmed_rule():
    catalog = _catalog()
    entry = rule_entry(catalog.get(QUANTITY_SIGN))
    assert entry.id == QUANTITY_SIGN
    assert entry.status == "confirmed"
    assert entry.class_ == "normalisation"
    assert "quantity" in entry.text.lower()
    assert entry.custodians == ("pershing",)


def test_rule_entry_citation_for_a_real_spec_citation():
    catalog = _catalog()
    entry = rule_entry(catalog.get(QUANTITY_SIGN))
    assert "page 13" in entry.citation_text
    assert entry.citation_link == "specs/pershing_gcus/2017-07-25.yaml#page=13"


def test_rule_entry_citation_for_a_code_citation():
    """No real committed rule has a code citation yet -- Rule Recovery's own drafts land in
    work/rule-recovery/, not the approved catalog (module docstring). Constructed directly from a
    real rule, only the citation swapped, the same technique test_spec_viewer.py already uses for
    a branch no real fixture exercises."""
    catalog = _catalog()
    base = catalog.get(QUANTITY_SIGN)
    code_cited = replace(base, citation=Citation(kind="code", file="legacy/Splitter.java", line=142, repository="pershing-legacy"))
    entry = rule_entry(code_cited)
    assert entry.citation_link == "pershing-legacy/legacy/Splitter.java:142"
    assert "Splitter.java:142" in entry.citation_text


def test_rule_entry_to_dict_round_trips_through_json():
    catalog = _catalog()
    entry = rule_entry(catalog.get(QUANTITY_SIGN))
    data = json.loads(json.dumps(entry.to_dict()))
    assert data["id"] == QUANTITY_SIGN


# ---------------------------------------------------------------- AC3, first half: filtering


def test_filter_by_status_confirmed_finds_exactly_the_one_real_confirmed_rule():
    catalog = _catalog()
    rules = filter_rules(catalog, status="confirmed")
    assert {r.id for r in rules} == {QUANTITY_SIGN}


def test_filter_by_status_recovered_finds_the_other_three():
    catalog = _catalog()
    rules = filter_rules(catalog, status="recovered")
    assert {r.id for r in rules} == {REFRESH_MODE, "drip_transaction_example.drip_split", "drip_transaction_example.cancel_correct"}


def test_filter_by_unknown_status_is_refused():
    catalog = _catalog()
    with pytest.raises(RuleReviewError, match="not a status"):
        filter_rules(catalog, status="approved")


def test_filter_by_custodian_finds_both_pershing_rules():
    catalog = _catalog()
    rules = filter_rules(catalog, custodian="pershing")
    assert {r.id for r in rules} == {QUANTITY_SIGN, REFRESH_MODE}


def test_filter_by_custodian_with_no_match_is_empty():
    catalog = _catalog()
    assert filter_rules(catalog, custodian="fidelity") == ()


def test_filter_by_rejection_code_finds_nothing_in_the_real_catalog():
    catalog = _catalog()
    assert filter_rules(catalog, rejection_code="R017") == ()


def test_filters_combine():
    catalog = _catalog()
    rules = filter_rules(catalog, status="confirmed", custodian="pershing")
    assert {r.id for r in rules} == {QUANTITY_SIGN}
    assert filter_rules(catalog, status="confirmed", custodian="fidelity") == ()


# ---------------------------------------------------------------- AC2: status changes, against a private writable copy


def test_change_status_confirms_a_real_recovered_rule(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)
    rule = catalog.get(REFRESH_MODE)
    assert rule.status == "recovered"

    changed = change_status(catalog, REFRESH_MODE, "confirmed", by="steward@example.com", note="Matches the sample file's header.", at=_at("2026-09-16T09:00:00Z"))

    assert changed.status == "confirmed"
    assert changed.last_change.by == "steward@example.com"
    assert changed.last_change.note == "Matches the sample file's header."
    # AC2: the file on disk records who, when, comment
    reloaded = load_catalog(rules_dir).get(REFRESH_MODE)
    assert reloaded.status == "confirmed"
    assert reloaded.last_change.by == "steward@example.com"


def test_change_status_rejects_and_marks_legacy_defect(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)

    rejected = change_status(catalog, "drip_transaction_example.drip_split", "rejected", by="steward@example.com", at=_at("2026-09-16T09:00:00Z"))
    assert rejected.status == "rejected"

    legacy = change_status(catalog, "drip_transaction_example.cancel_correct", "legacy_defect", by="steward@example.com", note="Superseded by the newer lifecycle rule.", at=_at("2026-09-16T09:00:00Z"))
    assert legacy.status == "legacy_defect"


def test_change_status_refuses_a_status_this_screen_does_not_set(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)
    with pytest.raises(RuleReviewError, match="not a status this screen sets"):
        change_status(catalog, REFRESH_MODE, "recovered", by="steward@example.com")


def test_change_status_unknown_rule_id_is_refused(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)
    with pytest.raises(RuleReviewError, match="no rule"):
        change_status(catalog, "pershing_gcus.does_not_exist", "confirmed", by="steward@example.com")


def test_change_status_forwards_set_status_s_own_validation(tmp_path):
    """Already the target status, or a blank `by`: astra_knowledge.rules.set_status's own
    StatusError, forwarded as a RuleReviewError -- this module adds no new validation of its own
    here (module docstring)."""
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)
    with pytest.raises(RuleReviewError, match="already confirmed"):
        change_status(catalog, QUANTITY_SIGN, "confirmed", by="steward@example.com", at=_at("2026-09-16T09:00:00Z"))
    with pytest.raises(RuleReviewError):
        change_status(catalog, REFRESH_MODE, "confirmed", by="   ", at=_at("2026-09-16T09:00:00Z"))


def test_review_statuses_is_exactly_ac2_s_own_three_words():
    assert REVIEW_STATUSES == ("confirmed", "rejected", "legacy_defect")


# ---------------------------------------------------------------- AC3, second half: identical rules and bulk confirm


def test_identical_rules_is_empty_for_the_real_catalog():
    """No two of the four real rules share text today."""
    catalog = _catalog()
    for rule in catalog.rules:
        assert identical_rules(catalog, rule.id) == ()


def test_identical_rules_finds_rules_sharing_text(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    # a second rule, same group, with quantity_sign's own real text verbatim
    twin_path = rules_dir / "pershing_gcus" / "quantity_sign_ca.yaml"
    original = (rules_dir / "pershing_gcus" / "quantity_sign.yaml").read_text(encoding="utf-8")
    twin_path.write_text(original.replace("id: pershing_gcus.quantity_sign", "id: pershing_gcus.quantity_sign_ca"), encoding="utf-8")

    catalog = load_catalog(rules_dir)
    twins = identical_rules(catalog, QUANTITY_SIGN)
    assert {r.id for r in twins} == {"pershing_gcus.quantity_sign_ca"}
    # symmetric
    assert QUANTITY_SIGN in {r.id for r in identical_rules(catalog, "pershing_gcus.quantity_sign_ca")}


def test_identical_rules_unknown_id_is_refused():
    catalog = _catalog()
    with pytest.raises(RuleReviewError):
        identical_rules(catalog, "pershing_gcus.does_not_exist")


def test_bulk_confirm_confirms_the_named_rule_and_every_twin(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    twin_path = rules_dir / "pershing_gcus" / "quantity_sign_ca.yaml"
    original = (rules_dir / "pershing_gcus" / "quantity_sign.yaml").read_text(encoding="utf-8")
    # start the twin at 'recovered' so both really change status
    twin_text = original.replace("id: pershing_gcus.quantity_sign", "id: pershing_gcus.quantity_sign_ca").replace("status: confirmed", "status: recovered")
    twin_text = twin_text.split("\nhistory:")[0] + "\nhistory:\n  - { status: recovered, by: \"spec-reader\", at: \"2026-09-06T09:00:00Z\" }\n"
    twin_path.write_text(twin_text, encoding="utf-8")

    catalog = load_catalog(rules_dir)
    results = bulk_confirm(catalog, "pershing_gcus.quantity_sign_ca", by="steward@example.com", note="Same rule, second currency.")

    assert {r.rule_id for r in results} == {QUANTITY_SIGN, "pershing_gcus.quantity_sign_ca"}
    twin_result = next(r for r in results if r.rule_id == "pershing_gcus.quantity_sign_ca")
    assert twin_result.ok is True

    # quantity_sign itself was already confirmed -- its own attempt fails, honestly reported, not silently dropped
    original_result = next(r for r in results if r.rule_id == QUANTITY_SIGN)
    assert original_result.ok is False
    assert "already confirmed" in original_result.message

    reloaded = load_catalog(rules_dir)
    assert reloaded.get("pershing_gcus.quantity_sign_ca").status == "confirmed"


def test_bulk_confirm_with_no_twins_confirms_just_the_one_rule(tmp_path):
    rules_dir = _copy_catalog(tmp_path)
    catalog = load_catalog(rules_dir)
    results = bulk_confirm(catalog, REFRESH_MODE, by="steward@example.com")
    assert len(results) == 1
    assert results[0].rule_id == REFRESH_MODE
    assert results[0].ok is True


# ---------------------------------------------------------------- render_*


def test_render_markdown_includes_text_class_and_citation():
    catalog = _catalog()
    entries = tuple(rule_entry(r) for r in filter_rules(catalog))
    text = render_markdown(entries)
    assert "quantity_sign" in text and "normalisation" in text and "Citation" in text


def test_render_markdown_with_no_rules_says_so():
    assert "No rules match." in render_markdown(())


def test_render_bulk_result_shows_ok_and_failed():
    from astra_control.rule_review import BulkResult

    text = render_bulk_result((BulkResult("a", True, "confirmed"), BulkResult("b", False, "already confirmed")))
    assert "confirmed" in text and "failed: already confirmed" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


# ---------------------------------------------------------------- CLI


def test_cli_show_against_the_real_catalog(capsys):
    import astra_control.cli as cli

    code = cli.main(["rule-review", "show", "--rules", str(RULES)])
    assert code == 0, capsys.readouterr()
    text = capsys.readouterr().out
    assert "quantity_sign" in text and "normalisation" in text


def test_cli_show_filters_by_status(capsys):
    import astra_control.cli as cli

    code = cli.main(["rule-review", "show", "--rules", str(RULES), "--status", "confirmed", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["id"] for d in data] == [QUANTITY_SIGN]


def test_cli_show_unknown_status_is_a_clear_error(capsys):
    import astra_control.cli as cli

    code = cli.main(["rule-review", "show", "--rules", str(RULES), "--status", "approved"])
    assert code == 2
    assert "not a status" in capsys.readouterr().err


def test_cli_set_status_against_a_private_copy(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "set-status", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--status", "confirmed", "--by", "steward@example.com", "--note", "Matches the sample."])
    assert code == 0, capsys.readouterr()
    reloaded = load_catalog(rules_dir).get(REFRESH_MODE)
    assert reloaded.status == "confirmed" and reloaded.last_change.by == "steward@example.com"


def test_cli_set_status_refuses_a_status_this_screen_does_not_set(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "set-status", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--status", "recovered", "--by", "steward@example.com"])
    assert code == 2
    assert "not a status this screen sets" in capsys.readouterr().err


def test_cli_bulk_confirm(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "bulk-confirm", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--by", "steward@example.com", "--json"])
    assert code == 0, capsys.readouterr()
    data = json.loads(capsys.readouterr().out)
    assert data == [{"rule_id": REFRESH_MODE, "ok": True, "message": "confirmed"}]


def test_cli_bulk_confirm_with_a_failed_twin_exits_one(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    twin_path = rules_dir / "pershing_gcus" / "quantity_sign_ca.yaml"
    original = (rules_dir / "pershing_gcus" / "quantity_sign.yaml").read_text(encoding="utf-8")
    twin_path.write_text(original.replace("id: pershing_gcus.quantity_sign", "id: pershing_gcus.quantity_sign_ca"), encoding="utf-8")
    # quantity_sign is already confirmed, so bulk-confirming its own twin also (re)attempts it -- refused, reported
    code = cli.main(["rule-review", "bulk-confirm", "--rules", str(rules_dir), "--id", "pershing_gcus.quantity_sign_ca", "--by", "steward@example.com"])
    assert code == 1
    assert "failed: pershing_gcus.quantity_sign_ca is already confirmed" in capsys.readouterr().out


def test_cli_set_status_as_auditor_is_refused(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "set-status", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--status", "confirmed", "--by", "steward@example.com", "--role", "auditor"])
    assert code == 2
    assert "not allowed" in capsys.readouterr().err
    assert load_catalog(rules_dir).get(REFRESH_MODE).status == "recovered"  # refused outright, nothing written


def test_cli_set_status_as_steward_succeeds(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "set-status", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--status", "confirmed", "--by", "steward@example.com", "--role", "steward"])
    assert code == 0, capsys.readouterr()


def test_cli_bulk_confirm_as_bsa_is_refused(tmp_path, capsys):
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "bulk-confirm", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--by", "steward@example.com", "--role", "bsa"])
    assert code == 2


def test_cli_show_as_auditor_succeeds_reads_always_allowed(capsys):
    import astra_control.cli as cli

    code = cli.main(["rule-review", "show", "--rules", str(RULES), "--role", "auditor"])
    assert code == 0, capsys.readouterr()


def test_cli_show_invalid_role_is_refused(capsys):
    import astra_control.cli as cli

    code = cli.main(["rule-review", "show", "--rules", str(RULES), "--role", "wizard"])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_set_status_without_role_is_unchanged(tmp_path, capsys):
    """The retrofit is additive: omitted, --role changes nothing about the command's own
    behaviour (astra_control.permissions' own module docstring)."""
    import astra_control.cli as cli

    rules_dir = _copy_catalog(tmp_path)
    code = cli.main(["rule-review", "set-status", "--rules", str(rules_dir), "--id", REFRESH_MODE, "--status", "confirmed", "--by", "steward@example.com"])
    assert code == 0, capsys.readouterr()


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: text, class and citation rendered together for a real rule. AC2: a status change
    records who, when and a comment, against a private copy so the real catalog is untouched.
    AC3: filter by status/custodian works against the real catalog, and bulk confirm reaches
    every rule sharing the same text."""
    catalog = _catalog()
    entry = rule_entry(catalog.get(QUANTITY_SIGN))
    assert entry.text and entry.class_ and entry.citation_text

    assert {r.id for r in filter_rules(catalog, status="confirmed")} == {QUANTITY_SIGN}
    assert {r.id for r in filter_rules(catalog, custodian="pershing")} == {QUANTITY_SIGN, REFRESH_MODE}

    rules_dir = _copy_catalog(tmp_path)
    write_catalog = load_catalog(rules_dir)
    changed = change_status(write_catalog, REFRESH_MODE, "confirmed", by="steward@example.com", note="AC2 check.", at=_at("2026-09-16T09:00:00Z"))
    assert changed.last_change.by == "steward@example.com" and changed.last_change.note == "AC2 check."

    twin_path = rules_dir / "pershing_gcus" / "quantity_sign_ca.yaml"
    original = (rules_dir / "pershing_gcus" / "quantity_sign.yaml").read_text(encoding="utf-8")
    twin_path.write_text(original.replace("id: pershing_gcus.quantity_sign", "id: pershing_gcus.quantity_sign_ca").replace("status: confirmed", "status: recovered").split("\nhistory:")[0] + "\nhistory:\n  - { status: recovered, by: \"spec-reader\", at: \"2026-09-06T09:00:00Z\" }\n", encoding="utf-8")
    bulk_catalog = load_catalog(rules_dir)
    results = bulk_confirm(bulk_catalog, "pershing_gcus.quantity_sign_ca", by="steward@example.com")
    assert any(r.rule_id == "pershing_gcus.quantity_sign_ca" and r.ok for r in results)
