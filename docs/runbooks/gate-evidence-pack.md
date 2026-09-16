# Runbook: assembling and exporting a gate's evidence pack

Story S6.2.4 (ADR 0073). A gate's own criteria, evidence and approvals assembled into one real,
reviewable pack — a gate review runs on this, not a scattered set of tool reports.

## Showing the pack

```bash
astra-control gate-evidence-pack show \
  --gate-pack work/gate-evidence-compiler/pershing_position-2026-09-13/gate_pack.json \
  --approvals releases/pershing_position/gate_approvals.yaml \
  --role pm
```

`--gate-pack` must be a real `gate_pack.json` written by `astra-agents
gate-evidence-compiler run` — every one of its seven criteria is shown exactly as compiled: name,
status (`MET`/`NOT MET`), the compiler's own one-line summary, and its evidence path. `--approvals`
is optional; when given, every real approval recorded for this release in that log is shown in its
own separate section — read fresh, not the pack's own snapshot (see "Deciding" below for what a
mismatch between the two means). Add `--json` for the machine-readable form.

Exit 0 when every criterion is `MET`; exit 1 when at least one is not (a real condition to look at,
the same shape `board show` uses for a stream over its WIP limit); exit 2 for a missing or
unreadable `--gate-pack` or a role the CLI does not recognize.

## Exporting the PDF

```bash
astra-control gate-evidence-pack export \
  --gate-pack work/gate-evidence-compiler/pershing_position-2026-09-13/gate_pack.json \
  --approvals releases/pershing_position/gate_approvals.yaml \
  --out releases/pershing_position/evidence/gate-pack.pdf \
  --role pm
```

Writes a real PDF to `--out` — every criterion's name, status, description, its full evidence
(pretty-printed) and every real approval, approver and note. Same exit codes as `show`, plus exit 2
if `--out` cannot be written.

## Deciding

- **A criterion still shows `NOT MET` for "Release approved" even though `--approvals` lists a
  real approval for this release**: the pack was compiled before that approval was recorded. The
  pack is a snapshot — re-run `astra-agents gate-evidence-compiler run` for this release to pick
  up the approval before treating the gate as cleared.
- **`error: no gate pack found at ...`**: `--gate-pack` does not exist or is not valid JSON — check
  it is a real `gate_pack.json` written by the Gate Evidence Compiler, not a path to its Markdown
  twin (`gate_pack.md`).
- **Approvals section says "No approval recorded for this release yet."**: either `--approvals`
  was not given, or the log has no entry whose `release` matches this pack's own `release` field
  exactly.
- **Evidence looks truncated in the PDF**: very long or deeply nested evidence values wrap onto
  new lines rather than extending past the page margin; the full evidence is still in `--json`
  output if you need the exact structure.

## Notes

- Criteria and approvals are read from two different real files and shown in two separate
  sections, never merged into one status — `astra_control.gate_evidence_pack`'s own module
  docstring explains why the pack's own "approval" criterion can lag a real, already-recorded
  approval.
- This is the first PDF this codebase writes; `reportlab` renders it, and this module's own tests
  verify the real content lands in the bytes by reading them back with `pdfplumber`.
- `gate-evidence-pack show|export` are both reads with no write action; every role, including
  auditor, may run either.
