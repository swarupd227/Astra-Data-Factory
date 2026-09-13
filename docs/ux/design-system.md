# Design system — components, states, and content rules

Story S6.0.3 (E6, F6.0, WBS 2.6.11). A component library for the workbench — tables, diff view,
status chips, approval bar, filters, forms — documented with their states, so screens are
consistent and quick for a front-end engineer to build. Unlike S6.0.1 and S6.0.2, every
acceptance criterion here is something this repository can satisfy on its own; nothing waits on
a session with Envestnet.

Published, interactive reference: see the workbench-design-system artifact (ask for the link if
you don't have it — `Artifact({action: "list"})` finds it by title). This file is the committed
source of record; the published page is the same content, live and clickable.

## The six components

Each is documented with its states below. The published page shows every state rendered, not
just described.

### Status chips

Five kinds — success, warning, critical, info, neutral — each pairing a distinct icon shape with
a text label, never colour alone. Real vocabularies already built into this factory map onto them
directly:

| Vocabulary | Source | Chips |
|---|---|---|
| Gate criterion | ADR 0051 | MET (success) · NOT MET (critical) |
| Autonomy level | ADR 0053 | L0 Observe (neutral) · L1 Suggest (info) · L2 Prepare (warning) · L3 Act with audit (success) |
| Rejection severity | `domains/custodial/rejections.yaml` | critical · error (warning) · warning (info) |

### Tables

States: **loading** (skeleton rows, no layout shift when real rows replace them), **empty** (a
plain sentence, not a blank rectangle), **error** (the real failure message, not a spinner that
never resolves), and **populated** (sortable headers, row hover, row selection via checkbox — the
selected state uses a left accent bar in addition to a background tint, so it still reads with
colour removed).

### Diff view

One shape for every diff: a gutter column (`+`/`−`/blank) plus a background tint plus, for removed
lines, strikethrough — three independent signals for one meaning. Demonstrated against a real
Modeler mapping going from unmapped to mapped.

### Approval bar

States: **default** (accept primary/filled, reject outlined in the critical colour and labelled,
edit plain — never two same-weight destructive-looking buttons side by side), **confirmed** (a
success chip replaces the buttons, naming who and leaving an undo), and **disabled** (the reason
is written next to the control, not left to opacity alone — demonstrated with guardrails' own L3
evidence bar).

### Filters

States: **none applied** (a single add-filter affordance) and **active** (each filter is its own
removable chip naming exactly what it constrains, plus a live count of how many rows match) — never
a filter bar that gives no way to see what's currently applied.

### Forms

States: **default**, **focused**, **inline validation error** (the field outlines in the critical
colour *and* an icon-and-text message appears directly under it, in the exact words the CLI
already uses), and **disabled**. The published page's form runs guardrails' own real validation
rules live — set level to L3 and submit without evidence to see the actual rejection messages
`astra_agents.guardrails.record_change` raises.

## Colour & meaning

**Rule:** colour is an accelerant, never the only carrier. Every component above pairs colour with
a second and often third independent signal (icon shape, text label, gutter symbol, strikethrough,
position). The published page has a live grayscale toggle over the chip and diff examples — with
colour removed, every example still reads correctly from shape and text alone. That toggle is the
proof; this document states the rule.

## Error messages

**Rule:** every message says what happened and what to do next — the exact field, the exact
value, the exact fix. Not invented copy: the table below is verbatim strings this factory's own
agents already raise today.

| Message | What happened | What to do | Source |
|---|---|---|---|
| `'maybe' is not a decision; decisions are accepted, rejected` | Invalid value | Valid values listed | `exception_triage.py` |
| `a change to L3 needs an acceptance rate >= 80%; evidence gives 50%` | Evidence below the bar | Both numbers shown | `guardrails.py` |
| `exceptions.csv: missing column(s) level` | Malformed input file | File and exact missing column named | `exception_triage.py` |
| `who made the decision must be given (--by)` | Required field blank | Names the exact flag | `exception_triage.py` |
| `reading a PDF needs pdfplumber; pip install 'astra-agents[pdf]'` | Missing dependency | The exact install command | `spec_reader.py` |
| `'FR' is not a DQ rule category; categories are control_total, sign_field, date, key, pairing` | Invalid category | Every valid option listed | `dq_generator.py` |

**Don't:** "Something went wrong. Please try again." — names nothing, fixes nothing.
**Do:** "exceptions.csv: missing column(s) level — add the column and retry."

## Notes

- Palette, type (Archivo for UI, Fragment Mono for data) and component visual language here are
  this project's first production-fidelity design system pass — distinct on purpose from
  S6.0.2's wireframes, which stayed deliberately low-fidelity because their job was finding
  layout problems, not settling visual design.
- No component here is implemented in React yet; this is the HTML/CSS/interaction contract a
  React implementation follows, the same way a design system is usually specified independent of
  a specific framework before component code is written against it.
