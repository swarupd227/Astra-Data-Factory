from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_agents.exception_triage import (
    AUTO_APPLY_CONFIDENCE,
    Decision,
    ExceptionRecord,
    ExceptionTriageError,
    acceptance_rate,
    generate,
    load_decisions,
    load_exceptions,
    load_rejections,
    record_decision,
    render_markdown,
    run,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
REJECTIONS = REPO / "domains" / "custodial" / "rejections.yaml"
EXAMPLE = REPO / "agents" / "examples" / "exception_triage"
EXCEPTIONS = EXAMPLE / "exceptions.csv"
DECISIONS = EXAMPLE / "decisions.yaml"


def _taxonomy():
    return load_rejections(REJECTIONS)


def _exceptions():
    return load_exceptions(EXCEPTIONS)


def _exc(id_="E1", code="ACCOUNT_NOT_FOUND", raw_value="ACC1", field_name="account_number", status="NEW") -> ExceptionRecord:
    return ExceptionRecord(id=id_, rejection_code=code, level="record", entity="Account", custodian_id="pershing", field_name=field_name, raw_value=raw_value, message="m", record_key="k", raised_at="2026-01-01T00:00:00Z", status=status)


# ---------------------------------------------------------------- loading


def test_load_rejections_reads_the_real_taxonomy():
    taxonomy = _taxonomy()
    assert taxonomy.code("ACCOUNT_NOT_FOUND") is not None


def test_load_rejections_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(ExceptionTriageError, match="not found"):
        load_rejections(tmp_path / "missing.yaml")


def test_load_exceptions_reads_the_real_committed_fixture():
    records = _exceptions()
    assert len(records) == 12
    assert records[0].id == "EX0001" and records[0].rejection_code == "ACCOUNT_NOT_FOUND"


def test_load_exceptions_raises_for_a_missing_required_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("exception_id,rejection_code\nE1,ACCOUNT_NOT_FOUND\n", encoding="utf-8")
    with pytest.raises(ExceptionTriageError, match="level"):
        load_exceptions(path)


def test_load_exceptions_raises_for_a_blank_exception_id(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("exception_id,rejection_code,level,message,raised_at\n,X,record,m,2026-01-01T00:00:00Z\n", encoding="utf-8")
    with pytest.raises(ExceptionTriageError, match="blank"):
        load_exceptions(path)


def test_load_exceptions_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(ExceptionTriageError, match="not found"):
        load_exceptions(tmp_path / "missing.csv")


def test_load_decisions_none_path_is_empty():
    assert load_decisions(None) == ()


def test_load_decisions_reads_the_real_committed_fixture():
    decisions = load_decisions(DECISIONS)
    assert len(decisions) == 14
    assert decisions[0] == Decision(code="PRICE_STALE", decision="accepted", at="2026-08-01T09:00:00Z", by="ops@example.com")


# ---------------------------------------------------------------- acceptance_rate


def test_acceptance_rate_with_no_history_is_a_neutral_half():
    confidence, accepted, total = acceptance_rate("SOME_CODE", ())
    assert confidence == 0.5 and accepted == 0 and total == 0


def test_acceptance_rate_reflects_the_real_price_stale_track_record():
    decisions = load_decisions(DECISIONS)
    confidence, accepted, total = acceptance_rate("PRICE_STALE", decisions)
    assert (accepted, total) == (8, 9)
    assert confidence == pytest.approx((8 + 1) / (9 + 2))


def test_acceptance_rate_only_counts_the_named_code():
    decisions = (Decision("A", "accepted", "t", "b"), Decision("B", "rejected", "t", "b"))
    confidence, accepted, total = acceptance_rate("A", decisions)
    assert (accepted, total) == (1, 1)


# ---------------------------------------------------------------- record_decision


def test_record_decision_appends_and_is_readable_back(tmp_path):
    path = tmp_path / "decisions.yaml"
    record_decision(path, code="X", decision="accepted", by="steward@example.com")
    decisions = load_decisions(path)
    assert len(decisions) == 1
    assert decisions[0].code == "X" and decisions[0].decision == "accepted" and decisions[0].by == "steward@example.com"


def test_record_decision_appends_to_an_existing_log(tmp_path):
    path = tmp_path / "decisions.yaml"
    record_decision(path, code="X", decision="accepted", by="a@example.com")
    record_decision(path, code="X", decision="rejected", by="b@example.com")
    decisions = load_decisions(path)
    assert len(decisions) == 2
    assert [d.decision for d in decisions] == ["accepted", "rejected"]


def test_record_decision_rejects_an_invalid_decision_value(tmp_path):
    with pytest.raises(ExceptionTriageError, match="maybe"):
        record_decision(tmp_path / "d.yaml", code="X", decision="maybe", by="a@example.com")


def test_record_decision_rejects_a_blank_by(tmp_path):
    with pytest.raises(ExceptionTriageError, match="by"):
        record_decision(tmp_path / "d.yaml", code="X", decision="accepted", by="   ")


# ---------------------------------------------------------------- generate() / grouping


def test_generate_groups_exceptions_sharing_the_same_raw_value():
    taxonomy = _taxonomy()
    exceptions = (_exc("E1", raw_value="ACC1"), _exc("E2", raw_value="ACC1"), _exc("E3", raw_value="ACC2"))
    draft = generate(exceptions, taxonomy)
    assert len(draft.suggestions) == 2
    grouped = next(s for s in draft.suggestions if s.count == 2)
    assert set(grouped.exception_ids) == {"E1", "E2"}


def test_generate_falls_back_to_field_name_when_raw_value_is_blank():
    taxonomy = _taxonomy()
    exceptions = (_exc("E1", raw_value=None, field_name="account_number"), _exc("E2", raw_value=None, field_name="account_number"))
    draft = generate(exceptions, taxonomy)
    assert len(draft.suggestions) == 1 and draft.suggestions[0].count == 2


def test_generate_suggestion_resolution_is_the_taxonomys_own_text_verbatim():
    taxonomy = _taxonomy()
    draft = generate((_exc(),), taxonomy)
    expected = taxonomy.code("ACCOUNT_NOT_FOUND").resolution
    assert draft.suggestions[0].resolution == expected


def test_generate_ignores_exceptions_not_in_new_status():
    taxonomy = _taxonomy()
    exceptions = (_exc("E1", status="NEW"), _exc("E2", status="RESOLVED"))
    draft = generate(exceptions, taxonomy)
    assert draft.suggestions[0].count == 1 and draft.suggestions[0].exception_ids == ("E1",)


def test_generate_reports_a_code_with_no_taxonomy_entry_honestly():
    taxonomy = _taxonomy()
    draft = generate((_exc("E1", code="MADE_UP_CODE", raw_value=None, field_name=None),), taxonomy)
    assert draft.suggestions[0].resolution is None
    assert draft.suggestions[0].has_resolution is False
    assert "MADE_UP_CODE" in draft.unresolved_codes
    assert draft.ok is False


# ---------------------------------------------------------------- auto_apply gate


def test_auto_apply_requires_both_whitelisting_and_confidence():
    taxonomy = _taxonomy()
    high_confidence = tuple(Decision("PRICE_STALE", "accepted", "t", "b") for _ in range(20))
    draft = generate((_exc("E1", code="PRICE_STALE", raw_value=None, field_name=None),), taxonomy, high_confidence)
    suggestion = draft.suggestions[0]
    assert suggestion.whitelisted is True
    assert suggestion.confidence >= AUTO_APPLY_CONFIDENCE
    assert suggestion.auto_apply is True


def test_auto_apply_is_false_when_whitelisted_but_confidence_has_not_earned_it():
    taxonomy = _taxonomy()
    draft = generate((_exc("E1", code="PRICE_MISSING", raw_value=None, field_name=None),), taxonomy)  # no history: confidence 0.5
    suggestion = draft.suggestions[0]
    assert suggestion.whitelisted is True
    assert suggestion.confidence < AUTO_APPLY_CONFIDENCE
    assert suggestion.auto_apply is False


def test_auto_apply_is_false_when_not_whitelisted_even_with_perfect_confidence():
    taxonomy = _taxonomy()
    perfect = tuple(Decision("ACCOUNT_NOT_FOUND", "accepted", "t", "b") for _ in range(20))
    draft = generate((_exc("E1", code="ACCOUNT_NOT_FOUND"),), taxonomy, perfect)
    suggestion = draft.suggestions[0]
    assert suggestion.confidence > AUTO_APPLY_CONFIDENCE
    assert suggestion.whitelisted is False
    assert suggestion.auto_apply is False


def test_acceptance_by_code_is_reported_even_for_a_code_with_no_current_exceptions():
    """Acceptance rate is tracked per code, not just per suggestion in this run."""
    taxonomy = _taxonomy()
    decisions = (Decision("SOME_OTHER_CODE", "accepted", "t", "b"),)
    draft = generate((_exc(),), taxonomy, decisions)
    assert "SOME_OTHER_CODE" in draft.acceptance_by_code


# ---------------------------------------------------------------- run() / report / files


def test_run_reads_all_three_files_from_disk():
    draft = run(EXCEPTIONS, REJECTIONS, DECISIONS)
    assert len(draft.suggestions) == 9
    assert draft.ok is False  # UNKNOWN_LOCAL_CODE has no taxonomy entry


def test_render_markdown_reports_auto_apply_and_unresolved():
    draft = run(EXCEPTIONS, REJECTIONS, DECISIONS)
    text = render_markdown(draft)
    assert "PRICE_STALE" in text and "**yes**" in text
    assert "## Codes with no taxonomy entry" in text and "UNKNOWN_LOCAL_CODE" in text


def test_write_draft_writes_report_and_json(tmp_path):
    draft = run(EXCEPTIONS, REJECTIONS, DECISIONS)
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert any(s["auto_apply"] for s in data["suggestions"])


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_real_fixture(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["exception-triage", "run", "--exceptions", str(EXCEPTIONS), "--rejections", str(REJECTIONS), "--decisions", str(DECISIONS), "--out", str(out)])
    assert code == 1, capsys.readouterr()  # UNKNOWN_LOCAL_CODE is unresolved
    assert (out / "report.md").exists()
    text = capsys.readouterr().out
    assert "9 root cause(s)" in text and "1 eligible to auto-apply" in text


def test_cli_run_without_decisions_defaults_every_code_to_50_percent(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["exception-triage", "run", "--exceptions", str(EXCEPTIONS), "--rejections", str(REJECTIONS), "--out", str(out)])
    assert code == 1
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert all(s["confidence"] == 0.5 for s in data["suggestions"])
    assert not any(s["auto_apply"] for s in data["suggestions"])  # nothing has earned it yet


def test_cli_reports_a_start_error_for_a_missing_exceptions_file(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["exception-triage", "run", "--exceptions", str(tmp_path / "missing.csv"), "--rejections", str(REJECTIONS), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_record_decision_appends_to_the_log(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "decisions.yaml"
    code = cli.main(["exception-triage", "record-decision", "--decisions", str(path), "--code", "ACCOUNT_NOT_FOUND", "--decision", "accepted", "--by", "steward@example.com"])
    assert code == 0, capsys.readouterr()
    assert path.exists()
    assert load_decisions(path)[0].code == "ACCOUNT_NOT_FOUND"


def test_cli_record_decision_reports_a_clear_error_for_a_bad_decision(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["exception-triage", "record-decision", "--decisions", str(tmp_path / "d.yaml"), "--code", "X", "--decision", "maybe", "--by", "a@example.com"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S5.8.1: a suggestion is produced for ACCOUNT_NOT_FOUND, SECURITY_NOT_FOUND (the backlog's
    "NEW_SECURITY") and TRANSACTION_CODE_UNMAPPED (the backlog's "MISSING_TX_CODE") -- the real
    taxonomy codes those backlog names refer to (ADR 0048); acceptance rate is tracked per code;
    and only a whitelisted class with a confidence that has actually earned it auto-applies."""
    draft = run(EXCEPTIONS, REJECTIONS, DECISIONS)
    named = {"ACCOUNT_NOT_FOUND", "SECURITY_NOT_FOUND", "TRANSACTION_CODE_UNMAPPED"}
    covered = {s.rejection_code for s in draft.suggestions if s.has_resolution}
    assert named <= covered

    assert set(draft.acceptance_by_code) >= {"PRICE_STALE", "PRICE_MISSING"}

    auto = {s.rejection_code for s in draft.auto_apply_suggestions}
    assert auto == {"PRICE_STALE"}  # whitelisted and earned; PRICE_MISSING whitelisted but has not
    assert not (named & auto)  # none of the three named codes are whitelisted at all
