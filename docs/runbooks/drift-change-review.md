# Runbook: review and approve a detected drift

Story S6.3.9 (ADR 0065). A detected layout change beside its proposed spec delta, an optional
config delta, and the impact list — approve it into a non-prod change-request log, never a git
commit, never a write to `specs/` or `configs/`.

## Reviewing a drift

```bash
astra-control drift-review show \
  --drift-report work/drift-watcher/pershing_gcus/2017-07-25/report.json
```

Shows every raw finding (`record_length`/`new_code`, current vs proposed), the spec field diff
(built by applying the findings to a candidate spec purely in memory and comparing it with the
real registered version — `astra-control spec-viewer compare`'s own logic, reused directly), and
the impact list: the spec's own `custodians` field, always; `consumers` only when given.

```bash
astra-control drift-review show \
  --drift-report work/drift-watcher/pershing_gcus/2017-07-25/report.json \
  --old-config configs/examples/pershing_position.yaml \
  --new-config work/pershing_position_candidate.yaml \
  --consumer tamarac --consumer reporting-warehouse
```

`--old-config`/`--new-config` add a real config diff — given only when an engineer has already
drafted a real candidate config reflecting the spec change (this command never synthesizes one
itself; see Notes). `--consumer` is repeatable — nothing in this repository tracks a downstream
Gold consumer, so this is the only way one shows up here.

## Approving into non-prod

```bash
astra-control drift-review approve \
  --drift-report work/drift-watcher/pershing_gcus/2017-07-25/report.json \
  --requests releases/pershing_gcus/drift-change-requests.yaml \
  --approved-by engineer@example.com --note "Widen to 125, allow CD in non-prod first." \
  --role engineer
```

Appends one entry — spec id, spec version, who, when, an optional note — to the given
`--requests` log. **This is the entire effect.** Nothing is written to `specs/` or `configs/`;
nothing is committed, branched or pushed. A human (or CI, reading this log) turns the approved
entry into a real git change through this repository's own normal pull-request flow.

```bash
astra-control drift-review show-requests --requests releases/pershing_gcus/drift-change-requests.yaml
```

## Deciding

- **Spec field diff is empty but Findings shows a `record_length` change**: that is expected — a
  `record_length` change is a file-level attribute, not a field, so `compare` never shows it; read
  the Findings table for it, not the field diff.
- **No config diff shown**: no `--old-config`/`--new-config` were given, or no candidate config has
  actually been drafted yet for this drift. This command does not invent one.
- **"Consumers: not tracked"**: no `--consumer` was given — this repository has no data source for
  downstream Gold consumers, so this is the honest default, not a bug.
- **`approve` refuses "approved_by must be given"**: `--approved-by` was blank or all whitespace —
  nothing was written; retry with a real identity.

## Notes

- `drift-review approve` never invokes git and never writes to `specs/` or `configs/` — checked
  exhaustively across this codebase, nothing does. The change-request log is a real, working
  stand-in for the pull request a human or CI opens next, the same "Git is the system of record"
  boundary this repository's own root `README.md` already states.
- `drift-review show|approve|show-requests` take the same optional `--role` every command in this
  plane does; `show`/`show-requests` are reads, available to every role. `approve` is granted to
  both engineer and steward — the story's own actor.
