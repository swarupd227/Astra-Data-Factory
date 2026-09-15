# ADR 0069: A new per-custodian floor, not an edit to the real config, and the first write with no role gate

Date: 2026-09-15
Status: Accepted
Story: S6.3.13 Notification preferences (E6, F6.3, WBS 2.6.25)

## Context

S1.2.4 "Alerts to Slack, Jira and email" is already fully built — real Terraform
(`infra/terraform/foundation/alerting.tf`): `CONTROL.ALERTS`, `CONTROL.ALERT_ROUTES` (a real
severity→channel routing table, `critical`/`error` reach Slack+Jira+email, `warning` Slack+email,
`info` Slack only), real Slack webhook, Jira REST and Snowflake email delivery. But that routing
is global and severity-based — every recipient on a channel gets everything that severity routes
to; nothing in it is per-person. A source config's own `alerts:` block (`generation/src/astra_
data/schemas/config-v0.schema.json`) already lets a custodian classify `late`/`task_failure`
events at a chosen severity (`info`/`warning`/`error`/`critical`) — real, per-custodian, already
built. Neither piece has ever had a per-*user* preference layered on it, and neither has an
"in-app" channel — no inbox table, no rendered screen, exists anywhere in this repository.

This story's own actor is "any user" for AC1 and "ops roles" for AC2 — two different scopes in
one story. Nothing in this plane's `permissions.py` has ever gated an action by *who owns the
record* rather than by role alone; every prior action check is a flat role lookup.

## Decision

1. **Severity is the config schema's own four-value alert vocabulary** (`info`/`warning`/
   `error`/`critical`), not the domain pack's own three-value rejection-code severity (no `info`)
   — this story is about *alerts*, the same thing the config schema's own severity field already
   names; the two vocabularies look alike but are not interchangeable, so the choice is made
   explicitly rather than assumed.

2. **Channels are this story's own three — `slack`, `email`, `in_app`** — not S1.2.4's own three
   (`slack`, `jira`, `email`). Jira is not something this story asks a person to personally opt
   into (a ticket is an operational artifact, not a notification); `in_app` is real, storable
   preference data even though nothing in this repository can deliver to it yet — the same honest
   "not built yet" gap this plane has already named for other missing infrastructure, not
   invented delivery code to paper over it.

3. **AC2's "severity threshold per custodian" is a new, additive noise floor, not an edit to the
   real config's own `alerts:` block.** That block answers "what severity is a *late* or
   *task_failure* event classified at"; this story's own floor answers a different question,
   "below what severity should nobody be bothered about this custodian at all." Editing the real,
   compiled `alerts:` block in place would mean safely round-tripping a real source config — the
   same risk this plane already declined in S6.3.12 for `domains/*/rejections.yaml`. Instead,
   `custodian_thresholds` is this module's own small, full-state setting — one row per custodian,
   current value only, no per-change history — the same shape `astra_control.board`'s own
   `wip_limits` already has (a plain current-value dict, not an event log).

4. **`reaches(settings, email, custodian_id, channel, severity)` combines both knobs** — an alert
   only reaches a person on a channel when it clears the custodian's own floor *and* that
   person's own channel threshold. Neither AC works in isolation as a genuinely useful
   answer to "would this alert reach me"; this function is the one place both meet.

5. **This is the first per-user settings store in this plane, and `notification-preferences.set`
   is the first write action with no role gate at all** (`UNIVERSAL_WRITE_ACTIONS`, granted to
   every `Role`, auditor included). S6.3.1's own "an auditor role can read everything and change
   nothing" is about this plane's shared, auditable factory state — a rule's status, a board
   position, an autonomy level. A person's own channel preference changes nothing about the
   factory; it is not the kind of write that guarantee is about, so granting it to auditor does
   not weaken it. `notification-preferences.set-threshold`, by contrast, changes a shared setting
   that affects every user's own alerts for a custodian — AC2's own "for ops roles" — granted to
   `Role.OPS` alone, the one role that name most literally names; no textual precedent anywhere in
   this repository names a broader "ops roles" cluster, so none is invented here either, the same
   honest-gap convention this file's own module already established for "QE engineer," "SRE,"
   "security reviewer" and "architect."

## Consequences

- `notification-preferences show|set|show-thresholds|set-threshold|reaches` take the same
  optional `--role` every command in this plane does. `show`/`show-thresholds`/`reaches` are
  reads, available to every role; `set` is available to every role including auditor;
  `set-threshold` is OPS's own first write action.
- `control/src/astra_control/permissions.py`'s own test suite gained an explicit exception list
  (`UNIVERSAL_WRITE_ACTIONS`) rather than a silent loosening — the auditor "changes nothing"
  guarantee is now precise about what kind of change it covers, checked exhaustively either way.
- No rendered preferences screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested settings store,
  per-custodian floor and combined-reachability logic a screen would be built on top of. This is
  also F6.3's own last story — every "Workbench screens" story in the backlog now has real,
  tested domain logic behind it, even where the rendered screen itself remains future work.
