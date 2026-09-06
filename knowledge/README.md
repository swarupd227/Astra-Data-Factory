# astra-knowledge

The knowledge plane of Astra Data Factory (product spec Section 5): what the factory knows. It holds the spec registry, the pattern library and the domain packs; the rule catalog (F2.4) joins them here. It depends on `astra-core` and on nothing above it; `astra-data` (generation) depends on it.

```bash
cd knowledge
python -m venv .venv && . .venv/bin/activate
pip install -e ../core -e ".[dev]"
pytest
```

## Spec registry

Source Specs live in [specs/](../specs/README.md), one directory per layout, one file per version. The registry loads them, validates each file and the set as a whole, and resolves which version is in force:

```python
from datetime import date
from astra_knowledge import Registry

registry, problems = Registry.load("specs")
spec = registry.resolve("pershing", "position", date(2026, 9, 6))   # the version in force
spec.record("detail").field("quantity").picture.scale                 # 5 implied decimals
```

| Command | What it does |
|---|---|
| `astra-spec validate` | Every spec file against the schema and its own consistency, plus registry-wide checks |
| `astra-spec list` | Every version with its effective date, file type and custodians |
| `astra-spec resolve --custodian <id> --file-type <type> --date <YYYY-MM-DD>` | The version in force on a business date; exit 1 when none |
| `astra-spec show --id <spec> --version <version>` | Every field with position, picture, type and citation |
| `astra-spec search [--custodian] [--family] [--file-type] [--date] [--all-versions]` | Ranked matches: specs listing the custodian first, then specs of the family, then file-type-only; newest first within a rank. Specs with no family are flagged. |
| `astra-spec unclassified` | Specs with no family, waiting for the Pattern Matcher or an architect |

Search is what the Pattern Matcher (S5.3.1) calls: for a new custodian it searches by custodian and the family it suspects, and gets the exact custodian's spec first, then reuse candidates from the same family. With a date, only versions in force on that date are considered; one version per spec is returned unless `--all-versions` is given. `validate` prints a warning for every spec without a family.

`--format github` prints workflow annotations so a failing pull request shows each problem on its file and line. `--format json` is for tools.

## Pattern library

A pattern is a reusable way to handle a class of source (product spec Section 4): an id, what it applies to, a reference implementation that turns a file into typed rows, and tests. The generation plane renders the same semantics into native code for the target; the reference implementation is the oracle rendered code is checked against.

| Pattern | Applies to | What it does |
|---|---|---|
| `fixed_width_multi_record` | `file.format: fixed_width` | Reads the record type of each line from the configured position, fixed-length or up to an end marker; parses header and trailer into file metadata and every detail record into one typed row; reports problems per line and field without stopping |
| `delimited_file` | `file.format: delimited` | Comma, pipe or any single-character delimiter; quoted fields with doubled or escaped quotes; `header_rows` skipped and the first checked against field labels; record types by column. A row whose column count differs from `file.column_count` rejects the file (a file-level problem), though readable rows are still returned. Numbers are explicit ("-123.45") unless a picture declares implied decimals. |

| pairing (spec block) | any spec with `pairing` | After parsing, joins the listed detail record types into one logical record per key (for example account and CUSIP), merging their fields; keys appear once and a field name shared by both records is prefixed with its record label. A record whose partner never appears is reported as `PAIR_INCOMPLETE` with its line and keys; a second record of the same type for the same keys is `PAIR_DUPLICATE`; a blank key is `PAIR_KEY_BLANK`. `parse()` applies it; `parse(..., pairing=False)` returns the physical records. |

```yaml
pairing:
  - name: position
    records: [holding, valuation]      # detail record labels, in merge order
    keys: [account_number, cusip]      # fields present in each, equal values pair them
```

| merge (spec block) | any spec with `merge` | Decides how a parsed file changes Silver from a header flag: **refresh** replaces every row of the file's scope (for example the remote id) as of the file's business date, inserting or updating the rows in the file and retiring the rest; **update** merges on keys and carries every other row forward untouched. `patterns.merge` is the reference implementation over an in-memory Silver state and the oracle for the rendered MERGE (S3.2.3). An unknown mode, a missing header or a business date earlier than what Silver already holds for the scope is a file-level problem (`MERGE_MODE_UNKNOWN`, `MERGE_OUT_OF_ORDER`) and nothing is merged; duplicate or blank keys within a file are `MERGE_DUPLICATE_KEY` and `MERGE_KEY_BLANK`. Every application yields the `MERGE_LOG` entry the platform records, from which a refresh not seen for N days is alerted. |

