# ADR 0070: A new general approval record, honest about two fields nothing in this codebase has ever tracked

Date: 2026-09-15
Status: Accepted
Story: S6.2.1 Approvals and autonomy levels (E6, F6.2, WBS 2.6.4) — opens feature F6.2

## Context

AC1 ("approval stores user, time, evidence seen, agent version") names a record shape that
sounds like it should already exist — the product spec itself asserts it does, twice ("Every
approval records who, what, the evidence seen, and the agent version," Section 8; "PROVENANCE.json
— inputs, agent versions, models, approvers," Appendix A). It does not. `docs/adr/0067-audit-log-
viewer.md` (S6.3.11) already confirmed, exhaustively, that no dataclass anywhere in `control/`,
`agents/`, or `verification/` has an `agent_version` field, and no approval-shaped record anywhere
stores a pointer to evidence — only free text at best. That story treated S6.2.1 (this one) as the
origin of the vocabulary it was working around, not something it should have waited for — F6.3's
own five approval-shaped flows (`rule_review.change_status`, `agent_review.accept`/`reject`,
`drift_review.approve`, `autonomy_admin.set_level`, `config_studio.request_promotion`) were each
built already, none of them named by the backlog as dependent on S6.2.1, none of them worth
retrofitting now that this story exists.

AC2 ("rejection returns the item to the agent with the comment") names a mechanism this codebase
has never had. Every agent's own CLI (`agents/src/astra_agents/cli.py`) runs stateless, one shot,
from files given on the command line; no agent reads a prior rejection or feedback file as an
input today, and no "rejected, here's why" file convention exists anywhere under `work/`.

## Decision

1. **This is a new, general approval mechanism — not a wrapper or a retrofit.** Nothing in the
   backlog names S6.2.1 as the foundation F6.3's own five flows should have deferred to; each of
   those already has its own accountable-person shape, grounded in its own domain (a rule's own
   history, a gold set's own case, a spec's own change-request log, a guardrails log, a promotion
   log). `astra_control.approvals` is for a draft with no dedicated review flow of its own — a
   sixth shape, not a replacement for the other five.

2. **`agent_version` is optional and caller-supplied, honestly `None` when not given — never
   invented.** The product spec's own text treats "agent version" and "model" as two distinct
   fields (Appendix A: "agent versions, models, approvers"); the only real, reachable thing in
   this codebase — an LLM model name on three agents' own `AnthropicClient` classes
   (`spec_reader`/`rule_recovery`/`modeler`, each with its own per-agent `--model` flag) — is a
   different concept the spec does not conflate with it, and does not exist for 8 of the 11
   draft-producing agents. Fabricating a version string to make this AC look satisfied would be
   worse than naming the gap plainly, the same standard this whole plane has already held.

3. **`evidence_path` is a real file path, checked to exist before the record is written — never a
   fabricated reference.** This mirrors `astra_agents.gate_evidence_compiler.Criterion.
   evidence_path` directly, the one place in this codebase that already generalizes "evidence" as
   a path a reviewer can open, read generically, and never assume the internal shape of. A caller
   claiming evidence that does not exist is refused outright, nothing written — the same standard
   already applied to a blank reason, a blank approver, an unknown level.

4. **The proposing agent's own autonomy level is `astra_control.autonomy_admin.current_level`,
   called directly — not a second lookup, not duplicated logic.** Both modules already live in
   `control/`; the story's own "to see the autonomy level of the agent that proposed" is answered
   by the exact function S6.3.12 already built for exactly this purpose, given an already-loaded
   `tuple[LevelChange, ...]` the caller supplies (no history recorded defaults to L0, the same
   default `astra_agents.guardrails` itself uses).

5. **"Rejection returns the item to the agent" writes a real `rejection.yaml` into the draft's own
   real directory (`work/<agent>/<subject>/`) — not a live agent re-run.** This is the file a
   future agent enhancement could read as feedback; making an agent actually read it would mean
   changing the Agents plane's own input contract, out of bounds for this module the same way
   importing `astra_agents` directly has always been. The comment is required (unlike an
   approval's own optional one) — AC2 names it specifically, and a rejection with no reason for
   the agent to improve on is not useful feedback to "return."

## Consequences

- `approvals approve|reject|show` take the same optional `--role` every command in this plane
  does; `approve`/`reject` are granted to steward alone — this story's own actor, and now the
  plane's busiest write role across five different stories.
- No real committed approvals or rejections log exists anywhere in this repository yet — this
  module's own tests build both against the real, committed `work/spec-reader/pershing_gcus_full/
  2017-07-25/` draft directory (a private copy, never the real one) and the real `report.json`
  it already contains as genuine evidence.
- No rendered approval screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested record shape, evidence
  check, autonomy lookup and draft-return logic a screen would be built on top of. This opens F6.2
  — S6.2.2 through S6.2.5 remain ahead.
