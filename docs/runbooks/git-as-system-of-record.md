# Runbook: commit an approved change, with real provenance

Story S6.2.2 (ADR 0071). Every approved change committed with a real `PROVENANCE.json`, so no
artifact exists only in the factory's own local file stores. This is the first command in this
whole CLI that actually mutates git history — read this runbook's own "Trying it safely" section
before running it against anything you care about.

## Committing an approval

```bash
astra-control git-provenance commit \
  --repo /path/to/the/repo \
  --approvals releases/pershing_gcus/approvals.yaml --subject pershing_gcus.quantity_sign \
  --artifact rules/pershing_gcus/quantity_sign.yaml \
  --provenance-path PROVENANCE.json \
  --role engineer
```

`--subject` must already have a real entry in the given `--approvals` log (`astra-control
approvals approve` writes it — S6.2.1). Every `--artifact` (repeatable) must be a real file
already inside `--repo`. This stages **exactly** those artifacts plus the `PROVENANCE.json` it
writes — nothing else in the working tree, ever — and refuses outright, nothing committed, if the
repository already has something else staged. One real commit, referencing the approval's own
subject and approver in its message (or `--message` for your own).

## Verifying nothing is factory-only

```bash
astra-control git-provenance verify \
  --repo /path/to/the/repo \
  --artifact rules/pershing_gcus/quantity_sign.yaml --artifact PROVENANCE.json
```

Exit 0 means every given artifact is tracked by git; exit 1 names exactly which ones are not —
the literal, checkable meaning of "no artifact exists only in the factory database."

## Trying it safely

**Never point `--repo` at a repository you care about while experimenting.** Every test for this
command, and this runbook's own examples, target a disposable, scratch repository:

```bash
mkdir /tmp/scratch-repo && cd /tmp/scratch-repo
git init && git config user.email you@example.com && git config user.name "You"
echo "hello" > README.md && git add README.md && git commit -m "initial commit"
```

Then point `--repo` at `/tmp/scratch-repo` and delete the directory when you are done. `git-
provenance commit` performs a real `git commit` — there is no dry-run mode.

## Deciding

- **`error: already has staged changes`**: something else is staged in `--repo` that this call did
  not put there — commit or unstage it yourself first; this command never sweeps up work that
  isn't its own.
- **`error: not found`**: an `--artifact` path does not exist — check it is a real file, and that
  it is given relative to (or resolves inside) `--repo`.
- **`verify` names an artifact still untracked after a `commit`**: check `--provenance-path` and
  every `--artifact` were given to the same `commit` call — only the ones actually staged there
  get committed.

## Notes

- `PROVENANCE.json`'s own shape reuses the one convention `astra-data render` and `astra-data
  migrate run` already share (a `{"files": {path: sha256}}` digest map) — `"approval"` carries the
  real `Approval` record (who, when, evidence, agent version, autonomy level) in place of either
  existing writer's own free-form inputs.
- `git-provenance commit|verify` take the same optional `--role` every command in this plane
  does; `verify` is a read, available to every role. `commit` is granted to engineer alone — this
  story's own actor.