```yaml
merge:
  mode_field: refresh_flag              # header field carrying the mode
  modes: { R: refresh, U: update }      # its codes, both modes required
  scope: [remote_id]                    # header fields identifying what a refresh replaces
  business_date_field: file_date        # header date field
  keys: [account_number, cusip]         # keys of the merged (logical) record
```

| split (spec block) | any spec with `split` | After parsing and pairing, turns one custodian record into several canonical records. A rule names the logical record, the field and codes that trigger it, and two or more parts; each part sets values (a code, a cleared quantity), negates numeric fields, and keeps every other value. The DRIP example yields a `dividend` and a `purchase` from one line. Every part records its origin: the source record label, the rule name, its position in the rule and the line number. `parse(..., splitting=False)` returns the unsplit rows. |

```yaml
split:
  - name: drip
    record: detail
    when: { field: transaction_type, equals: DRIP }     # or `in: [A, B]`
    into:
      - { name: dividend, set: { transaction_type: DIV, quantity: null } }
      - { name: purchase, set: { transaction_type: BUY }, negate: [amount] }
```

| lifecycle (spec block) | any spec with `lifecycle` | Cancels and corrections. The block names the action field and which codes mean `new`, `cancel` and `correct`, the fields that identify a record and the fields of a cancel or correction that name the original. `patterns.lifecycle.apply_lifecycle` runs a parsed file against a state of transactions: a cancel marks the original `cancelled` and points at the cancel, and the cancel record points back at the original; a correction marks the original `superseded`, points at the correction, and the correction is the active record. Split parts share their line's identity, so a cancel of a DRIP line closes both the dividend and the purchase. Problems: `LIFECYCLE_ORIGINAL_MISSING`, `LIFECYCLE_ALREADY_CLOSED`, `LIFECYCLE_DUPLICATE`, `LIFECYCLE_IDENTITY_BLANK`. |

```yaml
lifecycle:
  record: detail
  action_field: action
  actions: { N: new, X: cancel, C: correct }
  identity: [transaction_id]                  # what identifies a record
  reference: [original_transaction_id]        # how a cancel or correction names it
```

```python
from astra_knowledge.patterns import SilverState, merge, parse
state = SilverState()
result = merge(state, spec, parse(spec, open("GCUS_20260905.dat")), "GCUS_20260905.dat")
result.mode, result.inserted, result.updated, result.carried, result.retired
result.log_entry(spec, "GCUS_20260905.dat")   # what CONTROL.MERGE_LOG records
```

A parse result carries problems at three levels: `file` (the whole file is unusable: column count mismatch, header mismatch, missing trailer, malformed quoting), `record` (a line could not be placed) and `field` (one value). `ParsedFile.rejected` is true when any file-level problem exists.

```python
from astra_knowledge.patterns import parse
parsed = parse(spec, open("sample.dat"))
parsed.metadata["header"]["file_date"]     # date(2026, 9, 5)
parsed.rows[0].values["quantity"]          # Decimal('123.45678'), sign applied from quantity_sign
parsed.problems                            # [RowProblem(line_number=3, message="sign is blank; ...", ...)]
```

```bash
astra-spec parse --id pershing_gcus --version 2017-07-25 sample.dat      # rows, metadata, problems; --format json
```

Value rules, shared by every pattern: strings lose trailing spaces and blank is NULL; a code is kept as written when declared (a blank code is a code) and reported when not; integers are digits; decimals take their scale from the picture and their sign from the separate sign field, where a blank sign makes the value NULL, not zero; dates and times follow the declared format and all zeros is NULL.

### Signed implied-decimal numerics

One function, `patterns.numerics.signed_implied_decimal(digits, sign, scale, convention)`, turns unsigned digits plus a separate sign and implied decimals into a number. The sign convention is configuration: the codes of the sign field in the spec say which values mean positive, negative or unknown (`+`/`-`/blank by default; `C`/`D` or `P`/`N` are a spec change, not a code change):

```yaml
- name: quantity
  picture: 9(13)V9(5)
  sign_field: quantity_sign
- name: quantity_sign
  codes:
    - { value: "+", meaning: long, sign: positive }
    - { value: "-", meaning: short, sign: negative }
    - { value: " ", meaning: unknown, sign: unknown }
```

