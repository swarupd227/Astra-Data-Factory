# ADR 0074: Group-level workflow, not a per-exception store — that is its own later story

Date: 2026-09-16
Status: Accepted
Story: S6.2.5 Ops exception UI (E6, F6.2, WBS 2.6.8) — closes feature F6.2

## Context

Exception Triage (S5.8.1) already produces exactly the raw material this story asks a person to
work with: a real `report.json`, one `Suggestion` per root-cause group (a rejection code plus a
`group_key` — the same raw value, else the same field, else just the code), each carrying a real
`resolution` from the rejection taxonomy when one exists, `None` when it does not (the code lands
in `unresolved_codes` instead). `astra_control.queue.exceptions_from` already reads this exact
file directly, already excludes any group whose `auto_apply` is true (a whitelisted, self-healing
group needs no operator at all) — this story's own module follows both conventions rather than
inventing a third way to read the file or importing `astra_agents.exception_triage`.

The story's own new -> suggested -> approved -> resubmitted -> closed chain has no existing
per-exception implementation anywhere in this codebase to build on. Confirmed exhaustively before
writing anything:

- **No per-exception-id action exists today.** Exception Triage and `astra_control.queue` both
  operate purely on aggregated `Suggestion` groups; nothing anywhere mutates or tracks a single
  exception's own status.
- **The backlog itself schedules the real backbone as later work**: S7.1.4 "Exception store and
  state machine" (F7.1), explicitly titled and scoped for exactly this — "exceptions stored with
  state transitions and owners" — and listed *after* this story. Building a second, competing
  per-exception store here would preempt that story, not extend it.
- **The CDM's own `Exception.STATUS` column** (`domains/*/cdm/1.0.yaml`) is a different, four-value
  vocabulary (`NEW`/`RESOLVED`/`AUTO_RESOLVED`/`DISMISSED`) for a single record, schema-only today
  (no Python anywhere reads or writes a row of it) — not this story's own five-value chain, and not
  something to quietly reuse or conflate with it.
- **No callable or composable single-record re-run mechanism exists anywhere.**
  `astra_data.render.resolve`'s own `RESOLVE` stored procedure is deployed, whole-run, set-based
  SQL, invoked by live pipeline orchestration — structurally unlike `astra_control.golden_viewer`'s
  own composable `astra-verify golden capture` command, which at least has a real CLI entry point
  to reference. There is nothing to compose here, only a deployed artifact this environment cannot
  invoke.
- **No ageing/aging computation exists anywhere in this repository.** The only real per-exception
  timestamp anywhere is `raised_at` on a raw `ExceptionRecord` row (`exceptions.csv`, the same file
  Exception Triage itself reads) — Exception Triage's own `Suggestion` does not carry it forward.

## Decision

1. **The workflow tracks suggestion GROUPS, not individual exception ids** — the same granularity
   `astra_control.queue` and Exception Triage's own report already use. A real per-exception store
   with its own transitions and owners is S7.1.4's own job; this module does not build a
   competing, narrower version of it.

2. **"new" and "suggested" are read straight off Exception Triage's own real distinction** — a
   group whose code has a taxonomy resolution starts `suggested`; a group whose code does not
   (`unresolved_codes`) starts `new` and is refused outright by `accept()` until a person adds a
   taxonomy entry and Exception Triage runs again. Never an invented vocabulary layered on top.

3. **Each group is a full-state card with its own embedded transition history** — the same shape
   `astra_control.board.CustodianCard` already established (a custodian's transitions already
   carry its own history), rather than a second append-only log duplicating a full-state
   rewrite. `sync()` adds a card for every group not yet tracked and never touches one already
   tracked, so a later Exception Triage run can never silently overwrite an operator's own
   in-progress work.

4. **The five statuses are enforced strictly in order** (`accept`, `resubmit`, `close` each refuse
   outright unless the card is in the exact status that precedes it), the same "refused outright,
   nothing written" shape `astra_control.config_studio.advance` already established for its own
   `profile -> draft -> dry_run` sequence. `edit()` is only allowed on an `approved` card — after
   accept, before resubmit — matching the story's own AC ordering ("accept a suggestion, edit,
   resubmit and close") — and keeps the original Exception Triage resolution alongside the edited
   one for comparison, a lighter version of `astra_control.agent_review`'s own "keep the original"
   shape (one editable field here, not several).

5. **`resubmit()` never fakes a live re-run.** It records the transition — who, when — with an
   honest note that no callable single-record resolution step exists in this environment, the same
   "genuine gap, plainly named" posture every other Control module in this session has used for a
   capability no environment here actually has (a live Snowflake sandbox, a non-prod database, a
   live identity provider).

6. **The ageing report reads `exceptions.csv` directly**, the identical file Exception Triage
   itself reads, grouping still-open (`status == NEW`) rows by `rejection_code` and reporting the
   oldest real `raised_at` per code — the only real per-exception timestamp anywhere in this
   codebase, never approximated from when a card was first synced onto this screen's own board.

## Consequences

- `exception-review show`/`ageing` are reads, available to every role. `accept`/`edit`/`resubmit`/
  `close` are real writes to this plane's own exception review board, granted to ops and BSA
  together — the story's own "reconciliation operator" actor is `docs/ux/personas.md`'s own
  renaming of the product spec's "ops / business analyst," and `astra_control.queue`'s own
  `KIND_ROLES[QueueItemKind.EXCEPTION]`, written before this story, already anticipated both roles
  sharing it.
- A group's card can sit at `resubmitted` indefinitely with no automated way to confirm the
  underlying records actually cleared — that confirmation needs a real resolution re-run this
  environment cannot perform. `close()` is a person's own judgment call that the batch's next real
  run resolved it, not a verified fact this module checks.
- When S7.1.4 eventually builds the real per-exception store, this module's own group-level board
  will likely be superseded or need to reconcile against it — flagged here rather than pretended
  away.
