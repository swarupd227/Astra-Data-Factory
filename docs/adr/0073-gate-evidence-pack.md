# ADR 0073: The gate pack's own criteria are a snapshot; the approvals log is read fresh, never conflated

Date: 2026-09-16
Status: Accepted
Story: S6.2.4 Gate evidence pack UI (E6, F6.2, WBS 2.6.7)

## Context

`astra_agents.gate_evidence_compiler` (S5.11.1) already produces exactly what this story needs to
assemble: a `GatePack` — seven named criteria (DQ, parity, volume, chaos, DR, agent evaluation,
approval), each with a real `evidence_path`, the tool's own `evidence` dict, and an honest `MET`/
`NOT MET` status — written as a real `gate_pack.json`. Two Control-plane modules already read this
exact file directly, never importing `astra_agents`: `astra_control.queue.approvals_from` (reads
`criteria`/`release` to decide whether an approval is outstanding) and `astra_control.audit_log.
gate_approvals_from` (reads a separate approvals YAML log — `release`/`approver`/`at`/`note` — to
build an audit trail). This story's own module follows both conventions rather than inventing a
third way to read either file, or importing `astra_agents.gate_evidence_compiler` directly.

One real subtlety, confirmed by reading `gate_evidence_compiler.py` in full: the pack's own
`"approval"` criterion is computed once, when `generate()` runs, by checking whether a matching
`Approval` record already existed *at that time*. A real approval recorded after the pack was last
compiled does not retroactively flip that criterion to `MET` — the pack is a snapshot. The
approvals log itself, by contrast, is always current. A gate evidence pack assembled for a review
should show both honestly: the criteria exactly as compiled (never silently recomputed, since this
module does not reimplement `GATE_CRITERIA`'s own logic and must not pretend to), and every real
approval for the release read fresh from the log, in its own clearly separate section.

No PDF-writing capability exists anywhere in this repository. The one PDF-related dependency
already present (`pdfplumber`, `agents/pyproject.toml`'s own `pdf` extra) reads PDFs — it is what
Spec Reader uses to parse an input layout document — and cannot write one. Unlike `astra_control.
golden_viewer`'s own capture command (composed and shown, never run, because a live capture needs
a non-prod database connection no environment here has), a PDF export needs nothing this
environment doesn't already have once the pack's own data is assembled — so this story adds a real
dependency (`reportlab`) and performs the actual export, rather than composing a command for a
person to run elsewhere.

## Decision

1. **`gate_pack.json` and the approvals log are both read raw**, the identical convention
   `astra_control.queue` and `astra_control.audit_log` already established for these exact two
   files — no new import of `astra_agents.gate_evidence_compiler` anywhere in this module.

2. **Criteria and approvals are two clearly separate sections, never merged.** `GateEvidencePack.
   criteria` carries the pack's own compiled snapshot (`met`, `status`, `summary`, `evidence_path`,
   `evidence`, faithfully copied); `GateEvidencePack.approvals` carries every real approval
   `gate_approvals_for` finds for that release in the live log, independent of what the pack's own
   `"approval"` criterion says. A reviewer sees both, exactly as they are, and draws their own
   conclusion about whether the pack needs recompiling — this module never recomputes `all_met` or
   any criterion's own status to account for a since-recorded approval.

3. **The PDF is real, not composed-and-shown.** `render_pdf`/`write_pdf` use `reportlab` (a new
   dependency, added to `control/pyproject.toml`) to write actual bytes: every criterion's name,
   status, description, evidence path and full evidence (pretty-printed, word-wrapped so it stays
   inside the page), plus every real approval, approver, timestamp and note. Verified in this
   module's own tests by reading the produced bytes back with `pdfplumber` and asserting the real
   content — score, match rate, approver — actually landed in the PDF, rather than trusting the
   writer alone.

4. **`build_pack` refuses outright when `gate_pack.json` is missing or unreadable** — there is
   nothing to assemble a pack from, the same "refuses if missing" shape `astra_control.
   git_provenance.build_provenance` already established for a required artifact. Missing
   approvals, by contrast, are never an error — an unapproved release is a legitimate state to
   show, the same "no evidence, not an error" shape every prior reader of these two files uses.

5. **Both actions are reads, granted to every role.** Assembling and exporting a pack changes
   nothing in this plane's own shared factory state — the same reasoning `throughput-metrics.show/
   export` (S6.2.3) and `audit-log.export` (S6.3.11) already established for a report or a CSV
   written to a caller-given path.

## Consequences

- `gate-evidence-pack show|export` take the same optional `--role` every command in this plane
  does; both are in `READ_ACTIONS`, available to every role including auditor.
- A pack whose criteria still show `NOT MET` for `"approval"` alongside a real approval already in
  the log is not a bug in this module — it is a real, visible signal that the gate pack should be
  recompiled (`astra-agents gate-evidence-compiler run`) to pick up the approval. This module does
  not paper over that gap.
- `reportlab` is now a real runtime dependency of `astra-control`; `pdfplumber` moves from
  "already installed for another package's own extra" to a declared `dev` dependency of
  `astra-control` itself, since this module's own tests now depend on reading a PDF back.
