# ADR 0013: The canonical data model as a versioned domain pack artifact

Date: 2026-09-06
Status: Accepted
Story: S2.3.1 Canonical model tables and keys (E2, F2.3, WBS 2.2.8)

## Context

The product spec defines a Domain Pack as the canonical model, vocabulary, rule patterns, DQ patterns, rejection taxonomy and reference-data patterns for one industry area, stored as a versioned package, and the Canonical Model (CDM) as DDL plus semantic definitions plus a glossary with versions and migration paths. The custodial pack is the first. Every client instance must start from the same model, breaking changes must be deliberate, and the definitions people read in the glossary must be the definitions the database carries.

## Decision

1. **A domain pack is a directory in Git**, `domains/<name>/`, alongside `specs/` and `configs/`. It holds `glossary.yaml` and `cdm/<major>.<minor>.yaml`, one file per model version, plus `cdm/migrations/<version>.md` notes and `cdm/rendered/<version>/` output. The knowledge plane (`astra_knowledge.cdm`) loads, validates, diffs and renders it; `astra-spec cdm` is the command line.

2. **Definitions live in the glossary only.** An entity names its glossary term; the term's definition is the entity's definition and becomes the table comment in rendered DDL. The validator rejects an entity whose term is missing, is a concept term, or has a different name, and rejects an entity term that no model version uses (a removed entity keeps its term, so older versions stay loadable). This is how "definitions match the glossary terms" holds: there is one definition, not two kept in step.

3. **Keys are the reconciliation identity.** Each entity declares the columns that identify one row, all required. References declare which columns point at another entity's key. Snowflake does not enforce uniqueness or referential integrity on Iceberg tables, so the model renders one uniqueness test per key and one orphan test per reference, in the bundle test form (a query that returns failing rows). S7.1.1 runs them.

4. **The versioning rule is enforced, not documented.** Versions are MAJOR.MINOR and each follows its predecessor. `diff` classifies every change: removing or renaming an entity or column, changing a key, making a column required, narrowing a type, or changing the lineage or the target schema is breaking; adding an entity or optional column, widening a type, or changing descriptions, codes, PII categories or references is additive. A breaking change requires the next major version and a migration note file; an additive change requires the next minor version; a version with no changes, a skipped number, or a major bump with nothing breaking fails validation.

5. **DDL is rendered for Snowflake managed Iceberg tables** with `{{ DATABASE }}` as the placeholder the deploy pipeline fills, so one rendering serves every environment. Tables inherit the database's external volume and catalog; `BASE_LOCATION` follows the foundation's `<schema>/<table>/` convention. Identifiers are always quoted, because `ACCOUNT` is a reserved word in Snowflake and other entity names may become one. Logical types map to `STRING`, `NUMBER(18,0)`, `NUMBER(p,s)`, `DATE`, `TIMESTAMP_NTZ(6)` and `BOOLEAN`, the types the foundation already uses.

6. **Rendered output is committed and checked.** `astra-spec cdm render` writes `ddl.sql` and `tests/*.sql` for every version; CI runs `astra-spec cdm render --check` and fails when they are stale. A model change is therefore reviewed as a DDL diff, and S7.1.1 can deploy the files as they are.

7. **Lineage columns are part of the model.** `SOURCE_SYSTEM`, `SOURCE_FILE`, `SOURCE_LINE`, `CONFIG_VERSION`, `LOADED_AT` and `UPDATED_AT` are appended to every table, and an entity cannot redefine them. PII columns carry the category of the foundation's PII tag (ADR 0008) so the tag can be applied when the tables are created.

## Consequences

- The Modeler agent (S5.4.x) maps Source Specs to these entities and raises CDM change requests as new model versions; a breaking request is visible as a major version with a migration note before a steward sees it.
- S2.3.2 adds the rejection taxonomy that `EXCEPTION.REJECTION_CODE` refers to; S2.3.3 adds the reference-data patterns behind `ACCOUNT_ID` and `SECURITY_ID` resolution.
- S7.1.1 packages `cdm/rendered/<version>/` as a release bundle and runs the key tests; S8.2 publishes the glossary terms to the governance tool.
- A second domain pack follows the same layout without changes to the tooling.
