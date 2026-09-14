# Runbook: run the factory board

Story S6.1.1 (ADR 0054). Every custodian at its station, one WIP limit enforced per stream,
custodians live per week. The Control plane's first module — no live Postgres yet; `board.yaml`
is a real, working stand-in for it, read fresh and rewritten on every command.

## Adding a custodian

```bash
astra-control board add --board board.yaml --custodian pershing --stream envestnet-custodial
```

Enters at `profile`, the board's own entry point. Blocked (exit 2) if the stream is already at
its own WIP limit.

## Moving a custodian

```bash
astra-control board move --board board.yaml --custodian pershing --to draft
```

`--to` is one of `profile`, `draft`, `dry_run`, `dual_run`, `cutover`. Any station to any station
is allowed — there is no forward-only sequence enforced here (ADR 0054 point 6). Blocked (exit 2)
only when the move would newly put the custodian's stream over its own WIP limit: entering the
board fresh, or re-entering from `cutover`. A lateral move between two stations short of
`cutover` is never blocked, even for a stream currently over a since-lowered limit.

## Setting a stream's WIP limit

```bash
astra-control board set-wip-limit --board board.yaml --stream envestnet-custodial --limit 3
```

Takes effect immediately; does not retroactively block custodians already in flight (ADR 0054
point 4) — only the next attempt to add or re-enter is checked against it.

## Reading the board

```bash
astra-control board show --board board.yaml               # markdown, one section per stream
astra-control board show --board board.yaml --json         # the same state, machine-readable
```

Per stream: the WIP limit, how many custodians are currently in flight, and a table of every
custodian at its station. A final section: custodians live per week — the ISO week each custodian
most recently moved into `cutover`, not a snapshot of who happens to be live today. Exit 0 when
every stream is within its own limit; exit 1 when at least one is over.

## Deciding

- **`add` or `move` is blocked**: the message names the stream, how many it would put in flight,
  and the limit — either wait for the stream to drain, or raise the limit with `set-wip-limit` if
  the constraint itself has changed.
- **`show` exits 1, a stream is over its own limit**: this can happen legitimately (a limit
  lowered after custodians were already in flight) — the board says so honestly rather than
  silently blocking their own further movement; nothing forces the stream back under the limit
  except those custodians reaching `cutover` or being moved out.
- **Custodians live per week looks low**: check whether recent go-lives were recorded with `move
  --to cutover` at all — the count only reflects transitions this board actually recorded, never
  inferred from elsewhere.

## Notes

- `control/examples/board.yaml` is a committed example: the real `pershing` custodian mid-flight
  in `envestnet-custodial` (at its own WIP limit, 3/3), alongside a second, illustrative stream
  `blackrock-tableau` sitting comfortably under its own limit with one custodian already live —
  no Fidelity, Schwab or BlackRock Tableau source exists in this repository; both stream names
  come straight from the product spec's own text (Sections 1 and 7.1).
- This is domain logic and a CLI, not the rendered Workbench screen a delivery lead will
  eventually click through (React, per the product spec's own architecture) — that is later
  Control-plane work built on top of what this module already gets right and already tests.
