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

## Addendum: A/B record pairing (S2.2.4, 2026-09-06)

Pairing is a step after parsing, not a parser: the spec's `pairing` block names the detail record types that form one logical record and the key fields they share, and `patterns.pairing.pair` joins the parsed rows into one row per key with the keys once and every other field of each record, prefixing a field name that both records use. Records pair regardless of order or distance within the file; each physical record takes part once. What cannot be joined is reported with a rejection code and the row reference: `PAIR_INCOMPLETE` for a record whose partner never appeared, `PAIR_DUPLICATE` for a second record of the same type and keys, `PAIR_KEY_BLANK` for a record whose key is blank. Problems now carry a `code` so the exception store (S3.2.5) and the rejection taxonomy (S2.3.2) can refer to them. `parse()` applies pairings by default so the reference output is the logical file; the registry validates that paired records exist, are detail records, belong to one pairing, and carry every key. An illustrative split-position spec joins the registry as the fixture.

## Addendum: split records and the cancel/correct lifecycle (S2.2.6, 2026-09-06)

Two more steps over parsed rows, both driven by spec blocks. **Split** (`split` block, `patterns.split.split`): one custodian record becomes two or more canonical records when a field carries one of the listed codes. Each part sets values, clears values or negates numbers and keeps everything else, and every part carries its provenance (`origin`, `split`, `part` on the row plus the line number), so a canonical transaction is always traceable to the custodian line it came from. The DRIP case is the fixture: one line yields a dividend and a purchase whose amount is the dividend negated. `parse()` applies pairing then splitting, so the reference output is the canonical file. **Lifecycle** (`lifecycle` block, `patterns.lifecycle.apply_lifecycle`): the action field's codes map to new, cancel or correct; identity fields say what a record is and reference fields say what a cancel or correction points at. A cancel closes the original (`cancelled`, `cancelled_by`) and is stored as a non-active record that points back (`cancels`); a correction closes the original (`superseded`, `superseded_by`) and becomes the active record that points back (`corrects`). Split parts share their line's identity plus their part name, so a cancel of a split line closes every part. Rejection codes: `LIFECYCLE_ORIGINAL_MISSING`, `LIFECYCLE_ALREADY_CLOSED`, `LIFECYCLE_DUPLICATE`, `LIFECYCLE_IDENTITY_BLANK`. The registry validates both blocks against the logical records (paired records and split parts included). The rendered SQL of S3.2.3 must reproduce the same links; the state kept here is the oracle.

## Consequences

- `astra-spec parse` exercises any spec against a sample file, which is the first thing the Profiler (S5.2.1) and a dry-run (S4.1.1) do.
- The delimited pattern (S2.2.2) adds a second `Pattern` with the same result shape; A/B pairing (S2.2.4) and full-versus-delta merge (S2.2.5) operate on parsed rows and file metadata rather than on lines.
- The S3.2.2 parse renderer must implement exactly these value rules in SQL; the tests in `knowledge/tests/test_values.py` are the specification.
- The signed implied-decimal function of S2.2.3 exists in `patterns/values.py`; that story renders it for the target platform and adds the unit tests it names.
