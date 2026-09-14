# ADR 0055: Config studio is a guided sequence and a promotion log, not a second orchestrator

Date: 2026-09-14
Status: Accepted
Story: S6.1.2 Config studio: build and evaluate (E6, F6.1, WBS 2.6.2)

## Context

The product spec names this exact flow twice: the Ops/BSA persona's own top task ("profile,
review draft, dry-run, request promotion", Section 3) and the onboarding workflow's own review
split ("Steward reviews (simple tier: BSA reviews). Approval promotes to QA, then to a dual-run
in production", Section 7.1). Three of the four steps already have a real, tested owner in this
repository — `astra_agents.profiler.run` needs only local files; `astra_agents.modeler.run` needs
a live Anthropic call; `astra_verification.dryrun.dry_run` needs a live Snowflake sandbox — and
the fourth, "request promotion," has never had a home at all. This story does not get to invent a
fifth way to profile a sample or draft a config; it has to decide what is actually left once
those three are set aside, and it has to decide, precisely, what "no engineer involved" enforces.

## Decision

1. **Config studio never re-runs an agent.** The same reason `astra_agents.gate_evidence_compiler`
   never runs DQ, parity, chaos or DR itself (ADR 0051 point 2) applies here at every one of the
   first three steps: Profiler needs no credentials and could technically be called directly, but
   Modeler needs a live model call and dry-run needs a live sandbox, neither of which this module
   can honestly promise in every environment. Config studio's job is the one thing left once
   those three are set aside: the sequence itself, and its own audit trail.

2. **`start`/`advance` reuse the factory board (S6.1.1) directly — a stricter guided sequence on
   top of it, not a second state machine beside it.** `astra_control.board.move` deliberately
   allows any station to any station (ADR 0054 point 6), because a person with judgement might
   need to send work backward. A BSA's self-service flow should not have that same freedom
   forward: `advance` only accepts the next station in `profile -> draft -> dry_run`, one at a
   time, rejecting a skip with a clear message rather than silently trusting the caller. This
   sequence stops at `dry_run` on purpose — `dual_run` and `cutover` are not reached through
   config studio at all; they follow a promotion actually being granted, a separate, later step.

3. **"Every step is recorded with who and when" is answered by extending the board's own history,
   not by adding a parallel log.** `astra_control.board.Transition` gained an optional `by:
   str | None = None` (a fully backward-compatible field — every existing board test and the
   S6.1.1 example fixture still round-trip unchanged); `add_custodian` and `move` both accept
   `by=` now, and `render_markdown` shows it as a fourth column. Two logs recording the same three
   moves would only risk disagreeing with each other; the board already is the record of what
   happened and when, so it is now also the record of who did it.

4. **`request_promotion` is the one genuinely new event, and it gets the same append-only shape
   this repository already uses for a decision like this** — `astra_agents.gate_evidence_
   compiler`'s `Approval` and `astra_agents.guardrails`'s `LevelChange`: who, when, and a
   free-text note, one entry per request, oldest first. It is not folded into the board itself
   because a promotion *request* is not a station move — the board only reaches `dual_run` after
   someone actually approves and executes the promotion, a step this story does not build.

5. **The guardrail this story is named for: `tier == "simple"` needs only its own requester;
   anything else needs a distinct reviewer, or the request is refused and nothing is written.**
   This is the product spec's own sentence turned into a structural check, not a suggestion a
   caller could route around: `request_promotion` raises `ConfigStudioError` before writing a
   single line when a medium or complex request has no `reviewed_by`. `TIERS = ("simple",
   "medium", "complex")` is duplicated here from `astra_agents.pattern_matcher`'s own closed
   vocabulary (ADR 0043) rather than adding the whole Agents plane as a dependency of the Control
   plane for one tuple of three strings that has not changed since it was introduced.

6. **Hand-written YAML entries here quote free text with `json.dumps`, not a bare `"{value}"`
   wrap.** `astra_agents.guardrails.record_change` and `astra_agents.exception_triage.
   record_decision` both wrap their own free-text fields the simpler way, which is a real,
   latent bug if a reason or a note ever contains a quote or a colon — not triggered so far only
   because nothing has been typed into either that would trigger it. This module's own free-text
   field (`note`) is exactly where that is likely to happen first, so it is written correctly
   here; fixing the older two modules to match is a separate, smaller piece of work, not part of
   this story.

## Consequences

- `control/examples/promotion-requests.yaml` reuses the real S6.1.1 example board unchanged —
  `tableau-gl`, already at `dry_run` there — rather than building a second example fixture from
  scratch; the two example files are meant to be read together.
- No live simple-tier example exists anywhere in this repository (the one real committed source,
  `configs/examples/pershing_position.yaml`, is `tier: medium`) — every test and example here
  uses a plainly-labeled tier value passed directly, not a real spec assigned that tier by Pattern
  Matcher. Nothing here asserts one exists.
- Config studio still has no rendered Workbench screen, no live Postgres and no workflow engine,
  the same honest gap ADR 0054 already named for the factory board — this is the tested domain
  logic and CLI a screen would be built on top of, in the same order every plane in this
  repository has already been built.
