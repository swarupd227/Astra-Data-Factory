from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_agents.gate_evidence_compiler import (
    NO_EVIDENCE,
    EvidenceSources,
    GateEvidenceCompilerError,
    generate,
    load_approvals,
    record_approval,
    render_markdown,
    run,
    write_pack,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "agents" / "examples" / "gate_evidence_compiler"
DQ = EXAMPLE / "dq_report.json"
PARITY = EXAMPLE / "parity_report.json"
VOLUME = EXAMPLE / "volume_report.json"
CHAOS = EXAMPLE / "chaos_report.json"
DR = EXAMPLE / "dr_report.json"
AGENT_EVAL = EXAMPLE / "agent_eval_report.json"
APPROVALS = EXAMPLE / "approvals.yaml"
RELEASE = "pershing_position-2026-09-13"

FULL_SOURCES = EvidenceSources(dq_report=DQ, parity_report=PARITY, volume_report=VOLUME, chaos_report=CHAOS, dr_report=DR, agent_eval_report=AGENT_EVAL, approvals=APPROVALS)


# ---------------------------------------------------------------- generate() with full evidence


def test_generate_with_full_evidence_is_all_met():
    pack = generate(RELEASE, FULL_SOURCES)
    assert len(pack.criteria) == 7
    assert pack.all_met is True
    assert pack.unmet == ()
    assert all(c.met for c in pack.criteria)


def test_generate_every_criterion_has_a_stable_id():
    pack = generate(RELEASE, FULL_SOURCES)
    ids = [c.id for c in pack.criteria]
    assert ids == ["dq_score", "parity", "volume", "chaos", "dr_drill", "agent_eval", "approval"]


def test_generate_dq_criterion_reads_the_real_fixture():
    pack = generate(RELEASE, FULL_SOURCES)
    dq = next(c for c in pack.criteria if c.id == "dq_score")
    assert dq.met is True
    assert "0.994" in dq.summary and "0.98" in dq.summary


def test_generate_volume_criterion_reads_status_published():
    pack = generate(RELEASE, FULL_SOURCES)
    volume = next(c for c in pack.criteria if c.id == "volume")
    assert volume.met is True and "published" in volume.summary


# ---------------------------------------------------------------- the guardrail: no evidence -> NOT MET


def test_generate_with_no_evidence_at_all_lists_every_criterion_not_met():
    pack = generate(RELEASE, EvidenceSources())
    assert len(pack.criteria) == 7  # never silently missing one
    assert pack.all_met is False
    assert len(pack.unmet) == 7
    for c in pack.criteria:
        assert c.met is False
        assert c.evidence is None
        assert c.summary == NO_EVIDENCE
        assert c.status == "NOT MET"


def test_generate_with_a_nonexistent_path_is_not_met_not_an_error():
    sources = EvidenceSources(dq_report=Path("does/not/exist.json"))
    pack = generate(RELEASE, sources)
    dq = next(c for c in pack.criteria if c.id == "dq_score")
    assert dq.met is False and dq.summary == NO_EVIDENCE


def test_generate_with_a_malformed_json_file_is_not_met_not_a_crash(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    pack = generate(RELEASE, EvidenceSources(dq_report=bad))
    dq = next(c for c in pack.criteria if c.id == "dq_score")
    assert dq.met is False and dq.summary == NO_EVIDENCE


def test_generate_with_partial_evidence_mixes_met_and_not_met():
    pack = generate(RELEASE, EvidenceSources(dq_report=DQ, parity_report=PARITY))
    by_id = {c.id: c for c in pack.criteria}
    assert by_id["dq_score"].met is True
    assert by_id["parity"].met is True
    assert by_id["volume"].met is False
    assert pack.all_met is False


# ---------------------------------------------------------------- approvals


def test_load_approvals_reads_the_real_committed_fixture():
    approvals = load_approvals(APPROVALS)
    assert len(approvals) == 1
    assert approvals[0].release == RELEASE and approvals[0].approver == "steward@example.com"


def test_load_approvals_none_path_is_empty():
    assert load_approvals(None) == ()


def test_load_approvals_missing_file_is_empty(tmp_path):
    assert load_approvals(tmp_path / "missing.yaml") == ()


def test_generate_approval_criterion_met_when_the_release_matches():
    pack = generate(RELEASE, EvidenceSources(approvals=APPROVALS))
    approval = next(c for c in pack.criteria if c.id == "approval")
    assert approval.met is True and "steward@example.com" in approval.summary


def test_generate_approval_criterion_not_met_for_a_different_release():
    pack = generate("a_different_release-2026-01-01", EvidenceSources(approvals=APPROVALS))
    approval = next(c for c in pack.criteria if c.id == "approval")
    assert approval.met is False and approval.summary == NO_EVIDENCE


def test_record_approval_appends_and_is_readable_back(tmp_path):
    path = tmp_path / "approvals.yaml"
    record_approval(path, release="rel-1", approver="a@example.com", note="looks good")
    approvals = load_approvals(path)
    assert len(approvals) == 1
    assert approvals[0].release == "rel-1" and approvals[0].note == "looks good"


def test_record_approval_appends_to_an_existing_log(tmp_path):
    path = tmp_path / "approvals.yaml"
    record_approval(path, release="rel-1", approver="a@example.com")
    record_approval(path, release="rel-2", approver="b@example.com")
    approvals = load_approvals(path)
    assert [a.release for a in approvals] == ["rel-1", "rel-2"]


def test_record_approval_rejects_a_blank_release(tmp_path):
    with pytest.raises(GateEvidenceCompilerError, match="release"):
        record_approval(tmp_path / "a.yaml", release="  ", approver="a@example.com")


def test_record_approval_rejects_a_blank_approver(tmp_path):
    with pytest.raises(GateEvidenceCompilerError, match="approv"):
        record_approval(tmp_path / "a.yaml", release="rel-1", approver="  ")


def test_a_freshly_recorded_approval_makes_the_criterion_met(tmp_path):
    path = tmp_path / "approvals.yaml"
    record_approval(path, release=RELEASE, approver="pm@example.com")
    pack = generate(RELEASE, EvidenceSources(approvals=path))
    approval = next(c for c in pack.criteria if c.id == "approval")
    assert approval.met is True


# ---------------------------------------------------------------- run() / report / files


def test_run_is_the_same_as_generate():
    assert run(RELEASE, FULL_SOURCES).to_dict() == generate(RELEASE, FULL_SOURCES).to_dict()


def test_render_markdown_lists_every_criterion_and_flags_not_met():
    pack = generate(RELEASE, EvidenceSources(dq_report=DQ))
    text = render_markdown(pack)
    assert "Data quality score meets target" in text and "**MET**" in text
    assert "**NOT MET**" in text
    assert "## Not met" in text


def test_render_markdown_reports_ready_to_release_yes_when_all_met():
    pack = generate(RELEASE, FULL_SOURCES)
    text = render_markdown(pack)
    assert "Ready to release: yes" in text


def test_write_pack_writes_report_and_json(tmp_path):
    pack = generate(RELEASE, FULL_SOURCES)
    report_path, data_path = write_pack(pack, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["all_met"] is True and len(data["criteria"]) == 7


# ---------------------------------------------------------------- CLI


def test_cli_run_with_full_evidence(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main([
        "gate-evidence-compiler", "run", "--release", RELEASE,
        "--dq-report", str(DQ), "--parity-report", str(PARITY), "--volume-report", str(VOLUME),
        "--chaos-report", str(CHAOS), "--dr-report", str(DR), "--agent-eval-report", str(AGENT_EVAL),
        "--approvals", str(APPROVALS), "--out", str(out),
    ])
    assert code == 0, capsys.readouterr()
    assert (out / RELEASE / "gate_pack.md").exists()
    text = capsys.readouterr().out
    assert "ready to release: yes" in text


def test_cli_run_with_no_evidence_exits_nonzero(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["gate-evidence-compiler", "run", "--release", RELEASE, "--out", str(out)])
    assert code == 1
    text = capsys.readouterr().out
    assert "ready to release: no" in text
    assert text.count("NOT MET") == 7


def test_cli_record_approval_appends_to_the_log(tmp_path, capsys):
    import astra_agents.cli as cli

    path = tmp_path / "approvals.yaml"
    code = cli.main(["gate-evidence-compiler", "record-approval", "--approvals", str(path), "--release", RELEASE, "--approver", "pm@example.com"])
    assert code == 0, capsys.readouterr()
    assert load_approvals(path)[0].approver == "pm@example.com"


def test_cli_record_approval_reports_a_clear_error_for_a_blank_release(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["gate-evidence-compiler", "record-approval", "--approvals", str(tmp_path / "a.yaml"), "--release", " ", "--approver", "pm@example.com"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S5.11.1: the pack lists each of the seven gate criteria with its own evidence, and a
    criterion given no evidence path at all is shown as NOT MET, never silently omitted."""
    full = generate(RELEASE, FULL_SOURCES)
    assert len(full.criteria) == 7
    for c in full.criteria:
        assert c.evidence is not None  # every criterion has real evidence attached

    empty = generate(RELEASE, EvidenceSources())
    assert len(empty.criteria) == 7  # still every criterion, none silently dropped
    assert all(c.status == "NOT MET" for c in empty.criteria)
    assert all(c.evidence is None for c in empty.criteria)
