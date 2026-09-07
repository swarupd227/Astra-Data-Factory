# configs

Source configs: one versioned instance of a Source Spec mapped to the canonical model under a Target Profile, with rules, DQ rules and resolution parameters (product spec Section 4). YAML, in Git, reviewed like code.

## Layout

```
configs/
  <custodian>/
    <source id>.yaml      # file name equals source.id
  examples/
    pershing_position.yaml
```

## Validation

Every pull request validates every file here against the config schema and its references:

```bash
astra-data validate configs            # from the repository root, after `pip install -e generation`
astra-data --format github validate    # what CI runs: one annotation per problem, on the file and line
```

A problem is reported with file, line and a plain sentence: a missing field, an unknown field with the allowed ones listed, a value outside its allowed set, a mapping whose rule is not listed under `rules`, a rule that is not in the catalog or has been rejected by its owner (with `--rules rules`), duplicate ids, a file whose name does not match its `source.id`.

The schema is versioned by `config_version` and lives at `generation/src/astra_data/schemas/config-v<n>.schema.json`. Version 0 fixes identity, ownership and references; rules are catalog ids (`rules/<group>/<name>.yaml`, see [rules/README.md](../rules/README.md)); field-level mapping semantics arrive with the config compiler (S3.1.1).

`processing.target_lag_minutes` (default 15) is the TARGET_LAG of the parsed dynamic tables and the interval of the source's process task.

A mapping carries either `source` (a field of the spec's logical record) or `constant` (a fixed value typed by the target column); a source maps into one canonical entity and must produce every key and required column of it, through mappings, constants, the resolution or the stage. `resolution` says how custodian identifiers become platform identifiers as set-based joins against the replicated reference data: `account` (the source field with the custodian's account number), `security` (identifiers tried in order, each with the source field that carries it), `transaction_code` (the custodian's code and its map to canonical types) and `price` (looked up from `SILVER.PRICE` when missing or always, within a lookback). Each names the rejection code its failure raises, defaulting to the taxonomy's (`ACCOUNT_NOT_FOUND`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `TRANSACTION_CODE_UNMAPPED`, `PRICE_MISSING`); see [ADR 0022](../docs/adr/0022-resolution.md).

`astra-data compile` goes further than validation: it resolves the spec version, the pattern, the target profile, the domain pack's latest canonical model and the catalog rules, and checks every mapping's target column, source field and transform for type agreement. Mappings name the spec's logical record with `record` when it has more than one; transforms come from the vocabulary in `generation/src/astra_data/transforms.py`. CI compiles every config.

Files starting with `_` are ignored, so a draft can sit beside real configs without failing the build.

## Delivery expectations and alert severity

Two optional blocks drive the alerts of S1.2.4. Every source of the same custodian must agree on them; the file lists are merged.

```yaml
delivery:
  cutoff_time: "06:00"            # HH:MM in the custodian's timezone
  timezone: America/New_York      # IANA name
  business_days: [mon, tue, wed, thu, fri]   # default
  refresh_expected_every_days: 7  # a full refresh file at least this often, else a refresh_stale alert
  files:
    - pattern: pershing/GCUS_%_POS_%.dat     # SQL LIKE pattern relative to the landing prefix
      description: Positions

alerts:
  late: error                     # severity when expected files are missing after the cutoff
  task_failure: error             # severity when one of the custodian's tasks fails
```

Severities are `info`, `warning`, `error` or `critical`; which channels each reaches is operations data in `CONTROL.ALERT_ROUTES`. The deploy pipeline runs `astra-data custodians sync` after the bundles, so `CONTROL.CUSTODIANS` and `CUSTODIAN_FILES` always reflect what was merged. `astra-data custodians render --environment dev` prints the SQL without connecting.
