# Runbook: run a custodian through config studio

Story S6.1.2 (ADR 0055). A guided `profile -> draft -> dry_run` sequence on top of the factory
board (ADR 0054), plus a promotion-request log — self-service for a simple-tier custodian, with
no engineer involved. No credentials, no live Postgres: config studio never runs Profiler, Modeler
or a dry run itself — those stay `astra-agents profiler run`, `astra-agents modeler run` and
`astra-verify dryrun run`, each its own step with its own real inputs.

## The sequence

```bash
astra-control config-studio start   --board board.yaml --custodian pershing --stream envestnet-custodial --by bsa@example.com
astra-control config-studio advance --board board.yaml --custodian pershing --to draft    --by bsa@example.com
astra-control config-studio advance --board board.yaml --custodian pershing --to dry_run  --by bsa@example.com
```

`start` puts the custodian on the board at `profile` — do this once the sample has actually been
profiled (`astra-agents profiler run`). `advance` moves exactly one step forward; skipping a
station, or moving anywhere but `profile -> draft -> dry_run`, is refused (exit 2) with a message
naming the sequence. `--by` is required on every call — this is how "every step is recorded with
who and when" is actually true, not just documented; it is stored on the board's own transition
history (`board show` has a By column) rather than a second, separate log.

## Requesting promotion

```bash
astra-control config-studio request-promotion --board board.yaml --requests requests.yaml \
  --custodian pershing --tier simple --requested-by bsa@example.com --note "profiled and dry-run clean"
```

Needs the custodian to have already reached `dry_run` — request too early and it is refused,
naming the station it is actually at. `--tier simple` needs nothing else: this is the self-service
path the story is named for, no engineer involved. Any other tier needs `--reviewed-by` too:

```bash
astra-control config-studio request-promotion --board board.yaml --requests requests.yaml \
  --custodian pershing --tier medium --requested-by bsa@example.com --reviewed-by steward@example.com
```

Omit `--reviewed-by` for a medium or complex request and it is refused outright (exit 2) — nothing
is written to the log, never recorded and flagged for later.

## Reading requests

```bash
astra-control config-studio show-requests --requests requests.yaml                    # everyone
astra-control config-studio show-requests --requests requests.yaml --custodian pershing --json
```

## Deciding

- **`advance` is refused, "one station at a time"**: the custodian has to reach the target station
  through the sequence, not skip to it — check `board show` for where it actually is.
- **`request-promotion` is refused, "needs a steward's review"**: this is not simple tier; get a
  `--reviewed-by` from whoever actually reviewed it before requesting again.
- **A promotion is requested but the custodian never reaches `dual_run`**: config studio does not
  grant a promotion, only requests one — moving to `dual_run` is a separate, later approval this
  tool does not perform.

## Notes

- `control/examples/promotion-requests.yaml` requests promotion for `tableau-gl`, already at
  `dry_run` in the real S6.1.1 example board (`control/examples/board.yaml`) — the two examples
  are meant to be read together, not regenerated separately.
- No real simple-tier source exists in this repository yet (`configs/examples/pershing_position.
  yaml` is `tier: medium`); every example and test here passes a tier value directly rather than
  one a real Pattern Matcher run assigned.
