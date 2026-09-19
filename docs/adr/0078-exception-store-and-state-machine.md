# ADR 0078: The workflow keeps the model's four statuses, adds a graph, a history and assignment, and enforces them in SQL and Python from one definition

Date: 2026-09-19
Status: Accepted
Story: S7.1.4 Exception store and state machine (E7, F7.1, WBS 2.7.9)

## Context

S3.2.5 (ADR 0023) built the exception *store*: every rejected row is written to
`EXCEPTIONS.<SOURCE>` with its code, its full payload and the workflow columns `STATUS`,
`RESOLUTION`, `RESOLVED_BY`, `RESOLVED_AT`, `UPDATED_AT` — and ADR 0023 says "Triage moves a row
out of NEW; nothing else does." Nothing implemented that move. S6.2.5 (ADR 0074) built a workflow
for suggestion *groups* and named this story as the per-exception backbone. Research before
designing found exactly what is and is not there:

- **Vocabulary exists, a graph does not.** The CDM's `Exception.STATUS` codes are `NEW`,
  `RESOLVED`, `AUTO_RESOLVED`, `DISMISSED`. The only guard anywhere is a rendered test that the
  value is one of the four; nothing says which move is allowed.
- **No history and no owner.** Nothing records who moved an exception or when. The taxonomy names
  a responsible party per *code* (`custodian`, `steward`, `data_engineer`, `platform`), but there is
  no per-exception owner or assignee anywhere; `RESOLVED_BY` names the resolver afterwards.
- **No enforcement, in Python or SQL.** `rules.set_status` (the closest precedent) rejects an
  unknown or unchanged status but has no transition graph at all.
- **No consolidation.** The per-source tables and `SILVER.EXCEPTION` are unconnected, and no
  per-source bundle is committed, so no per-source EXCEPTIONS table is deployed anywhere yet.
- **Every consumer depends on `NEW`.** Exception Triage works only on rows with status `NEW`; the
  ageing report (S6.2.5) counts only `NEW`.

Three questions were left open by the repository and are decided here: what the states are,
what an owner is, and how the exceptions are consolidated.

## Decision

1. **The states are the model's own four statuses, unchanged.** Adding a status is a breaking
   model change (the backlog requires a new major CDM version and a migration note), would touch
   every consumer, and is unnecessary: "being worked on" is ownership, not a status. An exception
   leaves `NEW` exactly once, to `RESOLVED`, `AUTO_RESOLVED` or `DISMISSED`, and those are
   terminal. There is no reopen: nothing in the repository asks for one, and an invented one is a
   behaviour to defend. The workflow refuses to render for a pack whose model's `Exception.STATUS`
   codes differ from these states, and a test compares them to the real model.

2. **Every move needs an actor and its explanation, and `AUTO_RESOLVED` is only for a whitelisted
   code.** `RESOLUTION` is required for all three terminal states — what was done, or why it was
   dismissed. `AUTO_RESOLVED` requires the taxonomy's `auto_resolve` for the code, the field whose
   schema text names Exception Triage. The store enforces only that data-level condition; whether
   Triage's task class is currently authorised at L3 remains its own guardrail (S5.13.1).

3. **Owner is the taxonomy's responsible party for the code, assignee is a person.** The owner is
   read from `CONTROL.REJECTION_CODES` when an event is written and kept on the event; nothing new
   is stored per exception for it. The assignee is set and changed while the exception is `NEW`,
   and a closed exception cannot be assigned. The taxonomy owners are not mapped onto the Control
   plane's six roles here — that mapping belongs to the story that filters a queue by role
   (S6.3.2), not to the store.

4. **One append-only history, `CONTROL.EXCEPTION_HISTORY`, is the only new storage.** One row per
   move or assignment: source, exception, code, owner, kind, from and to status, assignee, actor,
   time, note. The status itself stays on the exception's own row in `EXCEPTIONS.<SOURCE>`, in the
   columns S3.2.5 already gave it — there is one status, never a second copy to reconcile.
   `CONTROL.EXCEPTION_ASSIGNMENTS` is a view of the latest assignment per exception.

5. **Two procedures are the only sanctioned way to move or assign,** `CONTROL.TRANSITION_EXCEPTION`
   and `CONTROL.ASSIGN_EXCEPTION`, so no client — an operations screen, the Triage agent — can
   bypass the rules. Each refuses an invalid request with a named, numbered exception before
   anything is written; the status change and its history row are written together, and the update
   applies only while the row still has the status that was read. Procedures address an exception
   by source and id and reach the per-source table by name after checking the name is a plain
   source id, because a domain-pack bundle cannot enumerate the per-source tables.

6. **One definition, two implementations, checked against each other.** The Python reference
   implementation (`astra_knowledge.patterns.exception_workflow`, the same "oracle the rendered
   code is checked against" role `Replica` plays for replication) holds the states, the allowed
   moves and the ordered rule names. The renderer (`astra_data.exception_store`) imports those
   constants to build the SQL, and its tests parse the rendered SQL and compare the allowed moves,
   the states, the order the rules run in and the rule numbers to the twin's. The exhaustive
   transition test runs the whole 4×4 matrix of states against the twin: every allowed edge moves,
   every other edge is refused with nothing changed and nothing recorded.

7. **The deployed bundle carries tests that hold on real data.** `custodial-exceptions` is a
   per-domain-pack bundle like Gold and Silver, so `astra-data deploy`/`test` and CI pick it up
   with no change. Its three tests are invariants over the history that any deployed environment
   can be checked against: every recorded move is an allowed edge, every event has an actor and the
   shape of its kind, nothing is assigned after the exception left `NEW`. The last also catches an
   assignment that raced a close, which the procedure guards against only best-effort.

## Consequences

- **The two workflows stay distinct.** The Control plane's group workflow (S6.2.5:
  new → suggested → approved → resubmitted → closed) is a screen's own status on a suggestion
  group and is unchanged; this is the per-exception status on the row. They meet where a person
  acts on a group: nothing here calls or replaces that module.
- **No cross-source listing yet.** "Every NEW exception across all sources" needs a union over
  per-source tables, which a pack bundle cannot render and this story does not add; history and
  assignments *are* cross-source, keyed by source id. The Triage export (still no producer) or a
  release-level render over all configs is where that belongs.
- **The procedures are verified structurally and through the twin, not executed.** No environment
  here has a live Snowflake account — the same gap named for every rendered procedure before this
  (`PUBLISH_GOLD`, the replication procedures). Their behaviour is asserted from the rendered text
  and from the twin they are generated from; executing them against a real sandbox is
  `astra-verify`'s job once such an environment exists.
- **Who may call them is a grant, not code here.** The procedures run `EXECUTE AS OWNER`, so
  nobody but the owner can call them until the foundation grants USAGE to the roles that should
  (steward, operations); adding those grants to the Terraform foundation is a deployment step this
  story does not take.
- **Nothing writes `NEW` rows back through the procedures yet.** Exception Triage still reads a
  CSV that nothing produces (ADR 0048); wiring it to call `TRANSITION_EXCEPTION` for an
  auto-applied group, and wiring the Control plane's screen to call both procedures, are the
  follow-on stories this backbone exists for.
