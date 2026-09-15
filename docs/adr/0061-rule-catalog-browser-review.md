# ADR 0061: Rule review is a browsing and bulk-orchestration layer over the mutation that already exists

Date: 2026-09-15
Status: Accepted
Story: S6.3.5 Rule catalog browser and review (E6, F6.3, WBS 2.6.17)

## Context

AC2 ("status change records who, when, comment") reads like new record-keeping to build. It is
not. `astra_knowledge.rules.set_status(rule, status, by, note=None, at=None)` already validates a
status change (a non-blank `by`, a time strictly after the rule's last recorded change), appends
a `HistoryEntry`, and rewrites the rule's own file — the exact mechanism `astra-spec rules
set-status` already exposes, and the one `astra_agents.rule_recovery`'s own module docstring
already tells a steward to use ("A steward confirms or rejects through `astra-spec rules
set-status`"). Reimplementing that here — a second place that writes a rule's history — would
create two ways for a rule's status to change, one of them not the system of record's own tool.
This story's real job, once that's seen, is the screen a spreadsheet is standing in for today: a
rule beside its citation (AC1), filtering (AC3's first half), and a bulk operation for rules that
are really the same rule stated twice (AC3's second half) — each write still going through
`set_status`, once per rule.

Two things AC3 names have no ready-made answer in the schema:

- **"Filter by rejection code."** A rule has no `rejection_code` field. The only place a rejection
  code appears anywhere in this repository's rule schema is a `tags` entry shaped
  `rejection-<code>` — `astra_agents.rule_recovery`'s own convention for tagging a freshly
  recovered rule that maps to the domain pack's rejection taxonomy. No rule committed to `rules/`
  today carries one; all four were recovered before the convention existed. Inventing a dedicated
  field to make the filter feel complete would not be grounded in anything this codebase actually
  does.

- **"Bulk confirm for identical rules."** No prior story compares two rules for sameness. The one
  natural, defensible definition available in `Rule`'s own shape is exact `text` equality — already
  whitespace-normalized at load time (`" ".join(rule["text"].split())`, `astra_knowledge.rules.
  load_rule_file`), so the comparison is stable across incidental formatting differences in the
  source YAML, not just byte-identical files.

## Decision

1. **`astra_control.rule_review` calls `astra_knowledge.rules.set_status` directly; it does not
   reimplement it.** `change_status(catalog, rule_id, status, *, by, note=None, at=None)` looks up
   the rule, forwards to `set_status`, and turns its `StatusError` into this module's own
   `RuleReviewError` — the same "forward, don't reimplement" shape `astra_control.diff_review`
   already established for `astra_verification.replay.config_diff` and `astra_control.
   custodian_page` for `astra_control.queue.exceptions_from`. Who, when and the comment are
   exactly `set_status`'s own `by`, `at` and `note` parameters; this module adds no new
   record-keeping.

2. **`change_status` accepts only `confirmed`, `rejected` and `legacy_defect`** —
   `REVIEW_STATUSES`, AC2's own three words — even though `astra_knowledge.rules.STATUSES` has a
   fourth, `recovered`. Reverting a rule to `recovered` is not something a steward reviewing the
   catalog does; `recovered` stays meaningful only as a value to filter *by* (AC3's "filter by
   status"), never one this screen sets. The restriction lives in `astra_control.rule_review`
   itself, not as an argparse `choices=` on the CLI, matching this plane's own established
   convention (`--to`/`--tier`/`--role` are all validated by their own domain function, never by
   argparse, so every command's error message keeps this codebase's consistent `error: ...`
   shape).

3. **A rejection code is read from `tags`, filtered by the `rejection-` prefix, never a fabricated
   field.** `rejection_codes(rule)` returns every `tags` entry starting with `rejection-`,
   uppercased, with the prefix stripped. Against the real catalog this returns nothing for every
   rule today — an honest gap, the same shape this plane has already named for a live SSO provider
   (S6.3.1), a FinOps agent and a live file-load log (S6.3.3): the filter is real and will surface
   a match the moment Rule Recovery or a steward actually tags one, but nothing here invents a
   result to look complete.

4. **"Identical," for bulk confirm, means exact `Rule.text` equality — a definition this module
   introduces, not one already established elsewhere.** `identical_rules(catalog, rule_id)` finds
   every other rule in the catalog whose `text` matches the named rule's exactly. `bulk_confirm`
   confirms the named rule and every one of its twins; each is its own independent `set_status`
   call, so one rule's own refusal (already confirmed, say) never blocks the others in the same
   batch — every rule attempted, the result naming which succeeded, the same "attempt every item,
   report per item, refuse nothing silently" shape `astra_control.rule_review`'s own bulk result
   borrows from `astra_agents.gate_evidence_compiler`'s own per-criterion reporting. Only bulk
   *confirm* is built — AC3 names that one operation, not a bulk reject or bulk legacy-defect.

5. **Citation rendering reuses `astra_control.diff_review.citation_link` as-is, not a second
   copy.** A rule's own citation is already `astra_knowledge.rules.Citation` — the same dataclass
   `astra_control.diff_review` already turns into an openable reference (`specs/<id>/<version>.
   yaml#page=N` for a spec citation, `<file>:<line>` for a code citation, AC1's own "source
   file:line"). No committed rule has a code citation yet (Rule Recovery's own drafts land in
   `work/rule-recovery/`, not the approved catalog), so this path is proven with a real rule's own
   citation swapped via `dataclasses.replace` — the same technique `test_spec_viewer.py` already
   uses for a branch no real fixture happens to exercise (ADR 0060, point 3).

## Consequences

- **Steward's first write action.** ADR 0057 named honestly that the steward role — S6.1.3's own
  actor — had zero write actions in the permissions surface at the time, the one action that story
  built (`diff-review.run`) being a read. `rule-review.set-status` and `rule-review.bulk-confirm`
  are steward's first writes, grounded directly in the product spec's own "data steward / rule
  owner... confirms recovered rules" (Section 3) — the fourteenth and fifteenth actions in
  `astra_control.permissions`, alongside the read `rule-review.show`.
- Every write test runs against a private, writable copy of the real rule catalog (`shutil.
  copytree` into `tmp_path`), never the committed `rules/` directory itself — `set_status`
  genuinely rewrites a rule's file on disk, so a test against the real catalog would permanently
  alter committed history. Read-only tests (filtering, citation rendering for a real rule, the
  real catalog's own empty rejection-code and identical-rule results) run directly against
  `rules/`.
- No rendered browser/review screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested filtering, citation
  and bulk-confirm logic a screen would be built on top of.
