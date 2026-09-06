# astra-knowledge

The knowledge plane of Astra Data Factory (product spec Section 5): what the factory knows. Today it holds the spec registry (S2.1.1); the pattern library, domain packs and the rule catalog (E2) join it here. It depends on `astra-core` and on nothing above it; `astra-data` (generation) depends on it.

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

## What is checked

Per file: schema shape; version equals the file name and id the directory; a valid effective date; a fixed-width file has a record length and every field a position within it; no two fields overlap; the picture's width equals the position length; the declared type fits the picture; dates and times have a format; codes are unique; a sign field names a field of the same record; several detail record types have names and match rules; every citation has a page or a line.

Across the registry: no two versions come into force for the same custodian and file type on the same date.

## Layout

```
src/astra_knowledge/
  schemas/source-spec-v0.schema.json   the Source Spec schema, versioned by spec_version
  picture.py                           COBOL-style pictures: width, digits, scale, sign
  registry.py                          loading, checks, resolution
  cli.py
tests/                                 run against the shipped registry and temporary variants of it
```
