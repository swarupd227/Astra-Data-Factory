# Wireframes and clickable prototype

Story S6.0.2 (E6, F6.0, WBS 2.6.10). Clickable wireframes for the four high-traffic screens named
in the story: the exception UI (S6.2.5), suggestion review (S6.3.6), the custodian page (S6.3.3)
and the run status dashboard (S6.3.8) — each with empty, loading, error and success states,
grounded in those screens' own backlog field lists rather than invented layout.

Published, interactive: the workbench-prototype artifact (ask for the link if you don't have it —
`Artifact({action: "list"})` finds it by title "Workbench Wireframes"). This repository holds no
separate markdown copy of the wireframes themselves — the prototype's own HTML/CSS/JS is the
source, versioned only on the published page, not duplicated here as a second, driftable copy.
This file exists so later work (S6.0.3's design system, S6.0.4's accessibility check) has a real
path to cite instead of a dangling reference.

## Scope

- **AC2, "each screen has empty, loading, error and success states drawn"**: done — all sixteen
  combinations (four screens × four states) are built and switchable from the prototype's own
  control bar.
- **AC1, "prototype walked through with three Envestnet users; findings logged"**: not done —
  needs real users, the same constraint as S6.0.1. The prototype ships a working findings log (a
  shared database behind the "Log a finding" drawer) ready to capture a real walkthrough's
  output; nothing has been logged to it yet because no walkthrough has happened.

## Notes

- Deliberately wireframe-fidelity (boxes, dashed placeholders, annotation labels), not final
  visual design — the story's own purpose is finding layout problems before code, not settling
  colour and type. S6.0.3's design system is where production-fidelity visual language lives.
- Error-state copy reuses this factory's own real error strings (the compiler's mapping error,
  the exceptions CSV column check, a missing `ANTHROPIC_API_KEY`) rather than placeholder text.
- Colour tokens were audited for WCAG AA contrast as part of S6.0.4; see
  [accessibility-check.md](accessibility-check.md).
