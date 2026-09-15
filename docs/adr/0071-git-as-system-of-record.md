# ADR 0071: The first module that actually mutates git — because the decision it commits was already made

Date: 2026-09-15
Status: Accepted
Story: S6.2.2 Git as system of record (E6, F6.2, WBS 2.6.5)

## Context

Every prior "this creates a change" module in this plane — `config_studio.request_promotion`,
`drift_review.approve`, `autonomy_admin.set_level`, `approvals.approve` (S6.2.1, the story
directly before this one) — deliberately stopped at writing a local log file, with the same
repeated rationale: the change each of those proposes still needs a human's review before it can
honestly land in git. Confirmed exhaustively, again, for this story: the only git invocation
anywhere in this entire codebase is `astra_verification.replay.old_config_from_git`, a read-only
`git show`. Nothing anywhere runs `git add`/`git commit`.

S6.2.2 asks for something categorically different: "every *approved* change committed." By the
time this module runs, an `astra_control.approvals.Approval` record already exists — a human has
already made the decision S6.2.1 exists to capture. Committing it to git is a mechanical,
deterministic recording step, not a second judgment call. That distinction — already decided vs.
still needs a human — is the whole reason every prior module stayed away from git and this one
does not.

`PROVENANCE.json` is not a new idea either — two real, independently hand-rolled writers already
exist (`astra_data.render.write_bundle`, `astra_data.migration._write_run`), each with its own
shape but sharing one real convention: a `{"files": {relative_path: sha256}}` digest map,
serialized with `json.dumps(..., indent=2, sort_keys=True) + "\n"`.

## Decision

1. **`commit_approval` stages *exactly* the artifact paths given, plus the `PROVENANCE.json` it
   writes — never `git add -A` or `.`, and it refuses outright if the repository already has
   anything else staged.** `git commit` itself is given the same explicit pathspec a second time,
   as a belt-and-suspenders measure: even if the staging step were somehow wrong, the commit
   itself still cannot include more than the paths this call named. A caller's own unrelated,
   in-progress work — staged or merely dirty in the working tree — is never swept in.

2. **`PROVENANCE.json` reuses the one real convention both existing writers already share** (the
   `{"files": {...sha256}}` digest map, the same `json.dumps` formatting) rather than inventing a
   third shape, and puts the real `Approval` record itself under `"approval"` — who, when, the
   evidence path, the agent version, the autonomy level — in place of either existing writer's own
   free-form `"inputs"`, since that is the one thing an *approval's* own provenance actually needs
   to prove that a config-render's or a migration's provenance does not.

3. **`verify_committed` is the literal, checkable meaning of "no artifact exists only in the
   factory database"** — not a policy statement, a function: given a list of paths, it names
   exactly which ones `git ls-files` does not track. A screen or a CI check can call it directly
   rather than trusting the claim.

4. **Every test and every live example in this story's own runbook targets a disposable, scratch
   git repository — created and destroyed for that one check, never this session's own project
   repository.** The live CLI sanity check for this story was run the same way: a temporary repo
   under the scratchpad directory, `git log` confirmed on the real project repo before and after
   to prove nothing leaked.

5. **Granted to `Role.ENGINEER` alone** — this story's own actor is "a data engineer," and turning
   an already-made approval into a commit is exactly the kind of mechanical step this plane's own
   `_WRITE_PERMISSIONS` already treats as engineer's own (`BOARD_ADD`/`BOARD_MOVE`/`DRIFT_REVIEW_
   APPROVE` — state changes following a decision already made elsewhere), not a second review a
   steward would perform.

## Consequences

- `git-provenance commit|verify` take the same optional `--role` every command in this plane
  does; `verify` is a read, available to every role; `commit` is engineer's own.
- This module is a real capability with a real external effect (a git commit) — the first of its
  kind in this codebase. Nothing in `control/`'s own CLI wires it to run automatically on
  anything; a caller (a human, or a future CI step) invokes it deliberately, per approval, the
  same "compose it, a caller decides when to run it" posture every other action-with-real-effect
  in this plane already has (`golden-viewer`'s own capture command, `parity-viewer`'s own
  captured report).
- No rendered "commit this approval" screen exists after this story — the same honest gap every
  prior Control-plane ADR has already named for its own piece; this is the tested commit,
  provenance and verification logic a screen (or a CI step) would be built on top of.
