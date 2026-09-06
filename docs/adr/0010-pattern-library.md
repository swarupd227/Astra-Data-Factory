# ADR 0010: The pattern library and its first pattern

Date: 2026-09-06
Status: Accepted
Story: S2.2.1 Fixed-width multi-record pattern (E2, F2.2, WBS 2.2.2)

## Context

The product spec's second principle is "patterns before instances": the first source of a kind is engineered, the rest are configuration. A pattern is stored as a definition, a renderer template and tests. The first pattern must parse fixed-width files with header, trailer and several detail record types so that Pershing-style files need no custom code.

## Decision

1. **A pattern has a reference implementation in the knowledge plane.** `astra_knowledge.patterns` holds a `Pattern` (id, what it applies to, a parse function) and the library of them. The reference implementation turns a file into typed rows and file metadata according to a Source Spec. It is what the profiler, dry-runs and test generation run locally, and it is the oracle: the SQL the generation plane renders for the same spec (S3.2.2) must agree with it row for row. Renderer templates live with the renderers in E3.

2. **The spec drives everything; the pattern adds no configuration of its own.** Record types, match rules, positions, pictures, formats, codes and sign fields all come from the Source Spec (ADR 0009). A layout that validates is parseable.

3. **The record type is read from a configured position**, either a fixed-length token (`match: { position: { start, length }, value }`) or a token from a start position up to an end marker character (`match: { start, end_marker, value }`). Match rules are validated against the file shape: a value must fit its position, a match must fall within the record length, and every record type in a multi-record file must have one.

4. **Value rules are one module, shared by every pattern**: strings lose trailing spaces and blank is NULL; a declared code is kept as written (a blank code is a code) and an undeclared value is reported; integers are digits; decimals take their scale from the picture and their sign from the separate sign field, and a blank sign makes the value NULL rather than zero (the rule S2.2.3 asks for, implemented here because this pattern needs it); dates and times follow the declared format and all zeros is NULL; booleans are Y/N, T/F, 1/0.

5. **Problems do not stop the parse.** A bad value, a blank required field, an unknown record type, a second header, an over-long line or a missing trailer is reported with line, record and field, and parsing continues. Lines shorter than the record length are padded, because custodian files often arrive with trailing spaces trimmed; longer lines are reported.

6. **Header and trailer are file metadata**, keyed by record label; detail records are rows keyed by their label, so files with several detail types keep them apart. Counts per record type are returned for control-total checks.

## Addendum: the delimited-file pattern (S2.2.2, 2026-09-06)

`delimited_file` handles comma, pipe or any single-character delimiter with quoted fields, doubled or escaped quotes, and optional header rows, through the standard library's CSV reader in strict mode. The spec gains `file.escape`, `file.column_count` and per-field `label`; record types are matched by column. Numbers in delimited files are explicit ("-123.45", thousands separators tolerated) unless a picture declares implied decimals, which is a second mode of the shared value rules rather than a second rule set. A row whose column count differs from the declared count, a header row that does not match the labels, or malformed quoting is a **file-level** problem: the parse result is marked rejected and the file is a DQ failure as a whole, while the rows that could be read are still returned for diagnosis. Problems now carry a level (file, record, field) so every consumer can tell "reject the file" from "reject the row" from "flag the value". The registry ships a second example spec, an illustrative CSV price file, so the pattern is exercised the same way the fixed-width one is.

## Consequences

- `astra-spec parse` exercises any spec against a sample file, which is the first thing the Profiler (S5.2.1) and a dry-run (S4.1.1) do.
- The delimited pattern (S2.2.2) adds a second `Pattern` with the same result shape; A/B pairing (S2.2.4) and full-versus-delta merge (S2.2.5) operate on parsed rows and file metadata rather than on lines.
- The S3.2.2 parse renderer must implement exactly these value rules in SQL; the tests in `knowledge/tests/test_values.py` are the specification.
- The signed implied-decimal function of S2.2.3 exists in `patterns/values.py`; that story renders it for the target platform and adds the unit tests it names.
