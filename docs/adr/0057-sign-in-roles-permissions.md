# ADR 0057: Enforcement lives at the CLI boundary; the auditor role is checked exhaustively

Date: 2026-09-14
Status: Accepted
Story: S6.3.1 Sign-in, roles and permissions: build and evaluate (E6, F6.3, WBS 2.6.13)

## Context

This story's three acceptance criteria are three different kinds of claim. "SSO via the client's
identity provider" needs a live IdP this repository cannot honestly promise in any environment —
the same constraint `astra_control.diff_review` already named for a live model call and a live
Snowflake sandbox. "Each role's allowed actions listed and enforced server-side" and "an auditor
role can read everything and change nothing" are both real, checkable claims about code that
already exists: the nine actions `astra_control.board`, `astra_control.config_studio` and
`astra_control.diff_review` (S6.1.1-S6.1.3) already expose. This story does not get to invent a
richer action surface to look more complete; it has to decide what "enforced" means for the
actions that are actually there, and prove the auditor guarantee against every one of them, not a
sample.

## Decision

1. **Six roles, closed — the backlog's own list.** `Role` is a six-member `Enum`: steward, BSA,
   engineer, ops, PM, auditor. `Action` is the same closed-vocabulary treatment applied to this
   plane's own nine CLI commands, not a broader notion of "everything a person in this role does"
   — an engineer's own richest work today happens in `astra-agents`, which this module does not
   gate. Extending enforcement there, the same way, is later work; inventing engineer-specific
   Control-plane actions here just to give the role something to do would not be grounded in
   anything this repository has actually built.

2. **Every role can take every read action; a role's write actions are the ones its own story
   actually named.** There is no story or ADR anywhere in this repository that gates a *read* by
   role, so `PERMISSIONS[role]` is `READ_ACTIONS | <role's own writes>` for every role, never a
   narrower read set invented for flavor. Writes are assigned by tracing each action back to the
   story that built it and its own actor: `board.set-wip-limit` to PM (S6.1.1's "delivery lead");
   config studio's guided sequence and promotion request to BSA (S6.1.2's own actor) and to ops
   (`docs/ux/personas.md`'s own grounded task, "review and promote a... source's draft... through
   onboarding"); `board.add`/`board.move` to ops, engineer and PM together, since none of the
   three stories names one of them exclusively. **Steward ends up with zero write actions today** —
   S6.1.3's own actor, but the one action that story built, `diff-review.run`, is a read. This is
   stated plainly in the module docstring rather than smoothed over; steward's real write actions
   (confirming rules, approving CDM changes) are Agents-plane work this permissions model does not
   reach yet.

3. **Enforcement lives at the CLI boundary, not inside `board.move` or `config_studio.
   request_promotion` themselves — for the same reason a guardrails check already lives there.**
   `config_studio`'s own retrofit (ADR 0055) already established the pattern: `--guardrails` is an
   optional CLI flag, checked before an existing function runs, rather than a third edit to
   `board.py`'s own signatures. This story reuses the identical shape: `--role`, optional on
   every one of the nine commands, checked against that command's own `Action` via a single
   `_check()` helper before the command's existing logic runs. Omitted, a command behaves exactly
   as it did before this story — proven directly by running every one of the 87 tests already
   committed for board, config studio and diff review unchanged after the retrofit landed.

4. **Every write command got the retrofit, not one sample call site.** `astra_agents.guardrails`
   (S5.13.1) deliberately retrofitted only one existing agent as proof that its own enforcement
   mechanism worked, and left the rest as scoped-out future work — correct there, because nothing
   in that story's own acceptance criteria claimed universal coverage. This story's own AC2
   ("each role's allowed actions... enforced") and AC3 ("read everything... change nothing") are
   both exhaustive claims by their own wording; a single retrofitted command would not make either
   one true. All nine commands carry the check.

5. **Real SSO is out of scope; the claims-to-role mapping is this story's own real, buildable
   piece.** `identity_from_claims` takes claims this module assumes are already authenticated —
   the redirect to the IdP, the SAML assertion or OIDC token signature check, is infrastructure no
   environment here can honestly exercise. What a platform administrator (this story's own actor)
   genuinely configures, once per client, is which of *their* IdP's own group names maps to which
   internal role — `RoleMapping`, loaded from `control/examples/role-mapping.yaml`'s own real,
   working shape. Zero matching groups is refused (no silent default role); more than one group
   mapping to *different* roles is refused too, rather than guessed at by picking one — an
   ambiguous mapping is a configuration problem for the administrator to fix, not a runtime
   decision this module should make quietly.

## Consequences

- `astra_control.permissions` adds no new plane dependency — it is pure closed-vocabulary logic
  over the actions the other three modules already expose, plus `astra_core.yamlsource.load` for
  the role-mapping file, already a dependency.
- The auditor guarantee (AC3) is proven exhaustively, not sampled: `test_auditor_is_refused_
  every_write_action_individually` iterates every `WRITE_ACTIONS` member and asserts a real raise
  for each one, so a ninth or tenth action added later without updating `_WRITE_PERMISSIONS[Role.
  AUDITOR]` (impossible, since that entry is always the empty set by construction) cannot
  silently grant a write the story's own AC would then violate.
- No rendered sign-in screen, no live IdP redirect, no session/cookie handling exists after this
  story — `permissions resolve` is the CLI-level proof that claims-to-role mapping works
  correctly; wiring it behind a real SSO redirect and a real Workbench session is later
  Control-plane work, the same honest gap ADR 0054 and ADR 0055 already named for their own pieces.
