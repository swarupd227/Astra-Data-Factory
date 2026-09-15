# Runbook: check a custodian's golden dataset coverage

Story S6.3.10 (ADR 0066). Which business days are captured, each with its own real hash, and
which are gaps — with the exact command that would fill one.

## Showing the calendar

```bash
astra-control golden-viewer show \
  --capture golden/pershing/capture.yaml \
  --golden-dir golden \
  --from 2026-06-01 --to 2026-08-29 \
  --store s3://astra-dev-golden-123456789012
```

`--capture` is the custodian's own `capture.yaml` — its `business_days` field says which weekdays
are ever expected to have a capture, the same calendar the real `astra-verify golden capture` CLI
itself walks. `--golden-dir` names where `<custodian>/datasets.json` lives (default: `golden`) —
the real index the same CLI already appends to. `--from`/`--to` is the window to check.

Every captured version shows with its own hash, capture time and row counts — a day recaptured
with different content shows every version, not just the latest. Exit 1 means at least one gap
exists in the window; exit 0 means the window is fully covered.

## Requesting a capture

Nothing here runs a live capture — no environment this repository runs in has a real non-prod
Loader connection, historical-file location, or `loader-replay` binary. Each gap's own row already
shows the exact command:

```bash
astra-verify golden capture golden/pershing/capture.yaml --from 2026-06-05 --to 2026-06-05 --store s3://astra-dev-golden-123456789012
```

Copy it as-is (with `--store` pointing at the real golden bucket) and run it from an environment
with the real connection this custodian's `capture.yaml` names.

## Deciding

- **A gap's own command has a `&lt;golden store...&gt;` placeholder**: no `--store` was given to
  `golden-viewer show` — pass the real bucket to get a command that can be copy-pasted as-is.
- **"outside 30 to 60"**: the custodian's own total captured business days (across its whole
  index, not just the requested window) is below the minimum or above the maximum the replay
  needs (S4.1.2's own AC) — not itself a gap in the requested window, but worth widening the
  window or capturing more days regardless.
- **A day shows two rows, two hashes**: it was captured twice with genuinely different content — a
  legitimate history, not a bug; the golden store never overwrites a version.

## Notes

- No real committed golden index exists in this repository (`golden/pershing/` has only its own
  `capture.yaml`, `connector.yaml` and `parity.yaml`) — a real `datasets.json` only exists once
  `astra-verify golden capture` has actually run somewhere with a live connection.
- `golden-viewer show` takes the same optional `--role` every command in this plane does; every
  role already reads it (this story adds no write action).
