# ADR 0068: A faithful duplicate of guardrails' own validation, a whitelist request that never touches the real taxonomy, and PM as the actor the spec itself names

Date: 2026-09-15
Status: Accepted
Story: S6.3.12 Admin: autonomy levels and whitelist (E6, F6.3, WBS 2.6.24)

## Context

AC1 ("change requires a reason and is logged") and AC2 ("L3 cannot be set unless the measured
acceptance rate is shown above threshold") are not new rules to invent — `astra_agents.guardrails`
(S5.13.1) already enforces exactly this: `record_change` refuses outright, writing nothing, unless
`agent`/`task_class`/`approver`/`reason` are all non-blank, and a change to `Level.L3` additionally
needs `Evidence` whose `sample_size >= L3_MINIMUM_SAMPLE` (20) and `acceptance_rate >=
L3_ACCEPTANCE_THRESHOLD` (0.8). This plane's own rule — never import `astra_agents` — applies here
even though `guardrails.py` is pure governance logic with no model call in it: the boundary this
whole session has held is about which package `control/` depends on, not about which functions
happen to be safe to call. So this story's own job is to reimplement that validation faithfully,
not merely call it.

The "self-healing whitelist" is not a separate artifact to discover — it is `RejectionCode.
auto_resolve` on a domain pack's own real rejection taxonomy (`domains/<pack>/rejections.yaml`),
already the exact field `astra_agents.exception_triage` reads for its own auto-apply gate,
independently of and alongside guardrails' own L3 authorization (the two are ANDed together at
the CLI, never inside one function). The real committed `domains/custodial/rejections.yaml` is
651 lines of hand-authored prose — descriptions, resolutions, section-header comments — for 67
codes, only two of which (`PRICE_MISSING`, `PRICE_STALE`) are whitelisted today. `astra_agents.
exception_triage`'s own ADR (0048) already states the real governance boundary directly: "Adding
`auto_resolve: true` to a new code in the real taxonomy is a steward's decision, not this agent's."

This story's actor, "architect," is not one of the six closed roles — but unlike every prior
"QE engineer"/"SRE"/"security reviewer" gap, this one is not moot: both new actions are writes,
so *some* role must actually be authorized, or the story's own screen has no one who can use it.

## Decision

1. **`set_level` reimplements `record_change`'s exact validation, in the same order, with the
   same messages, and writes the exact same hand-rendered YAML** — `LEVELS`, `L3_MINIMUM_SAMPLE`
   and `L3_ACCEPTANCE_THRESHOLD` are duplicated as a small closed vocabulary, the same way
   `astra_control.config_studio`'s own `TIERS` is already duplicated from `astra_agents.
   pattern_matcher` "rather than importing the whole Agents plane for one tuple... unlikely to
   drift on its own." Byte-for-byte compatibility is not incidental: it is verified directly —
   the real `astra_agents.guardrails.load_changes` reads a file this module wrote and recovers
   every field, evidence included, exactly.

2. **The self-healing whitelist is read via `astra_knowledge.rejections.load_taxonomy` directly**
   (the Knowledge plane, already imported throughout this plane — the "no `astra_agents` import"
   boundary is specific to the Agents plane, not to every other one) — never re-parsed, never a
   second schema.

3. **This module never writes to `domains/*/rejections.yaml`.** The real file has no safe
   round-trip writer anywhere in this codebase, and building one to flip a single boolean inside
   651 lines of hand-authored prose, comments and per-code narrative would risk corrupting real,
   carefully-authored production data for a capability the codebase's own ADR (0048) already says
   is a human's decision, not a program's. "Manage the whitelist" here means `request_whitelist_
   change` — logged, who and why, the same "propose, log, a human applies it" shape `astra_
   control.drift_review.approve` already established for "approve creates a Git change; never
   touches prod." Given a real taxonomy, the request is checked against it (the code is real, the
   change is not a no-op) before being logged; without one, it is still logged honestly, since a
   steward reviewing the log is the one who ultimately checks it against the real file.

4. **Both new writes are granted to `Role.PM` alone — not guessed onto `engineer`, and not split
   across roles the way S6.3.6's and S6.3.9's were.** The product spec's own persona table
   (Section 3) attributes this exact responsibility to a *different* named persona than
   "architect": the "Artizent delivery lead... Runs the factory board, pace dial, and agent
   autonomy levels" — already `Role.PM` (S6.1.1's own board actor, S6.1.2's own `board.
   set-wip-limit`). `docs/ux/personas.md`'s own PM task flow says the same thing operationally:
   "Record and review autonomy-level changes per (agent, task class)... `guardrails set-level` a
   promotion once its evidence clears the bar." This is the first genuinely forced role decision
   among this file's several "actor named is not a role" gaps — every prior one added only reads,
   moot regardless of who the actor "really" was; this one is grounded in the spec's own text
   naming the actual responsible persona, not a guess.

## Consequences

- `autonomy-admin show-levels|set-level|show-whitelist|request-whitelist-change|
  show-whitelist-requests` take the same optional `--role` every command in this plane does;
  three are reads, available to every role; `set-level` and `request-whitelist-change` are PM's
  own first write actions.
- No real committed guardrails changes log exists anywhere in this repository — this module's own
  tests build one directly, in the real writer's own exact shape. Byte-compatibility with
  `astra_agents.guardrails.load_changes` was verified live (a file this module wrote, read back
  correctly by the real Agents-plane function, evidence included) rather than as an automated
  test, since asserting it in `control/`'s own test suite would need `agents/src` on this plane's
  own test path — the same import boundary this module's production code does not cross either.
- No rendered admin screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested level-change
  validation and whitelist-request logic a screen would be built on top of.