| digits | sign | result |
|---|---|---|
| `000000000012345678` | `+` | `123.45678` |
| `000000000012345678` | `-` | `-123.45678` |
| `000000000012345678` | blank | NULL, with "sign is blank; the value is unknown, not zero" |
| `000000000012345678` | `X` | NULL, with "sign 'X' is not an accepted sign" |
| `000000000000000000` | blank | `0` (nothing to sign) |

`sign_style: leading` handles a sign character at the start of the digits instead. The same rules ship for Snowflake as `CONTROL.IMPLIED_DECIMAL`, `CONTROL.SIGNED_IMPLIED_DECIMAL` and `CONTROL.SIGNED_IMPLIED_DECIMAL_PROBLEM` (`infra/terraform/foundation/numerics.tf`), so rendered parse code and the reference implementation use one definition.

## Domain packs and the canonical data model

A domain pack is a directory under `domains/` (product spec Section 4): `glossary.yaml` and `cdm/<major>.<minor>.yaml`, one file per canonical model version. `astra_knowledge.cdm` loads and validates it, classifies the changes between versions, and renders Snowflake managed Iceberg DDL with key and reference tests. See [domains/custodial/README.md](../domains/custodial/README.md) for the custodial model and the versioning rule; the design is [ADR 0013](../docs/adr/0013-canonical-data-model.md).

| Command | What it does |
|---|---|
| `astra-spec cdm validate` | Every pack: glossary shape, model shape, entity terms against the glossary, keys and references, and the versioning rule between consecutive versions |
| `astra-spec cdm show --domain custodial [--version 1.0]` | Entities with keys, references, definitions and columns |
| `astra-spec cdm diff --domain custodial --from 1.0 --to 2.0` | Every change between two versions, classified breaking or additive |
| `astra-spec cdm render [--check]` | Writes `cdm/rendered/<version>/ddl.sql` and `tests/*.sql`; with `--check`, fails when they are stale (CI runs this) |
| `astra-spec rejections list [--domain d] [--level l] [--owner o]` | The pack's rejection codes with level, severity, owner and Loader codes |
| `astra-spec rejections parity --domain d [--reference file.csv]` | Which Loader codes the taxonomy reproduces and which it misses ([ADR 0014](../docs/adr/0014-rejection-taxonomy.md)) |

| `astra-spec reference list [--domain d]` | The pack's reference-data feeds: what they resolve, how snapshots arrive, when they run ([ADR 0015](../docs/adr/0015-reference-data-replication.md)) |
| `astra-spec rules validate [--configs configs]` | The rule catalog under `rules/`: every rule's shape, history and citation; with configs, every rule reference resolves and none is rejected ([ADR 0016](../docs/adr/0016-rule-catalog.md)) |
| `astra-spec rules list | show <id> | set-status <id> --status s --by who` | Browse the catalog with usage from configs; record a status change with who and when |

Every problem the pattern library reports carries a rejection code from the pack's `rejections.yaml`; `RowProblem.code` is that code.

## What is checked

Per file: schema shape; version equals the file name and id the directory; a valid effective date; a fixed-width file has a record length and every field a position within it; no two fields overlap; the picture's width equals the position length; the declared type fits the picture; dates and times have a format; codes are unique; a sign field names a field of the same record; several detail record types have names and match rules; every citation has a page or a line.

Across the registry: no two versions come into force for the same custodian and file type on the same date.

## Layout

```
src/astra_knowledge/
  schemas/source-spec-v0.schema.json   the Source Spec schema, versioned by spec_version
  picture.py                           COBOL-style pictures: width, digits, scale, sign
  registry.py                          loading, checks, resolution
  patterns/                            the pattern library: reference implementations and their result shape
  schemas/cdm-v0.schema.json           canonical data model versions; glossary-v0.schema.json the pack glossary
  cdm.py                               domain packs: loading, validation, version diff, DDL and test rendering
  rejections.py                        the rejection taxonomy and Loader parity
  reference_data.py                    reference-data feeds; patterns/reference_data.py replicates and resolves against them
  columns.py                           typed columns shared by the model and the feeds
  rules.py                             the rule catalog: loading, validation, status changes with history, lineage to configs
  cli.py
tests/                                 run against the shipped registry and temporary variants of it
```
