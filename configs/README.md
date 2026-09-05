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

A problem is reported with file, line and a plain sentence: a missing field, an unknown field with the allowed ones listed, a value outside its allowed set, a mapping that references a rule that does not exist or has been rejected, duplicate ids, a file whose name does not match its `source.id`.

The schema is versioned by `config_version` and lives at `generation/src/astra_data/schemas/config-v<n>.schema.json`. Version 0 fixes identity, ownership, references and the shape of rules; field-level mapping semantics arrive with the config compiler (S3.1.1).

Files starting with `_` are ignored, so a draft can sit beside real configs without failing the build.
