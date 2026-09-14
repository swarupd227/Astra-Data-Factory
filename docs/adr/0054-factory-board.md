# ADR 0054: The WIP limit gates entry into a stream, not a lateral move inside it

Date: 2026-09-14
Status: Accepted
Story: S6.1.1 Factory board: build and evaluate (E6, F6.1, WBS 2.6.1)

## Context

This is the first story of the Control plane application (E6) — the first code under a new
top-level `control/` package, `astra-control`, sibling to `core`, `knowledge`, `generation`,
`verification` and `agents`. The product spec names the target architecture (Section 5): "Control
plane | Python services; Postgres for state... | Durable, replayable workflows; nothing lives
only in memory" and "Workbench | React web app: factory board, review diffs, approvals, metrics,
evidence packs | One screen for engineers, BSAs and stewards." Neither Postgres nor the React
workbench exists yet in this repository; this story builds the one piece that can exist honestly
before either does — the board's own domain logic, tested and correct, with a real, working
stand-in for its eventual Postgres-backed storage.

Two things the story's three acceptance criteria leave for this ADR to settle, because the
backlog states them as facts (five named stations, a WIP limit per stream) without saying what a
"stream" is or exactly when a limit blocks a move:

## Decision

1. **Five stations, closed, the backlog's own list, never extended.** `profile`, `draft`,
   `dry_run`, `dual_run`, `cutover` — `astra_control.board.Station`, a five-member `Enum`. This
   is the product spec's own onboarding flow (Section 7.1) collapsed to the board's coarser view:
   Spec Reader/Profiler (`profile`), Modeler's draft config (`draft`), the sandbox dry-run
   (`dry_run`), the production dual-run phase (`dual_run`), and go-live (`cutover`).

2. **A stream is free text, one level up from family — the portfolio-level grouping a WIP limit
   actually gates.** The product spec's own release-order text works at exactly this level: "the
   first family in each stream is chosen by value" (Section 7.1) implies a stream holds several
   families, most plausibly a client engagement ("envestnet-custodial", "blackrock-tableau",
   "unfcu-fabric", ...) — the same three names Section 1's own "why now" paragraph lists. Free
   text, not a closed vocabulary the way `Station` is: an architect names a stream as an
   engagement arrives, the same reason `astra_agents.guardrails`'s own `task_class` and
   `astra_agents.exception_triage`'s own rejection-code `owner` are free strings rather than a
   fixed enum — the universe of engagements is not something this module can enumerate in advance.

3. **Every station short of `cutover` counts as work in flight; `cutover` is done.** `AC3`,
   "custodians live per week," only makes sense as a count of custodians that have crossed out of
   the in-flight set — a custodian that has never left `profile` is not yet "live" by any
   reading. `IN_FLIGHT_STATIONS` is therefore the other four, and `wip_count` sums a stream's
   cards at any of them.

4. **The WIP limit gates entry into a stream's in-flight set, never a lateral move already inside
   it.** `move` checks the limit only when the custodian was *not* already in flight before the
   move — entering fresh (an `add_custodian`, always at `profile`) or re-entering from `cutover`
   (a live custodian pulled back for rework). A custodian already in flight moving from one
   in-flight station to another (`draft` → `dry_run`, say) does not change how many of the
   stream's custodians are in flight, so it is never blocked — not even for a stream an architect
   has since pushed over its own limit by lowering it after custodians were already committed. A
   WIP limit stops new work entering; it was never meant to trap work already in progress, and a
   design that re-checked the total on every lateral move would do exactly that, permanently, the
   moment a limit is lowered below the current count. `test_move_within_in_flight_stations_is_
   never_blocked_by_the_wip_limit` proves this directly, alongside
   `test_move_back_from_cutover_into_in_flight_is_blocked_at_the_limit` proving the
   re-entry case genuinely is checked.

5. **`board.yaml` is full state, rewritten on every save — not an append-only log.** Every other
   operational log this session has built (`astra_agents.exception_triage`'s decisions,
   `astra_agents.guardrails`'s changes, `astra_agents.gate_evidence_compiler`'s approvals) is
   append-only, because each is a *record of a decision* nothing else derives. A board is
   different: its own state (which station, right now) is exactly what the last of each
   custodian's own transitions already says, and a custodian's `transitions` tuple already *is*
   its own history — a separate append-only log alongside it would only duplicate what full-state
   rewrite already keeps, and would risk disagreeing with it. This is the same reason
   `astra_agents.spec_reader.write_draft` writes a full, replaced draft rather than an appended
   one: the object being persisted is a current state, not a sequence of independent facts.
   `board.yaml` is a real, working stand-in for the Postgres-backed store E6 will eventually have
   — the same relationship `astra_agents.guardrails`'s own changes log already has to a database
   (ADR 0053), adapted to a full-state object instead of a log.

6. **No workflow-engine integration, no strict station sequence.** The product spec names Temporal
   (or equivalent) as the eventual workflow engine (Section 5); this story does not build against
   one, and `move` does not enforce a forward-only or one-station-at-a-time state machine — any
   station to any station is allowed. A pilot needs to send a source backward (a dual-run source
   found broken, sent back to `draft`) more than it needs a workflow engine's own strict guard
   rails re-implemented ahead of schedule; that guard, if one is wanted later, is the workflow
   engine's own job, not this module's.

## Consequences

- `control/examples/board.yaml` illustrates the board with the one real custodian this repository
  has (`pershing`, mid-flight in the `envestnet-custodial` stream — the product spec's own first
  design partner) alongside clearly-illustrative peers in that same stream and in a second,
  `blackrock-tableau` (the product spec's own second-named engagement, Section 1) — no Fidelity,
  Schwab or BlackRock Tableau source exists in this repository today. The same "real data plus
  labeled stand-ins" shape every other agent's example fixture in this session has used, chosen
  here specifically to show a stream sitting right at its own WIP limit (`envestnet-custodial`,
  3/3) next to one comfortably under its own (`blackrock-tableau`, 1/2, with one custodian
  already live) — the two states AC2 and AC3 actually describe.
- No live Postgres, no live Temporal, no rendered React screen exists after this story. The
  factory board a delivery lead actually clicks through is later Control-plane work; this story
  is the tested domain logic and CLI that work would sit on top of, following the same
  library-first, CLI-then-UI order every plane in this repository has already been built in.
- `astra-control`'s exit codes follow `astra-agents guardrails`'s shape (0 success, 2 a rejected
  change), not the eleven draft agents' 0/1/2 — except `board show`, which uses 1 for "at least
  one stream is over its own WIP limit," a real condition worth a distinct exit code the same way
  `astra-verify`'s own checks use 1 for "a check failed."
