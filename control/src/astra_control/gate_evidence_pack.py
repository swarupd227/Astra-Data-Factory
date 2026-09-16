"""Gate evidence pack: a gate's own criteria, evidence and approvals assembled into one real,
exportable pack a gate review runs on (S6.2.4, ADR 0073, product spec's own glossary: "Gate | A
named checkpoint with criteria, evidence and approvers").

Reads `gate_pack.json` (`astra_agents.gate_evidence_compiler`'s own `GatePack.to_dict()` shape)
and its own approvals log directly, the same "read the real shape, never import astra_agents"
convention `astra_control.queue.approvals_from` and `astra_control.audit_log.gate_approvals_from`
already established for these exact two files. Criteria are shown exactly as the pack recorded
them — a snapshot at compile time, `astra_agents.gate_evidence_compiler`'s own honest "no evidence
found" wherever a criterion has none — while approvals are read fresh from the live log and shown
in their own section, since a real approval can land after the pack was last compiled; the two are
never conflated, the same "show both, label clearly" shape `astra_control.throughput_metrics`
already established for `agent_eval_weekly` alongside its own acceptance numbers.

The PDF is the first file this codebase actually renders. Unlike `astra_control.golden_viewer`'s
own capture command — composed and shown, never run, because a live capture needs a non-prod
database connection no environment here has — a PDF needs nothing beyond this module's own already
-assembled data, so `render_pdf`/`write_pdf` perform the real export rather than composing a
command for someone else to run.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astra_core.yamlsource import SourceError, load
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle


class GateEvidencePackError(RuntimeError):
    pass


def _read_json(path: Path) -> dict | None:
    """Never raises — a missing or unreadable gate pack means nothing to export, the same shape
    astra_control.queue._read_json already established for this exact file."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_yaml(path: Path) -> dict:
    """Never raises — a missing or unreadable approvals log contributes no approvals, the same
    shape astra_control.audit_log._read_yaml already established for this exact file."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        return load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError):
        return {}


@dataclass(frozen=True)
class CriterionEvidence:
    id: str
    name: str
    description: str
    met: bool
    status: str
    summary: str
    evidence_path: str | None
    evidence: dict[str, Any] | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "met": self.met,
            "status": self.status,
            "summary": self.summary,
            "evidence_path": self.evidence_path,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class GateApprovalRecord:
    approver: str
    at: str
    note: str | None

    def to_dict(self) -> dict:
        return {"approver": self.approver, "at": self.at, "note": self.note}


@dataclass(frozen=True)
class GateEvidencePack:
    release: str
    generated_at: str
    all_met: bool
    criteria: tuple[CriterionEvidence, ...]
    approvals: tuple[GateApprovalRecord, ...]

    def to_dict(self) -> dict:
        return {
            "release": self.release,
            "generated_at": self.generated_at,
            "all_met": self.all_met,
            "criteria": [c.to_dict() for c in self.criteria],
            "approvals": [a.to_dict() for a in self.approvals],
        }


def gate_approvals_for(release: str, path: Path) -> tuple[GateApprovalRecord, ...]:
    """Every real approval recorded for `release` in a gate-evidence-compiler approvals log
    (`astra_agents.gate_evidence_compiler.record_approval`'s own shape), read fresh — not the
    snapshot a `gate_pack.json`'s own "approval" criterion carries."""
    data = _read_yaml(path)
    return tuple(
        GateApprovalRecord(approver=a.get("approver", ""), at=a.get("at", ""), note=a.get("note"))
        for a in data.get("approvals") or ()
        if a.get("release") == release
    )


def build_pack(gate_pack_path: Path, approvals_path: Path | None = None, *, clock=lambda: datetime.now(timezone.utc)) -> GateEvidencePack:
    """Refuses outright when `gate_pack_path` does not exist or is not real JSON — there is
    nothing to assemble a pack from, the same "refuses if missing" shape astra_control.
    git_provenance.build_provenance already established for a required artifact."""
    data = _read_json(gate_pack_path)
    if data is None:
        raise GateEvidencePackError(f"no gate pack found at {gate_pack_path}")
    release = data.get("release", "?")
    criteria = tuple(
        CriterionEvidence(
            id=c.get("id", "?"),
            name=c.get("name", "?"),
            description=c.get("description", ""),
            met=bool(c.get("met")),
            status=c.get("status", "NOT MET"),
            summary=c.get("summary", ""),
            evidence_path=c.get("evidence_path"),
            evidence=c.get("evidence"),
        )
        for c in data.get("criteria") or []
    )
    approvals = gate_approvals_for(release, approvals_path) if approvals_path else ()
    return GateEvidencePack(
        release=release,
        generated_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        all_met=bool(data.get("all_met")),
        criteria=criteria,
        approvals=approvals,
    )


def render_markdown(pack: GateEvidencePack) -> str:
    lines = [f"# Gate evidence pack — {pack.release}", "", f"Generated {pack.generated_at}", "", f"Overall: {'ALL CRITERIA MET' if pack.all_met else 'NOT ALL CRITERIA MET'}", "", "## Criteria", "", "| Criterion | Status | Summary | Evidence |", "|---|---|---|---|"]
    for c in pack.criteria:
        lines.append(f"| {c.name} | {c.status} | {c.summary or '-'} | {c.evidence_path or 'no evidence found'} |")
    lines += ["", "## Approvals", ""]
    if pack.approvals:
        lines += ["| Approver | At | Note |", "|---|---|---|"]
        lines += [f"| {a.approver} | {a.at} | {a.note or '-'} |" for a in pack.approvals]
    else:
        lines.append("No approval recorded for this release yet.")
    return "\n".join(lines) + "\n"


# -- PDF -------------------------------------------------------------------------------------


_STATUS_HEX = {True: "#1a7f37", False: "#cf222e"}


def _evidence_block(evidence: dict[str, Any] | None) -> str:
    if not evidence:
        return "no evidence found"
    raw = json.dumps(evidence, indent=2, sort_keys=True)
    wrapped = []
    for line in raw.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        wrapped.extend(textwrap.wrap(line, width=92, subsequent_indent=" " * (indent + 2)) or [line])
    return "\n".join(wrapped)


def render_pdf(pack: GateEvidencePack) -> bytes:
    """Renders the pack — criteria, evidence and approvals — as real PDF bytes. This is the
    first module in this codebase to actually write one; verified in this module's own tests by
    reading the produced bytes back with `pdfplumber`, already a real dependency here for
    Spec Reader's own input parsing, rather than trusting the writer alone."""
    from io import BytesIO

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch, topMargin=0.75 * inch, bottomMargin=0.75 * inch, title=f"Gate evidence pack — {pack.release}")
    styles = getSampleStyleSheet()
    mono = ParagraphStyle("mono", fontName="Courier", fontSize=7, leading=9)

    story: list = [
        Paragraph(f"Gate evidence pack &mdash; {pack.release}", styles["Title"]),
        Paragraph(f"Generated {pack.generated_at}", styles["Normal"]),
        Paragraph(f"Overall: <b>{'ALL CRITERIA MET' if pack.all_met else 'NOT ALL CRITERIA MET'}</b>", styles["Normal"]),
        Spacer(1, 0.2 * inch),
        Paragraph("Criteria", styles["Heading2"]),
    ]

    table_data = [["Criterion", "Status", "Summary"]] + [[c.name, c.status, c.summary or "-"] for c in pack.criteria]
    table = Table(table_data, colWidths=[2.1 * inch, 0.9 * inch, 3.5 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)

    for c in pack.criteria:
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(f"{c.name} &mdash; <font color='{_STATUS_HEX[c.met]}'>{c.status}</font>", styles["Heading3"]))
        story.append(Paragraph(c.description, styles["Normal"]))
        story.append(Paragraph(f"Evidence path: {c.evidence_path or 'none'}", styles["Normal"]))
        story.append(Preformatted(_evidence_block(c.evidence), mono))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Approvals", styles["Heading2"]))
    if pack.approvals:
        approval_data = [["Approver", "At", "Note"]] + [[a.approver, a.at, a.note or "-"] for a in pack.approvals]
        approval_table = Table(approval_data, colWidths=[2.1 * inch, 1.7 * inch, 2.7 * inch], repeatRows=1)
        approval_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(approval_table)
    else:
        story.append(Paragraph("No approval recorded for this release yet.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()


def write_pdf(pack: GateEvidencePack, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render_pdf(pack))
    return out
