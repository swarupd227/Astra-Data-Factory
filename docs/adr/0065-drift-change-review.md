# ADR 0065: A candidate spec built purely in memory, an existing config diff reused unchanged, and an approval that is one file write, never git

Date: 2026-09-15
Status: Accepted
Story: S6.3.9 Drift change review (E6, F6.3, WBS 2.6.21)

## Context

AC1 ("diff of spec versions and of config") names two diffs this plane has already built the hard
part of. `astra_control.spec_viewer.compare(old, new)` (S6.3.4) already diffs two `SourceSpec`
objects field by field; `SourceSpec` is a plain frozen dataclass with no registry-membership
requirement, so a candidate `new` version never needs to touch disk to be diffable.
`astra_control.diff_review.review` (S6.1.3) already diffs two real config files with rule impact
and affected-custodian lists. What neither already does is turn `astra_agents.drift_watcher`'s
own flat finding list (`kind`, `path`, `current`, `proposed`) into the `SourceSpec` `compare`
needs — the real implementation of Drift Watcher proposes only two kinds of point delta
(`record_length`, `new_code`), never a full spec document, and never a config delta at all,
despite the product spec's own aspirational "Drift Watcher... proposed config delta" (Section 6).

AC2's "impact list of affected custodians and consumers" names two different things. A `SourceSpec`
already has its own `custodians` field — no lineage computation needed the way a rule's own
cross-config lineage is. "Consumers" (a downstream Gold/read-model consumer — the product spec's
own Section 7.5 names "which sources, which consumers" together) has no source anywhere in this
repository's data model; nothing in `astra_knowledge` or `astra_data` tracks one.

AC3 ("approve creates a Git change; never touches prod") is the one mechanical question with a
real, checkable answer: does anything in this codebase invoke git to mutate history? Searched
exhaustively — the only git invocation anywhere is `astra_verification.replay.
old_config_from_git`, a read-only `git show` for a replay's `--old-ref` (ADR 0032). Nothing
commits, branches, tags or pushes. Every prior "this creates a real change" module in this plane —
`astra_knowledge.rules.set_status`, `astra_control.config_studio.request_promotion` — writes a
file and stops there; a human or CI turns it into a real committed change (`README.md`'s own
"Working in this repository": "Git is the system of record... A merge to main deploys to dev; qa
follows once a reviewer approves").

## Decision

1. **`apply_drift_findings` builds a candidate `SourceSpec` purely in memory; nothing here writes
   a spec file.** A `record_length` finding replaces `file["record_length"]`; a `new_code` finding
   adds a code to the named field (parsed from the finding's own `records[<record>].
   fields[<field>].codes` path, validated, refused outright for any other shape or an unknown
   record/field). The result is diffed against the real registered version with `spec_viewer.
   compare`, unmodified — the same function, not a second comparator.

2. **A `record_length` change never shows in `compare`'s own output, by design (ADR 0060: it only
   diffs fields), so this module always shows the raw findings themselves alongside `compare`'s
   field-level view**, rather than extending a comparator whose own scope was deliberately fields
   only, or silently dropping a real finding that has no field-shaped representation.

3. **A config diff is only ever shown when the caller already has two real config files** —
   `diff_review.review` called directly, unchanged. Drift's real implementation never proposes a
   config delta itself; synthesizing a hypothetical "new config" that points at a spec version
   never written to the registry would need that version to actually compile, which nothing in
   this module can honestly produce without inventing a spec-file renderer and a scratch registry
   overlay for a story whose own upstream agent does not build that far. The honest scope: when an
   engineer has already drafted a real candidate config (in `work/`, per draft-not-registry), this
   module diffs it; otherwise the config-diff section says so plainly.

4. **"Consumers" is caller-supplied, honestly empty when not given** — the same "a real, working
   stand-in for data this environment does not have" shape `astra_control.custodian_page`'s own
   `arrivals` and `cost` already established, not an invented downstream-consumer registry.

5. **`approve` appends one entry to a non-prod change-request log — never a git operation, never
   a write to `specs/` or `configs/`.** The exact same shape `astra_control.config_studio.
   request_promotion` already established: a small, hand-rendered, append-only YAML log
   (`json.dumps` for safe quoting, the same convention), refused outright with nothing written
   when `approved_by` is blank. "Never touches prod" is true structurally, not by a runtime check:
   nothing this function does writes anywhere but the one path it is given.

6. **`drift-review.approve` is granted to engineer and steward together** — the story's own actor,
   and the exact pairing `astra_control.queue`'s own `KIND_ROLES[QueueItemKind.DRIFT]` already
   anticipated (its own comment names this later story by number).

## Consequences

- `drift-review show|approve|show-requests` take the same optional `--role` every command in this
  plane does; `show`/`show-requests` are reads, available to every role.
- No real drift-driven config change has ever been drafted in this repository — this module's own
  tests prove the config-diff path by reusing `test_diff_review.py`'s own real config fixtures, not
  a config causally produced by this specific drift.
- No rendered review screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested spec-delta
  construction, diffing and non-prod approval logic a screen would be built on top of.
