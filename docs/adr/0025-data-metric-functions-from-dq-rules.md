# ADR 0025: Every DQ rule is one data metric function or one association

Date: 2026-09-07
Status: Accepted
Story: S3.2.7 Data Metric Functions from DQ rules (E3, F3.2, WBS 2.3.8)

## Context

A config's `dq_rules` were words: a rule had an id, a level, a sentence and a severity, and the bundle printed them as comments. Snowflake measures data quality natively with data metric functions (DMFs): system functions such as `NULL_COUNT` and `DUPLICATE_COUNT`, and custom functions that take a table and return a number, attached to a table and evaluated whenever it changes, with results in the account's data quality monitoring views. The pilot custodian's trailer carries a record count that must equal the number of detail records; operations want that gap, not a boolean.

## Decision

1. **A rule has a kind, and the kind decides what is rendered.** `dq_rules[].kind` is one of `control_total`, `not_null`, `unique`, `accepted_values`, `range` and `condition`, with the parameters the kind needs (`trailer_field` and `aggregate`; `field` or `fields`; `values`; `min` and `max`; `condition`). The compiler resolves every rule against the spec: the trailer field exists and is numeric, the record is a detail record, the field is a column of the table measured, a range applies to an ordered type, a condition names at least one column. Each problem names the rule and what is wrong.

2. **One DMF or one association per rule.** A rule a system function can measure is attached as that function on the rule's column: `not_null` is `SNOWFLAKE.CORE.NULL_COUNT`, `unique` over one field is `SNOWFLAKE.CORE.DUPLICATE_COUNT`. Every other rule is a custom DMF `CONTROL.<SOURCE>_<RULE_ID>` created by the bundle and attached to the table, taking exactly the columns the rule needs. A custom DMF returns 0 when the rule holds, the count of failing rows otherwise.

3. **The control total returns the gap.** A `control_total` rule measures the file metadata table, where each file has its trailer values and its record counts. The compiler pairs `TRAILER_<FIELD>` with `<RECORD>_COUNT`, or with `<RECORD>_<FIELD>_TOTAL` for `aggregate: sum`, in which case the parse renderer adds the per-file sum to the file metadata table. The DMF returns the sum over files of the absolute gap between the trailer total and what the file holds; the rendered test lists each file with its signed gap.

4. **The level says which table is measured.** `file` is the file metadata table and only control totals live there; `record` is the Bronze table of a physical detail record (named with `record` when the spec has several); `pair` and `business` are the Silver table of the logical record, whose columns follow the pairing prefix rule. The column layout helpers moved to `astra_data.layout` so the compiler and the renderers share them.

5. **Severity decides what fails a deploy.** Every rule is measured by its DMF whatever its severity. A rule at severity `error` also gets a rendered test that returns what fails it, so `astra-data test` fails on it; warning and info rules are visible in the DMF results and the docs only.

6. **The spec's own measures stay.** Row counts, nulls in required fields and duplicates of a single-column merge key are attached from the spec alone, before the rules, as before.

## Consequences

- The schema change is additive except that `kind` is now required; the only config, the example, was updated. A rule without a kind fails validation naming the field.
- Attaching a DMF needs `EXECUTE DATA METRIC FUNCTION` on the account; the foundation grants it to the deploying roles alongside task execution.
- `ALTER DYNAMIC TABLE ... ADD DATA METRIC FUNCTION` and `ALTER ICEBERG TABLE ... ADD DATA METRIC FUNCTION` are the documented forms; both are confirmed live in S7.1.1 with the rest of the bundle.
- DMF expectations (a threshold on the metric's value) are not rendered; the severity and the rendered test carry that meaning until the platform's expectation syntax is adopted.
- A `condition` rule is SQL the compiler cannot check beyond column names; it is the escape hatch for business rules, and a wrong condition fails at deploy, not silently.
