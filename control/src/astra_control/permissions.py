"""Sign-in, roles and permissions: each role's allowed actions listed and enforced
server-side, an auditor role that reads everything and changes nothing (S6.3.1, ADR 0057,
product spec Section 10: "Workbench... one screen for engineers, BSAs and stewards").

Six roles, closed — the backlog's own list, never extended: steward, BSA, engineer, ops, PM,
auditor. But a narrower question than "everything a person in this role does" — this module's
own `PERMISSIONS` answers only "which of this Control plane's own actions can this role take,"
the same closed set of CLI commands `astra_control.board`, `astra_control.config_studio` and
`astra_control.diff_review` already expose (S6.1.1-S6.1.3). An engineer's own richest work today
happens in the Agents plane's own CLI (`astra-agents`), which this module does not gate —
extending server-side enforcement there, the same way, is later work, not invented here for
symmetry's sake.

Every action is read or write. Every role can take every read action — there is no story or ADR
anywhere in this repository that gates a read by role, and inventing one here would not be
grounded in anything. A role's own write actions are the ones actually named by the story that
built each action: `board.set-wip-limit` by S6.1.1's own actor (a delivery lead — `pm` here);
config studio's guided sequence and promotion request by S6.1.2's own actor (a BSA) and by ops
(`docs/ux/personas.md`'s own grounded task for the ops reconciler, "review and promote a...
source's draft... through onboarding"); `board.add`/`board.move`, the board's own general
management, by ops, engineer and PM together — none of the three stories names one exclusively.
**The auditor role has no write action at all** — `PERMISSIONS[Role.AUDITOR]` is exactly the read
actions, nothing more, checked directly against every action this module knows (AC3). **Steward
has none either, today** — S6.1.3's own actor, but the one action that story built,
`diff-review.run`, is a read; nothing in this action set is yet a steward-specific write, and
this module says so honestly rather than inventing one to look complete.

Real SSO — redirecting to the client's own identity provider, validating a SAML assertion or an
OIDC token's signature — needs a live IdP this module cannot honestly promise in every
environment, the same reason `astra_control.diff_review` cannot honestly run a live model call or
a live Snowflake sandbox. What this module owns is the one piece that is genuinely this
platform's own: turning the IdP's own already-validated claims into an internal `Identity`,
through a `RoleMapping` a platform administrator configures once per client (which of *their* IdP
groups is "steward", which is "BSA", ...) — this story's own actor, doing this story's own job.

Enforcement lives at the CLI boundary, not inside `astra_control.board`'s or `astra_control.
config_studio`'s or `astra_control.diff_review`'s own functions — the Control plane's own real
server-side entry point in this codebase's convention ("Python services," product spec Section
5), the same reason a config-studio call is already gated at CLI time by a `--guardrails` flag
rather than by editing `board.move` itself a third time. `--role` is optional on every command,
the same shape `--guardrails` already has: omitted, a command behaves exactly as it did before
this story — every test already committed for board, config studio and diff review is untouched.
Given, the command's own action is checked against `PERMISSIONS[role]` before anything runs; a
role not authorized for the action is refused (exit 2) before a single line is written, the same
"refused outright, nothing written" shape `astra_agents.guardrails` already established.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from astra_core.yamlsource import load


class AuthorizationError(RuntimeError):
    pass


class Role(Enum):
    STEWARD = "steward"
    BSA = "bsa"
    ENGINEER = "engineer"
    OPS = "ops"
    PM = "pm"
    AUDITOR = "auditor"


class Action(Enum):
    BOARD_SHOW = "board.show"
    BOARD_ADD = "board.add"
    BOARD_MOVE = "board.move"
    BOARD_SET_WIP_LIMIT = "board.set-wip-limit"
    CONFIG_STUDIO_SHOW_REQUESTS = "config-studio.show-requests"
    CONFIG_STUDIO_START = "config-studio.start"
    CONFIG_STUDIO_ADVANCE = "config-studio.advance"
    CONFIG_STUDIO_REQUEST_PROMOTION = "config-studio.request-promotion"
    DIFF_REVIEW_RUN = "diff-review.run"
    CUSTODIAN_PAGE_SHOW = "custodian-page.show"
    SPEC_VIEWER_SHOW = "spec-viewer.show"
    SPEC_VIEWER_COMPARE = "spec-viewer.compare"


READ_ACTIONS = (
    Action.BOARD_SHOW,
    Action.CONFIG_STUDIO_SHOW_REQUESTS,
    Action.DIFF_REVIEW_RUN,
    Action.CUSTODIAN_PAGE_SHOW,
    Action.SPEC_VIEWER_SHOW,
    Action.SPEC_VIEWER_COMPARE,
)
WRITE_ACTIONS = tuple(a for a in Action if a not in READ_ACTIONS)

# Every role's own write actions, named by whichever story actually built the action and its own
# actor (module docstring). Reads are added to every role uniformly, below.
_WRITE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.STEWARD: frozenset(),
    Role.BSA: frozenset({Action.CONFIG_STUDIO_START, Action.CONFIG_STUDIO_ADVANCE, Action.CONFIG_STUDIO_REQUEST_PROMOTION}),
    Role.ENGINEER: frozenset({Action.BOARD_ADD, Action.BOARD_MOVE}),
    Role.OPS: frozenset({Action.BOARD_ADD, Action.BOARD_MOVE, Action.CONFIG_STUDIO_START, Action.CONFIG_STUDIO_ADVANCE, Action.CONFIG_STUDIO_REQUEST_PROMOTION}),
    Role.PM: frozenset({Action.BOARD_ADD, Action.BOARD_MOVE, Action.BOARD_SET_WIP_LIMIT}),
    Role.AUDITOR: frozenset(),
}

PERMISSIONS: dict[Role, frozenset[Action]] = {role: frozenset(READ_ACTIONS) | _WRITE_PERMISSIONS[role] for role in Role}


def _role(value: Role | str) -> Role:
    if isinstance(value, Role):
        return value
    try:
        return Role(value)
    except ValueError:
        raise AuthorizationError(f"'{value}' is not a role; roles are {', '.join(r.value for r in Role)}") from None


def _action(value: Action | str) -> Action:
    if isinstance(value, Action):
        return value
    try:
        return Action(value)
    except ValueError:
        raise AuthorizationError(f"'{value}' is not an action; actions are {', '.join(a.value for a in Action)}") from None


def authorized(role: Role | str, action: Action | str) -> bool:
    return _action(action) in PERMISSIONS[_role(role)]


def require(role: Role | str, action: Action | str) -> None:
    """Raises AuthorizationError, refusing outright, when `role` may not take `action` — never a
    warning, never a partial success."""
    role, action = _role(role), _action(action)
    if not authorized(role, action):
        raise AuthorizationError(f"{role.value} is not allowed to {action.value}")


# -- identity: an already-authenticated IdP's claims, mapped to an internal role ----------------


@dataclass(frozen=True)
class Identity:
    email: str
    name: str
    role: Role

    def can(self, action: Action | str) -> bool:
        return authorized(self.role, action)


@dataclass(frozen=True)
class RoleMapping:
    """Which of the client's own identity provider groups map to which internal role —
    configured once per client by a platform administrator, this story's own actor."""

    by_group: dict[str, Role]


