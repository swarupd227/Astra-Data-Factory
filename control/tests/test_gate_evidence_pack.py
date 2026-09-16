from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import pytest

from astra_control.gate_evidence_pack import (
    CriterionEvidence,
    GateApprovalRecord,
    GateEvidencePack,
    GateEvidencePackError,
    build_pack,
    gate_approvals_for,
    render_markdown,
    render_pdf,
    write_pdf,
)

REPO = Path(__file__).resolve().parents[2]
REAL_GATE_PACK = REPO / "control/examples/queue/gate-evidence-compiler/pershing_position-2026-09-13/gate_pack.json"
REAL_APPROVALS = REPO / "agents/examples/gate_evidence_compiler/approvals.yaml"


def _clock():
    return datetime(2026, 9, 15, tzinfo=timezone.utc)


# ---------------------------------------------------------------- build_pack, against real data


def test_build_pack_against_the_real_committed_example():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    assert pack.release == "pershing_position-2026-09-13"
    assert pack.all_met is False
    assert len(pack.criteria) == 7
    assert pack.generated_at == "2026-09-15T00:00:00Z"


def test_build_pack_reads_every_real_criterion_faithfully():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    dq = next(c for c in pack.criteria if c.id == "dq_score")
    assert dq.met is True
    assert dq.status == "MET"
    assert dq.summary == "score 0.994 (target 0.98)"
    assert dq.evidence_path == "agents\\examples\\gate_evidence_compiler\\dq_report.json"
    assert dq.evidence["score"] == 0.994

    approval = next(c for c in pack.criteria if c.id == "approval")
    assert approval.met is False
    assert approval.status == "NOT MET"
    assert approval.evidence is None
    assert approval.evidence_path is None


def test_build_pack_pairs_the_real_approval_for_this_release():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    assert pack.approvals == (
        GateApprovalRecord(approver="steward@example.com", at="2026-09-13T09:00:00Z", note="Every gate criterion met; approved for release."),
    )


def test_build_pack_without_an_approvals_path_has_no_approvals():
    pack = build_pack(REAL_GATE_PACK, clock=_clock)
    assert pack.approvals == ()


def test_build_pack_refuses_a_missing_gate_pack(tmp_path):
    with pytest.raises(GateEvidencePackError):
        build_pack(tmp_path / "no-such-gate_pack.json", clock=_clock)


def test_build_pack_refuses_malformed_json(tmp_path):
    bad = tmp_path / "gate_pack.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(GateEvidencePackError):
        build_pack(bad, clock=_clock)


# ---------------------------------------------------------------- gate_approvals_for


def test_gate_approvals_for_filters_by_release(tmp_path):
    log = tmp_path / "approvals.yaml"
    log.write_text(
        "approvals:\n"
        '  - { release: "a-2026-01-01", approver: "x@example.com", at: "2026-01-01T00:00:00Z" }\n'
        '  - { release: "b-2026-01-02", approver: "y@example.com", at: "2026-01-02T00:00:00Z" }\n',
        encoding="utf-8",
    )
    result = gate_approvals_for("a-2026-01-01", log)
    assert result == (GateApprovalRecord(approver="x@example.com", at="2026-01-01T00:00:00Z", note=None),)


def test_gate_approvals_for_missing_log_is_empty(tmp_path):
    assert gate_approvals_for("a", tmp_path / "no-such.yaml") == ()


def test_gate_approvals_for_no_match_is_empty(tmp_path):
    log = tmp_path / "approvals.yaml"
    log.write_text('approvals:\n  - { release: "other", approver: "x@example.com", at: "2026-01-01T00:00:00Z" }\n', encoding="utf-8")
    assert gate_approvals_for("a", log) == ()


# ---------------------------------------------------------------- to_dict


def test_to_dict_round_trips():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    data = pack.to_dict()
    assert data["release"] == "pershing_position-2026-09-13"
    assert len(data["criteria"]) == 7
    assert data["approvals"][0]["approver"] == "steward@example.com"


# ---------------------------------------------------------------- render_markdown


def test_render_markdown_shows_every_criterion_and_every_approval():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    text = render_markdown(pack)
    for c in pack.criteria:
        assert c.name in text
    assert "steward@example.com" in text
    assert "NOT ALL CRITERIA MET" in text


def test_render_markdown_with_no_approvals_says_so():
    pack = GateEvidencePack(release="r", generated_at="2026-09-15T00:00:00Z", all_met=True, criteria=(), approvals=())
    assert "No approval recorded for this release yet." in render_markdown(pack)


# ---------------------------------------------------------------- render_pdf / write_pdf, real bytes round-tripped via pdfplumber


def _extract_text(pdf_bytes: bytes) -> str:
    import io

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages)


def test_render_pdf_produces_real_bytes_readable_back():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    pdf_bytes = render_pdf(pack)
    assert pdf_bytes[:4] == b"%PDF"
    text = _extract_text(pdf_bytes)
    assert "pershing_position-2026-09-13" in text
    assert "Data quality score meets target" in text
    assert "steward@example.com" in text
    assert "score 0.994" in text


def test_render_pdf_includes_evidence_not_just_summary():
    """AC: 'Pack exported as PDF with criteria, evidence, approvals' -- the raw evidence, not
    only the compiler's own one-line summary, must actually be in the PDF bytes."""
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    text = _extract_text(render_pdf(pack))
    assert '"business_date": "2026-09-13"' in text
    assert '"match_rate": 0.998' in text


def test_render_pdf_with_no_evidence_says_so():
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    text = _extract_text(render_pdf(pack))
    assert "no evidence found" in text


def test_render_pdf_with_no_approvals_says_so():
    pack = GateEvidencePack(release="r", generated_at="2026-09-15T00:00:00Z", all_met=True, criteria=(), approvals=())
    text = _extract_text(render_pdf(pack))
    assert "No approval recorded for this release yet." in text


def test_write_pdf_writes_a_real_file(tmp_path):
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    out = write_pdf(pack, tmp_path / "nested" / "pack.pdf")
    assert out.is_file()
    assert out.read_bytes()[:4] == b"%PDF"


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC: 'assemble and export the evidence pack for a gate... exported as PDF with criteria,
    evidence, approvals.'"""
    pack = build_pack(REAL_GATE_PACK, REAL_APPROVALS, clock=_clock)
    out = write_pdf(pack, tmp_path / "pershing_position-2026-09-13.pdf")
    assert out.is_file() and out.suffix == ".pdf"

    text = _extract_text(out.read_bytes())
    assert all(c.name in text for c in pack.criteria)  # criteria
    assert '"score": 0.994' in text  # evidence
    assert "steward@example.com" in text  # approvals
