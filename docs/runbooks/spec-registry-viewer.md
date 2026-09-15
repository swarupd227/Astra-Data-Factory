# Runbook: browse and compare a Source Spec

Story S6.3.4 (ADR 0060). Every field of a spec version — position, type, a real citation
reference — and a version compare that highlights added, removed and shifted fields. No
credentials: reads the spec registry already on disk.

## Browsing one version

```bash
astra-control spec-viewer show --specs specs --id pershing_gcus --version 2017-07-25
```

One row per field, across every record: start, length, end, type, the citation's own text, and
`Open` — a real reference (`<document>#page=N&line=M`) built from the field's own citation, the
same document the Spec Reader cited when it first drafted this field.

## Comparing two versions

```bash
astra-control spec-viewer compare --specs specs --id pershing_gcus --old 2017-07-25 --new 2026-01-01
```

Exit 0 means the two versions have no differences; exit 1 means they do. Each row names the
record, the field, and one of:

- **added** — the field exists only in the newer version.
- **removed** — the field exists only in the older version.
- **shifted** — the same field, present in both, at a different position — the one change that
  can silently break a downstream mapping with nothing else about the field ever appearing to
  change.
- **changed** — same position, but its type, declared codes, or required flag differs.

## Deciding

- **A field shows `shifted`**: check every mapping that reads it (`astra-control diff-review run`
  against the config that maps this spec) — its own position moved, and nothing downstream that
  reads a fixed offset was told.
- **`compare` exits 1 but nothing looks concerning**: read the actual rows before promoting a
  config against the new version — `added`/`removed` on a filler or reserved field is often
  harmless, but the tool does not make that judgement for you.
- **A citation link 404s or looks wrong**: the field's own citation may have no `document` set
  (it falls back to `"<spec id> <spec version> layout document"`, a label, not a real file) — the
  Spec Reader's own draft is where that gets fixed, not this viewer.

## Notes

- The example in this runbook is real, not staged: `specs/pershing_gcus`'s own two committed
  versions genuinely differ by one added field (`lot_id`) and one shifted field (`filler`, pushed
  from position 67 to 79 by `lot_id`'s own insertion) — `compare` finds exactly this.
- `spec-viewer show`/`compare` take the same optional `--role` every other read command in this
  plane does (ADR 0057) — every role may read either.