def load_role_mapping(path: Path) -> RoleMapping:
    path = Path(path)
    if not path.is_file():
        raise AuthorizationError(f"role mapping file not found: {path}")
    data = load(path.read_text(encoding="utf-8")) or {}
    groups = data.get("groups") or {}
    return RoleMapping(by_group={str(group): _role(value) for group, value in groups.items()})


def identity_from_claims(claims: dict, mapping: RoleMapping, *, group_claim: str = "groups") -> Identity:
    """Turns an already-validated identity provider's claims into an internal Identity. The IdP
    integration itself — the redirect, the SAML assertion or OIDC token's own signature check —
    is out of scope here; claims arriving at this function are assumed already authenticated."""
    email = claims.get("email")
    if not email or not str(email).strip():
        raise AuthorizationError("the identity provider's claims have no email")
    name = str(claims.get("name") or email).strip()
    groups = claims.get(group_claim) or []
    matched = {mapping.by_group[g] for g in groups if g in mapping.by_group}
    if not matched:
        raise AuthorizationError(f"{email}: none of {list(groups)} maps to a role; ask a platform administrator to add one to the role mapping")
    if len(matched) > 1:
        raise AuthorizationError(f"{email}: {sorted(g for g in groups if g in mapping.by_group)} map to more than one role ({', '.join(sorted(r.value for r in matched))}); the role mapping is ambiguous for this person")
    return Identity(email=str(email).strip(), name=name, role=next(iter(matched)))


# -- the view --------------------------------------------------------------------------


def render_permissions(role: Role | None = None) -> str:
    """Every role's allowed actions, listed — AC2. `role` given: just that one role."""
    out = ["# Roles and permissions", ""]
    roles = (role,) if role else tuple(Role)
    for r in roles:
        actions = sorted(PERMISSIONS[r], key=lambda a: a.value)
        out.append(f"## {r.value}")
        out.append("")
        out.append("| Action | Kind | Allowed |")
        out.append("|---|---|---|")
        for a in sorted(Action, key=lambda a: a.value):
            kind = "read" if a in READ_ACTIONS else "write"
            allowed = "yes" if a in actions else "no"
            out.append(f"| {a.value} | {kind} | {allowed} |")
        out.append("")
    return "\n".join(out)
