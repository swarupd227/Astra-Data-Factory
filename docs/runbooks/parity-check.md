# Runbook: measure parity between the golden legacy output and the lakehouse

Story S4.2.1 (ADR 0033). The parity engine measures, for one custodian and business date, how much of what the legacy path loaded the lakehouse loaded the same way.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. History | QE engineer | The custodian has a golden dataset captured for the business date (S4.1.2): `astra-verify golden status golden` shows the date, or `astra-verify golden capture ...` is run for it. |
| 2. The mapping | QE engineer | `golden/<custodian>/parity.yaml` exists: the golden output that is the oracle, the lakehouse table, the keys and the fields with their tolerances. `astra-verify parity check golden` passes. A tolerance is a judgement call the client confirms — "price to 4 dp" for a price field, an absolute cent for a currency amount, exact for a code or a date. |
| 3. Environment | DevOps | The bundle for the source is deployed to the environment being checked, so the lakehouse table has rows for the business date. |

## Running it

```bash
astra-verify parity run \
  --config golden/pershing/parity.yaml \
  --business-date 2026-08-03 \
  --environment dev \
  --store s3://astra-dev-golden-123456789012
```

The summary line gives the match rate against the 99.5% target and the count of missing, extra and mismatched rows; the full report is `parity.md` next to `parity.json` under `work/parity/<custodian>-<date>/`.

Read the report in order:

1. **Match rate** — the headline number. Below target is not necessarily a bug: check the field summary first.
2. **Differences by field** — a field with most of the mismatches points at one mapping, one rule or one resolution part; that is usually where the real difference is, not spread evenly across every field.
3. **Missing and extra rows** — a row the legacy path loaded that the lakehouse did not (or the reverse) is worth checking against the exception store for that run: a real rejection explains a missing row; an unexplained one does not.

## Deciding

- **At or above target**: the day is in parity; record it as evidence.
- **Below target**: read the field summary and the sample mismatches first. A tolerance too tight for a legitimate rounding difference is a config fix (`golden/<custodian>/parity.yaml`); a real difference is a break for the config or the pattern library to fix, same as any other DQ finding.
- Run parity for several business days before trusting the number for a source; one day's rate is a data point, not a trend — S4.2.2 builds the trend and the evidence-pack export on top of this engine.

## Notes

- `--legacy-version` compares against a specific captured version instead of the latest for the date, useful when re-checking parity after a Loader fix that produced a new version.
- The lakehouse side is a live query against the named environment, not a rerun in a sandbox: the number is what is actually deployed there right now.
