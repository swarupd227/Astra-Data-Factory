# ADR 0009: The spec registry, and a core package beneath every plane

Date: 2026-09-06
Status: Accepted
Story: S2.1.1 Store a versioned Source Spec (E2, F2.1, WBS 2.2.1)

## Context

A custodian layout must be stored as a machine-readable Source Spec with an effective date and citations into the layout document, validated against a schema, so that every custodian on the same layout reuses it. Two versions must coexist; a query by custodian and business date must return the version in force.

This is the first knowledge-plane story. The config validator in the generation plane will need to read specs, which fixes the dependency direction: generation depends on knowledge, and knowledge must not depend on generation. Both need the same line-aware YAML loader and problem reporting.

## Decision

1. **A `core` package beneath every plane.** `astra-core` holds the line-aware YAML loader, the problem type and its formatting, schema error wording, and the Snowflake connection. The layering is `core <- knowledge <- generation <- verification`. The generation package re-exports the moved modules so existing imports keep working.

2. **The registry is a directory in Git**, `specs/<spec id>/<version>.yaml`. A spec id names a layout, not a custodian; the spec lists the custodians that deliver it. The file name is the version and carries no meaning beyond identity; `effective_from` is the business date the version comes into force. Two versions are two files.

3. **The Source Spec schema, version 0**, describes the document the citations point into, the file shape (fixed width with a record length, or delimited with a delimiter), and the records (header, detail, trailer, with match rules). Every field carries a position or column, a COBOL-style picture and/or a type, and a citation with a page or a line.

4. **The picture is authoritative.** A picture clause is parsed into width, digits, scale and sign. In a fixed-width file the position length must equal the picture width, the declared type must fit the picture's kind, dates and times must carry a format, and a separate sign field must exist in the record. Fields may not overlap or exceed the record length.

5. **Resolution is by custodian, file type and business date**: the version with the latest `effective_from` on or before the date, among versions that list the custodian and describe the file type. Across the registry, two versions may not come into force for the same custodian and file type on the same date.

6. **Configs are checked against the registry in CI.** `astra-data validate --specs specs` requires each config's `spec` reference to name an existing version that lists the config's custodian, describes the config's file type, and is in force by the config's effective date. The example config now references `pershing_gcus 2017-07-25`.

7. **The registry ships with a real example**: the Pershing GCUS position layout, two versions, with the 2026 version adding a lot identifier. It is the fixture for the tests and the model for every spec that follows.

## Addendum: search (S2.1.2, 2026-09-06)

`Registry.search` finds specs by custodian, family, file type and business date and ranks them: rank 1 when the spec lists the custodian, rank 2 when it belongs to the family (a reuse candidate for a custodian the registry has not seen), rank 3 when only the file type matched; newest first within a rank. With a date, only versions in force on that date count, one per spec unless all versions are asked for. A spec with no family is flagged on every hit, listed by `astra-spec unclassified`, and reported as a warning by `astra-spec validate`, so classification work is visible without failing the build. This is the query the Pattern Matcher (S5.3.1) runs.

## Consequences

- Every plane's validator fails the same way: file, line, plain sentence, GitHub annotation.
- The Spec Reader agent (S5.1.1) produces this file format; its output validates before it is stored.
- The parse renderer (S3.2.2) reads offsets, pictures and formats from the registry rather than from the config.
- Search by family and file type (S2.1.2) adds to the registry class; the `family` field is already in the schema.
- The registry is file-based today. A Snowflake or Postgres mirror for the workbench arrives with the spec registry viewer (S6.3.4).
