# Runbook: moving and assigning exceptions

Story S7.1.4 (ADR 0078). Every exception is a row in `EXCEPTIONS.<SOURCE>` (S3.2.5). This is how
one is assigned, moved and audited, and how the rules that refuse an invalid move are kept in step
with their reference implementation.

## The states

An exception is written `NEW`. It leaves `NEW` exactly once, to one of three terminal states:

| From | To | Meaning |
|---|---|---|
| `NEW` | `RESOLVED` | resolved by a person |
| `NEW` | `AUTO_RESOLVED` | resolved by a whitelisted rule, with audit — only for a code the taxonomy marks `auto_resolve` |
| `NEW` | `DISMISSED` | closed without a change |

These are the model's own `Exception.STATUS` codes. There is no reopen and no other move: from a
terminal state, every request is refused.

## Assigning

```sql
CALL CONTROL.ASSIGN_EXCEPTION('pershing_position', '<exception id>', 'ops@example.com', 'pm@example.com');
```

`SOURCE_ID` is the source config's id (the table is `EXCEPTIONS.<SOURCE_ID upper-cased>`). Refused
if the exception does not exist, if no assignee or actor is given, if the exception is already
closed, or if it is already assigned to that person. Assigning to someone else is a reassignment
and is kept in the history.

## Moving

```sql
CALL CONTROL.TRANSITION_EXCEPTION('pershing_position', '<exception id>', 'RESOLVED', 'steward@example.com',
                                   'Added the account to the cross-reference.');
```

The last argument is always required: what was done, or why the exception was dismissed. It is
written to the exception's `RESOLUTION`, with `RESOLVED_BY`, `RESOLVED_AT` and `UPDATED_AT`, and a
history row is written in the same transaction.

## When a request is refused

Nothing is written; the call fails with a named, numbered exception:

| Number | Rule | Why |
|---|---|---|
| -20101 | `exception_not_found` | no such exception in that source |
| -20102 | `not_a_state` | the target is not `NEW`, `RESOLVED`, `AUTO_RESOLVED` or `DISMISSED` |
| -20103 | `actor_required` | who is making the change was left blank |
| -20104 | `illegal_transition` | the exception is not `NEW` (or the target is `NEW`) |
| -20105 | `resolution_required` | no explanation was given |
| -20106 | `not_whitelisted` | `AUTO_RESOLVED` for a code that is not whitelisted |
| -20107 | `assignee_required` | no assignee was given |
| -20108 | `not_new` | assigning an exception that is already closed |
| -20109 | `already_assigned` | assigning to the person it already has |
| -20201 | `source_invalid` | the source id is not a plain source name |
| -20202 | `changed_concurrently` | the exception changed while it was being updated; read it again |

The checks run in this order, so a request with several faults is told about the first.

## Reading the history

```sql
SELECT * FROM CONTROL.EXCEPTION_HISTORY WHERE SOURCE_ID = 'pershing_position' AND EXCEPTION_ID = '<id>' ORDER BY EVENT_AT;
SELECT * FROM CONTROL.EXCEPTION_ASSIGNMENTS WHERE ASSIGNEE = 'ops@example.com';
```

Each row is one assignment or one move: who, when, the code, the taxonomy owner at that time, and
for a move the status before and after and the explanation. `EXCEPTION_ASSIGNMENTS` is each
exception's latest assignment; it keeps a closed exception's last assignee, so join the
exception's own `STATUS` to list only the open ones.

## Changing the workflow

The allowed moves, the states and the rule order are one definition:
`knowledge/src/astra_knowledge/patterns/exception_workflow.py`. Change it there, then:

```bash
astra-data exceptions render          # rewrites releases/<pack>-exceptions/
astra-data exceptions render --check  # what CI runs: fails if the committed bundle is stale
```

The renderer's tests parse the rendered SQL and compare its allowed moves, states, rule order and
rule numbers to that definition, so the SQL cannot quietly diverge from its reference
implementation. The renderer refuses a domain pack whose `Exception.STATUS` codes differ from the
workflow's states: adding a state is a model change first (a new major CDM version).

## Checking a deployed environment

`astra-data test --environment <env> releases` runs three invariants against the real history:
every recorded move is an allowed edge, every event has an actor and the shape of its kind, and
nothing was assigned after the exception left `NEW`. Each returns the offending rows, so a
passing run is an empty result.

## Not yet in place

- Calling either procedure needs a grant: they run `EXECUTE AS OWNER`, so until the foundation
  grants USAGE to a role, only the owner can call them.
- Nothing yet lists every `NEW` exception across all sources (per-source tables cannot be unioned
  by a pack bundle), and Exception Triage still reads a CSV rather than calling
  `TRANSITION_EXCEPTION`.
- The procedures have been checked from their rendered text and against the reference
  implementation, not executed — no environment here has a live Snowflake account. Run them
  against a sandbox before relying on them.
