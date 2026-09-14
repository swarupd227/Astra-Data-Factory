# ADR 0058: The queue reads what agents already wrote; it never re-scores anything

Date: 2026-09-14
Status: Accepted
Story: S6.3.2 Home / my queue: build and evaluate (E6, F6.3, WBS 2.6.14)

## Context

The story names four kinds of item by name — "approvals, exceptions assigned, breaks to explain,
drift changes" — and each already has a real, already-built source: Exception Triage (S5.8.1),
Break Explainer (S5.10.1), Drift Watcher (S5.9.1) and the Gate Evidence Compiler (S5.11.1). This
story's own job is not to invent a fifth source or a new scoring model; it is to decide, for each
of the four, exactly which of that tool's own already-computed items counts as "needs a person
today," and to filter the result by the six roles S6.3.1 already built.

## Decision

1. **Every item is read from a `report.json` (or `gate_pack.json`) already on disk, never a
   Python import of the agent that produced it.** The same discipline
   `astra_agents.gate_evidence_compiler` already established (ADR 0051 point 2): this module adds
   no dependency on `astra-agents` at all — `control/pyproject.toml` is unchanged by this story.
   A missing or unreadable report contributes nothing to the queue, never an error, the same "no
   evidence" shape that agent's own `_read_json` already uses.

2. **Which items from each report actually belong on the queue is a real decision per source, not
   "everything in the file":**
   - **Approval**: a gate pack whose own `approval` criterion is NOT MET while every *other*
     criterion IS. A gate pack still missing other evidence is a different problem, for whoever
     owns that evidence — queuing it here as "needs your approval" would be dishonest; nothing is
     actually ready for a decision yet.
   - **Exception**: an Exception Triage suggestion that is not already `auto_apply` — a
     whitelisted, confident suggestion already acted on autonomously needs nobody today. A code
     with no taxonomy entry at all is never `auto_apply` (whitelisted requires a real taxonomy
     entry), so it is included by the same rule without a second, separate check.
   - **Break**: a Break Explainer explanation whose own `explained` is `false` — literally that
     report's own "needs a person" category (ADR 0050); nothing wider.
   - **Drift**: every Drift Watcher finding, unfiltered — that report carries no "already
     resolved" signal to check, so every finding stays a queue item until something downstream
     clears it, honestly reflecting what the report actually says.

3. **Role mapping is traced to each item's own originating story and actor, the same discipline
   S6.3.1 already used for its own action permissions** — approvals to steward and PM (the gate
   pack's own persona-table entries: "signs gates," "records approval"); exceptions to ops and
   BSA (`docs/ux/personas.md`'s own grounded onboarding and exception-triage tasks); breaks to
   steward and ops (both investigate a parity difference per the same document); drift to
   engineer and steward (the product spec's own later S6.3.9 story names both as that review's
   actors). Nothing here is a new mapping invented for this story alone.

4. **"And assignments" has no real source today, and this module says so rather than fabricating
   one.** None of the four report shapes carries a specific assignee — only a role-relevant
   audience. Filtering stays role-only; per-person assignment is later work, once something in
   this factory actually records one (the same honest gap S6.3.1 named for auditor's own "reads
   everything" scope not reaching the Agents plane yet).

5. **"Counts refresh without reload" and "items open the right screen in one click" are UI
   behavior a CLI cannot demonstrate by definition** — every CLI invocation is itself a reload.
   The published `My Queue` artifact is where these two are actually proven: switching role
   re-filters and re-counts client-side, with no navigation; each item expands in place to its
   full detail. No destination screen for any of the four kinds exists yet (S6.2.4, S6.2.5,
   S6.3.7, S6.3.9 are all still backlog) — the artifact says exactly that per item rather than
   linking to a screen that is not real, the same honesty `astra_control.diff_review`'s own
   citation links already practice by pointing only at files that actually exist.

## Consequences

- `control/examples/queue/` holds four real, freshly generated reports — not hand-authored — run
  directly from each agent's own real example fixtures already committed elsewhere in this
  repository (`agents/examples/exception_triage`, `agents/examples/break_explainer`,
  `agents/examples/drift_watcher`, `agents/examples/gate_evidence_compiler`, with `--approvals`
  deliberately omitted from the gate-evidence-compiler run so its own `approval` criterion comes
  back NOT MET against everything else MET — exactly the case this story needed).
- Break Explainer's own real example explains 100% of its differences, so the committed "breaks"
  fixture genuinely produces zero queue items — stated as fact in both the test suite and the
  published artifact, not worked around by inventing an unexplained difference that is not real.
- No live rendering exists for any of the four destination screens; this story's own real
  deliverable is the aggregation, the role filter, and the CLI/artifact pair that prove both work
  today against real data — the same "domain logic and CLI first, screen later" order every
  Control-plane story so far has followed.
