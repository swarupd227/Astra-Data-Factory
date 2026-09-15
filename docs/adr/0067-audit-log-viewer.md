# ADR 0067: Six real sources unified into one record, two honest gaps named rather than invented around

Date: 2026-09-15
Status: Accepted
Story: S6.3.11 Audit log viewer (E6, F6.3, WBS 2.6.23)

## Context

"Search who approved what, when, with the evidence they saw and the agent version" reads like a
single record type this repository already has. It is not — it is the union of fields scattered
across six genuinely different, already-real logs, none of which alone has everything the AC
asks for:

| Source | Owner | Who | When | What was seen |
|---|---|---|---|---|
| `config_studio.PromotionRequest` | `control/` | `requested_by`/`reviewed_by` | `at` | `note` (free text) |
| `drift_review.ChangeRequest` | `control/` | `approved_by` | `at` | `note` (free text) |
| `astra_knowledge.rules.HistoryEntry` | Knowledge plane | `by` | `at` | `note` (free text) |
| `astra_agents.guardrails.LevelChange` | Agents plane | `approver` | `at` | `reason` (free text) + structured `evidence` (acceptance rate, sample size, window) |
| `astra_agents.gate_evidence_compiler.Approval` | Agents plane | `approver` | `at` | `note` (free text) |
| `astra_control.board.Transition` | `control/` | `by` (optional) | `at` | nothing |

No record anywhere has an `agent_version` field, despite the backlog (S6.2.1, and this story
itself) and the product spec (Section 8: "Every approval records who, what, the evidence seen,
and the agent version") both naming it explicitly as if it already existed. `PROVENANCE.json`
(the Generation plane's own build artifact) records a render's own tool version but is never
linked to an approver or a decision. No record anywhere stores a pointer to the evidence actually
shown at decision time — only free text, `guardrails.LevelChange`'s structured `evidence` being
the sole exception, and even that is a summary statistic (acceptance rate, sample size, window),
not a report path or citation.

`astra_control.queue` (S6.3.2) already solved the shape of this problem once, at a smaller scale:
four heterogeneous report kinds, one `QueueItem`, one `<kind>_from(path)` reader per source, one
aggregator. This story is the same pattern at six sources instead of four.

## Decision

1. **One `AuditRecord` unifies all six sources**, with `kind`/`action`/`user`/`at`/`custodian`/
   `subject`/`evidence`/`agent_version`/`source` — `source` is the file path a record came from,
   the same trace-back field `QueueItem.source` already carries. Six reader functions
   (`promotion_requests_from`, `drift_approvals_from`, `rule_status_changes_from`,
   `guardrail_changes_from`, `gate_approvals_from`, `board_moves_from`), each defensive — a
   missing or unreadable source contributes nothing, never an error, the exact same
   "no evidence, not a failure" shape `astra_control.queue`'s own `_read_json` already
   established.

2. **`config_studio`/`drift_review`/`astra_knowledge.rules` are called directly — the first two
   are already `control/` modules, the third is the Knowledge plane, already imported throughout
   this plane.** `guardrails.py` and `gate_evidence_compiler.py` live in the Agents plane, so
   their own logs are read as raw YAML instead — the same "no new agent import, read the file it
   already wrote" boundary `astra_control.queue` and `astra_control.agent_review` already
   established, extended here to two more Agents-plane log files rather than importing their
   dataclasses.

3. **`agent_version` is always `None`, in every record, from every source — never invented.** No
   source this module reads has ever tracked one; a fabricated version string here would be worse
   than an honest gap, since the whole point of this screen is that its answers are trustworthy
   enough to be SOX evidence.

4. **`evidence` is the richest honest thing each source actually has**: a free-text `note`/
   `reason` for five of the six, and for `guardrails.LevelChange` specifically, that `reason`
   joined with its own structured `evidence` when present (acceptance rate, sample size, window)
   — never a fabricated pointer to a report or diff no record actually stores.

5. **A `drift_approvals_from` record's own `custodian` is resolved from the real spec registry
   when `specs_dir` is given** — `ChangeRequest` itself carries no custodian, only `spec_id`/
   `spec_version`; the same registry lookup `astra_control.drift_review.review` already does
   (`SourceSpec.custodians`), reused here rather than leaving every drift-approval row
   unfilterable by custodian. Without `specs_dir`, it degrades to `None` honestly rather than
   refusing the whole read.

6. **A `board.Transition` with no recorded `by` (the field is optional) is skipped, never shown
   as an anonymous action** — an audit log with a row nobody can be attributed to would be worse
   than not showing it at all.

7. **`astra_control.agent_review`'s own accept/reject (S6.3.6) is absent from this aggregation,
   on purpose, not by oversight.** Its gold-set `Case` schema (`{id, tier, input, expected}`,
   `additionalProperties: false`) has structurally no room for a `by`/`at` field (ADR 0062) — there
   is genuinely nothing to read. This is the single sharpest gap this story's own AC collides
   with: an agent suggestion's own accept/reject decision cannot be part of a SOX-evidence query
   today.

8. **CSV export is the first use of `csv.writer`/`csv.DictWriter` for producing a file anywhere in
   `control/`** (every prior `csv` usage in this repository is `csv.DictReader`, reading someone
   else's file) — `to_csv`/`write_csv` use the standard library's own `csv.DictWriter` directly,
   no new dependency.

## Consequences

- `audit-log show|export` take the same optional `--role` every command in this plane does; both
  are reads — a real approval/decision is never MADE by this screen, only searched, so nothing
  here needed a write action, and `auditor`'s own "reads everything, writes nothing" (S6.3.1)
  needed no change.
- No real committed guardrails changes log or drift-change-requests log exists anywhere in this
  repository yet — this module's own tests build both directly, in the real writers' own exact
  YAML shape, rather than inventing a different one.
- No rendered search screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested aggregation,
  filtering and CSV-export logic a screen would be built on top of.
