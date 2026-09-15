# Runbook: read a custodian's page

Story S6.3.3 (ADR 0059). Family, tier, config version, current station, files today against the
cutoff, parity trend, open exceptions and cost — all in one place, each field read from something
this factory already built. No credentials.

## Running it

```bash
astra-control custodian-page show --config configs/pershing/pershing_position.yaml \
  --board board.yaml \
  --parity-report work/parity-report/pershing/report.json \
  --exception-report work/exception-triage/report.json \
  --arrivals arrivals.yaml \
  --cost 4.82
```

Every flag past `--config` is optional; a field with no source shows honestly as "not on the
board," "no parity report given," "0," or "no data — the FinOps agent is not built yet" rather
than a guess. `--arrivals` is a small file mapping the config's own `delivery.files[].pattern` to
a real arrival time — a stand-in for a live file-load log:

```yaml
arrivals:
  pershing/GCUS_%_POS_%.dat: "2026-09-15T05:41:00Z"
```

Read the page:

1. **Family, tier, config version** — the compiled config's own facts; config version is
   `effective_from` (human-readable) alongside the config's own content hash (precise).
2. **Current station** — read straight off the board given with `--board`; `not on the board`
   when none is given or this custodian isn't on it.
3. **Files today** — every expected file against the cutoff, `not yet` for anything `--arrivals`
   doesn't name.
4. **Parity trend** — the real match rate, target and trend from `--parity-report`, unmodified.
5. **Open exceptions** — the same count `astra-control queue show` would show for this source,
   from `--exception-report`.
6. **Cost** — only ever what `--cost` was given; no live source computes one.
7. **Links** — `spec` and `config` are real files; `parity_viewer`/`exceptions` point at the real
   report given, with a note that the viewer screen itself isn't built yet; `config_diff` is a
   runnable command (this config needs an earlier version to actually diff against).

## Deciding

- **A field says "no data" or "not yet"**: that source genuinely was not given, not a bug —
  supply the flag once the real source exists.
- **Files today shows nothing arrived**: check whether `--arrivals` was given at all and whether
  its own pattern strings match the config's `delivery.files[].pattern` exactly.
- **You want the actual config diff, not just the command hint**: run the printed
  `astra-control diff-review run` command yourself with a real earlier version as `--old`.

## Notes

- `control/examples/arrivals.yaml` is illustrative — no live ingestion pipeline populates it in
  this repository; a platform's real Snowpipe/file-load-log integration would.
- No FinOps agent exists in this repository's own Agents-plane backlog, so cost has no live
  source at all; `--cost` is always caller-supplied.
- `custodian-page show` takes the same optional `--role` every other read command in this plane
  does (ADR 0057) — every role may read it.
