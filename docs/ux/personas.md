# Personas and task flows — draft for validation

Story S6.0.1 (E6, F6.0, WBS 2.6.9). **Status: draft, not yet validated.** This document is prep
material for the two sessions the story actually requires — it is not the validated output the
story's acceptance criteria describe. See [Validation status](#validation-status) for exactly
what is still open.

## What this is, and isn't

The story asks for three things:

1. Six personas documented with their top three tasks.
2. Task flows reviewed against Envestnet's self-servicing draft, with gaps listed.
3. Sign-off from the ops lead and one BSA.

Only the first is something a repository can hold in advance of real people. The second needs
Envestnet's own self-servicing draft, which is not in this repository and which I have not seen;
the third needs an actual ops lead and an actual BSA to read this and say so. Neither can be
produced honestly without them, so neither is claimed here. What follows is the best-grounded
starting draft a UX designer could bring into those two sessions — built from the product spec's
own persona table (Section 3), its end-to-end workflow (Section 7.1) and every real "Who" column
already written into `docs/runbooks/*.md` across the Agents plane this factory has already built
— not from six people's own words, which is what the sessions are for.

## Persona set

The story names six personas — ops reconciler, BSA, steward, engineer, PM, auditor — which refine
(and in BSA's case, add to) the product spec's own six-persona table (Section 3, "Who uses it").
The mapping:

| This story's name | Product spec's own name | Exact match? |
|---|---|---|
| Ops reconciler | Ops / business analyst | Renamed — "reconciler" narrows to the custodial reconciliation work this engagement is actually for |
| BSA | *(not in the spec's persona table)* | New — named three times elsewhere in the spec (below), never given its own table row |
| Steward | Data steward / rule owner | Same |
| Engineer | Data engineer (Artizent or client) | Same |
| PM | Artizent delivery lead | Renamed — this factory's own runbooks already call the equivalent role "Project manager" in four places (below); which name sticks is exactly the kind of thing the real session should settle |
| Auditor | Client sponsor / auditor | Same |

Confirming or correcting this mapping is itself one of the two sessions' jobs, not something
this draft can settle alone.

## The six personas

Each task below is marked **grounded** (cited to the product spec or to a real, already-built
runbook or agent) or **hypothesis** (a reasonable guess this factory's own artifacts do not yet
confirm — flagged, not hidden, so the session can correct it rather than rubber-stamp it).

### Ops reconciler

Spec's own words (Section 3): "Onboards simple and medium sources: profile, review draft,
dry-run, request promotion; handles exceptions with agent suggestions." Must never "raise a
development ticket for a mapping change."

1. **Grounded** — Triage new exceptions using Exception Triage's own suggestions, and record
   accept/reject decisions that feed future confidence ([exception-triage.md](../runbooks/exception-triage.md):
   "Operations user" is the runbook's own "Who" for every exceptions step; `exception-triage
   record-decision` is how a decision is recorded).
2. **Grounded** — Review and promote a simple- or medium-tier source's draft — Profiler findings,
   the Modeler's config draft, a dry-run diff, DQ Generator and Test Generator output — through
   onboarding (product spec Section 7.1: "Ops drops a layout document and sample files on the
   board... Approval promotes to QA").
3. **Grounded** — Reconcile a parity break using the Break Explainer's own report, grouped by
   cause, escalating only what it could not explain (`break_explainer.render_markdown`'s own
   "## Unexplained — needs a person" section, [ADR 0050](../adr/0050-break-explainer.md)).

Task flow (draft):

1. Pick up a queued exception batch or onboarding source from the factory board (S6.1.1, not yet
   built).
2. Exceptions: read `exception-triage run`'s `report.md`; accept or reject each suggestion;
   `record-decision` for the ones with a clear answer.
3. Onboarding: review the Profiler/Modeler/DQ Generator/Test Generator drafts under `work/`;
   approve, or send back with a comment; trigger a dry-run.
4. Parity break: read the Break Explainer's `report.md`; action anything grouped "unexplained";
   confirm and close everything else.

### BSA

Not a row in the spec's own persona table, but named three times: "a BSA can read a config"
(Section 2, product principles); "Steward reviews (simple tier: BSA reviews)" — the onboarding
flow's own explicit branch (Section 7.1); and the Workbench is scoped for "engineers, BSAs and
stewards" (Section 10). Read together, BSA reads like a narrower, compliance-flavored sibling of
Steward, scoped to the lowest-complexity sources — but the spec never says why, and this session's
own artifacts have nothing else to ground beyond these three sentences.

1. **Grounded** — Review and approve a simple-tier source's draft before it promotes to QA, in
   place of the steward (Section 7.1's own branch).
2. **Grounded** — Read the generated config directly, in plain language, as part of that review
   (Section 2's own guardrail: "a BSA can read a config").
3. **Hypothesis — confirm with a real BSA** — Review exceptions or gate evidence with a
   compliance/AML lens for sources carrying regulatory-reporting weight. "BSA" names a real
   compliance role (Bank Secrecy Act analyst) in wealth-management back offices; nothing in this
   repository confirms what, if anything, that role adds beyond the simple-tier review above.

Task flow (draft):

1. Pick up a simple-tier source queued for review, post-dry-run (Section 7.1).
2. Read the rendered config in plain language — no generated code should need opening.
3. Approve, or return with a comment; approval promotes to QA.
4. *(to validate)* Any compliance-specific checklist item a real BSA would additionally apply.

### Steward

Spec's own words: "Confirms recovered rules, approves CDM changes, signs gates." Must never
"reverse-engineer code to find out what a rule does."

1. **Grounded** — Confirm or reject Rule Recovery's draft catalog entries; every entry stays
   `status: recovered` until a steward acts ([rule-recovery.md](../runbooks/rule-recovery.md),
   [ADR 0044](../adr/0044-rule-recovery.md)).
2. **Grounded** — Review a Modeler CDM change request, and decide any rule tagged
   `CONFIRM_WITH_LOADER` ([modeler.md](../runbooks/modeler.md): "who owns a new rule" is the
   runbook's own precondition; [ADR 0045](../adr/0045-modeler.md)).
3. **Grounded, with an open naming question** — Investigate a parity difference Break Explainer
   could not explain ([break-explainer.md](../runbooks/break-explainer.md): "Steward" is that
   runbook's own "Who"), and sign a release's gate — the spec's own words for Steward ("signs
   gates"), though [gate-evidence-compiler.md](../runbooks/gate-evidence-compiler.md) already
   attributes that same step to "Project manager." Both may be true (steward decides, PM records
   it) or this may be a real naming gap; worth asking directly in the session rather than
   resolved here.

Task flow (draft):

1. Review queued Rule Recovery drafts; confirm or reject each entry, with a reason.
2. Review queued Modeler CDM change requests and `CONFIRM_WITH_LOADER`-tagged rules.
3. Investigate unexplained parity breaks from the Break Explainer's report.
4. Review a release's gate pack and record (or hand off) the sign-off.

### Engineer

Spec's own words: "Reviews generated code and config for complex sources; extends patterns and
target profiles; resolves parity breaks the agents cannot." Must never "hand-write a parser, a
mapping or a DQ rule that a pattern already covers."

1. **Grounded** — Review generated code and config for complex-tier sources, where the Modeler's
   own tier assignment keeps the agent at L1 Suggest rather than a higher autonomy level
   ([ADR 0043](../adr/0043-pattern-matcher.md), [ADR 0045](../adr/0045-modeler.md)).
2. **Grounded** — Extend a pattern or target profile when the Pattern Matcher finds a spec too
   unlike anything known and routes it to the architect queue instead of guessing
   ([pattern-matcher.md](../runbooks/pattern-matcher.md)).
3. **Grounded** — Run and read the agent CLIs directly for an onboarding source — DQ Generator,
   Test Generator, Drift Watcher — rather than hand-writing what a pattern already covers
   ([dq-generator.md](../runbooks/dq-generator.md), [drift-watcher.md](../runbooks/drift-watcher.md)).

Task flow (draft):

1. Review a complex-tier source's Modeler draft and its mapping precision/recall report.
2. Take a new-pattern proposal (no family assigned) to the architect queue.
3. Run `dq-generator`/`test-generator`/`drift-watcher` directly for a source under onboarding,
   reading each one's own report.

### PM

Spec calls this role "Artizent delivery lead": "Runs the factory board, pace dial, and agent
autonomy levels." Must never "estimate effort per source by hand." This session's own runbooks
independently used "Project manager" for the same two steps (Gate Evidence Compiler) before this
story existed — the naming mismatch is real and listed in [Known gaps](#known-gaps), not
resolved here.

1. **Grounded** — Assemble and read a release's gate pack, recording approval once every
   criterion is met ([gate-evidence-compiler.md](../runbooks/gate-evidence-compiler.md),
   [ADR 0051](../adr/0051-gate-evidence-compiler.md)).
2. **Grounded** — Record and review autonomy-level changes per (agent, task class), including the
   evidence behind an L3 promotion ([guardrails.md](../runbooks/guardrails.md),
   [ADR 0053](../adr/0053-guardrails.md)).
3. **Grounded** — Run the factory board for a pace/throughput view across sources, instead of
   estimating effort by hand (spec's own words; the board itself is S6.1.1, not yet built).

Task flow (draft):

1. Open the factory board (not yet built) for a pace/throughput view.
2. For a release: gather each tool's report, run `gate-evidence-compiler run`, chase down any
   criterion still NOT MET.
3. Once every criterion is met: `gate-evidence-compiler record-approval`.
4. Monthly (the spec's own cadence for autonomy review): review each agent's task-class level and
   `guardrails set-level` a promotion once its evidence clears the bar.

### Auditor

Spec's own words: "Reads gate evidence, throughput, change history." Must never "trust a status
report without evidence behind it."

1. **Grounded** — Read a release's gate pack and open the tool report each MET criterion cites,
   rather than trusting the pack's own summary line ([ADR 0051](../adr/0051-gate-evidence-compiler.md):
   "gate decisions are made on the pack alone" is the guardrail this exists to prove, not assert).
2. **Grounded** — Inspect the audit trail directly: every approval and decision this factory
   records is an append-only log built for exactly this (`approvals.yaml`, `decisions.yaml`,
   the guardrails `changes.yaml`) — never a status someone typed from memory.
3. **Grounded, pending a screen** — Review throughput and change history once the audit log
   viewer exists (S6.3.11, not yet built) instead of asking someone for a status update — the
   spec's own words for this persona.

Task flow (draft):

1. Open a release's gate pack; for each MET criterion, open the tool report it names.
2. Open the approvals, decisions and guardrails logs for the release or date range in question.
3. *(once S6.3.11 exists)* Browse the audit log viewer directly.

## Known gaps

This repository holds no copy of Envestnet's self-servicing draft, so the task flows above have
not actually been reviewed against it — that review is one of the two sessions' own jobs, not
something this draft can substitute for. The concrete places that review would most likely
confirm, correct or contradict:

- **PM naming**: "Artizent delivery lead" (spec) vs. "Project manager" (this factory's own
  runbooks) — which name, if either, matches how Envestnet actually organizes this work.
- **BSA's scope beyond simple-tier review**: the one hypothesis task above, and whether BSA has
  any task this draft has not anticipated at all.
- **Steward vs. PM on "signs gates"**: whether these are genuinely two steps (steward decides, PM
  records) or a real gap between what the spec says and what this factory's own runbooks built.
- **Ops reconciler's actual daily mix**: how much of a real ops reconciler's day is exception
  triage vs. onboarding vs. reconciliation — this draft has no basis to weight the three tasks
  against each other, only to list them.
- Any task, screen, or persona Envestnet's own draft names that the product spec (and therefore
  this draft) does not mention at all.

## Validation status

| Item | Status |
|---|---|
| Six personas documented with their top three tasks | Draft complete — this document |
| Two sessions held with Envestnet ops and BSAs | **Not held** |
| Task flows reviewed against Envestnet's self-servicing draft | **Not done** — draft not in this repository |
| Gaps listed from that review | **Not done** — see [Known gaps](#known-gaps) for what this draft can anticipate in the meantime, which is not the same thing |
| Sign-off from the ops lead | **Not obtained** |
| Sign-off from one BSA | **Not obtained** |

S6.0.1 stays open until the last four rows are checked off by real people, not by this document.
