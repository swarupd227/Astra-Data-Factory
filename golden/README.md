# Golden datasets

The legacy path's own outputs, captured per custodian and business day as the oracle for parity (ADR 0031). One directory per pilot custodian:

```
golden/<custodian>/
  capture.yaml     where the historical files are, how a day is replayed through Splitter/Loader in non-production, the queries that read the outputs and rejections back
  datasets.json    the index: every captured version with its business date, hash, source files, row counts and store reference; appended, never rewritten
  parity.yaml      how a captured output compares with a lakehouse table (S4.2.1): keys, fields, tolerances
  connector.yaml   which client DQ tool (iceDQ, Datagaps) reads the same two things, and the role it connects with (S4.2.4)
```

The datasets themselves live in the golden bucket, one version per capture under `<custodian>/<business date>/v<n>/`: `sources.json` (the files with their hashes), one CSV per output, `replay.log`, and `manifest.json` whose hash is the hash of everything in it. The bucket's object lock keeps a written version read-only; a capture that produces identical content adds no version.

```bash
astra-verify golden check golden                                   # every pull request: capture files valid, indexes well formed
astra-verify golden status golden                                  # business days captured per custodian, against the 30 to 60 the replay needs
astra-verify golden capture golden/pershing --from 2026-06-01 --to 2026-08-29 --store s3://astra-dev-golden-123456789012
astra-verify golden verify golden/pershing --store s3://astra-dev-golden-123456789012   # every indexed version still hashes as captured
astra-verify parity check golden                                   # every pull request: parity mappings valid
astra-verify parity run --config golden/pershing/parity.yaml --business-date 2026-08-03 --environment dev --store s3://astra-dev-golden-123456789012
astra-verify parity report --config golden/pershing/parity.yaml --environment dev --store s3://astra-dev-golden-123456789012
astra-verify connector check golden                                # every pull request: connector configs valid
astra-verify connector describe --config golden/pershing/connector.yaml --environment dev --store s3://astra-dev-golden-123456789012
astra-verify connector store-result --config golden/pershing/connector.yaml --business-date 2026-08-03 --file icedq-export.csv
```

`parity report` runs every already-captured business date (or a `--from`/`--to` window), aggregates the match rate, difference groups and trend, and exports `report.md` / `report.json` into `releases/<source>-parity/` for every source `capture.yaml` names — the release evidence a gate decision is made from (S4.2.2, ADR 0034).

`connector describe` renders a connection sheet for the client's own DQ tool from `connector.yaml` and the same `parity.yaml`, so both tools reconcile identically; `connector store-result` files the tool's own exported result, hashed, next to `astra-verify parity run`'s own report for the business date (S4.2.4, ADR 0036).

The first capture follows [docs/runbooks/golden-capture.md](../docs/runbooks/golden-capture.md).

The captured business days are also what `astra-verify replay` (S4.1.3, ADR 0032) replays a drafted config change against: it reads this index for the N most recent dates and fetches the same files `capture.yaml` used, so a replay and a capture never disagree about what "history" means.
