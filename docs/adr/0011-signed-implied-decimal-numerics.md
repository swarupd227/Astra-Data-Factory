# ADR 0011: Signed implied-decimal numerics as one function, in Python and in Snowflake

Date: 2026-09-06
Status: Accepted
Story: S2.2.3 Signed implied-decimal numerics (E2, F2.2, WBS 2.2.4)

## Context

Custodian files write numbers as unsigned digits with implied decimals (picture `9(13)V9(5)`) and carry the sign in a separate field. Which characters mean positive, negative or unknown differs by custodian. The story asks for a single function so that every custodian's numeric convention is configuration, with `+` giving the positive decimal, a blank sign giving NULL rather than zero, and unit tests for `+`, `-`, blank and invalid.

## Decision

1. **One function in the reference implementation**: `astra_knowledge.patterns.numerics.signed_implied_decimal(digits, sign, scale, convention, sign_style)`. It returns the number, or NULL with a reason: blank digits are NULL without a problem; digits that are not all digits, an unknown sign on a non-zero magnitude, and a sign outside the convention are NULL with a problem. An unknown sign on a zero magnitude is zero, because there is nothing to sign. The value rules used by every pattern delegate to it.

2. **The sign convention is configuration in the Source Spec.** The codes of the sign field carry `sign: positive | negative | unknown`. The registry builds a `SignConvention` from them and requires both a positive and a negative code when any code declares a sign; without declarations the default applies (`+`, `-`, blank). A leading sign character inside the digits is `sign_style: leading`, which excludes a separate sign field.

3. **The same function ships for Snowflake** in `infra/terraform/foundation/numerics.tf` as three SQL UDFs in CONTROL: `IMPLIED_DECIMAL` places the decimal point by string position, exactly, with no floating-point division; `SIGNED_IMPLIED_DECIMAL` applies the convention passed as arrays of positive, negative and unknown codes and returns `NUMBER(38, 12)`; `SIGNED_IMPLIED_DECIMAL_PROBLEM` returns the reason a value is NULL. The parse renderer (S3.2.2) calls these rather than emitting arithmetic, casts to the column's own scale, and records the problem as a record-level DQ finding.

4. **The Python tests are the specification of both.** `knowledge/tests/test_numerics.py` covers the story's four cases and the convention; the Terraform test checks the SQL encodes the same rules. Equivalence on a live account is confirmed by the dry-run (S4.1.1), which compares the reference parse with the rendered parse row for row.

## Consequences

- Changing a custodian's sign convention is a spec edit reviewed in Git; no code changes.
- The Pershing GCUS example declares its convention on `quantity_sign`.
- Overpunched (zoned) signs are not covered; they need a third `sign_style` when a layout requires it.
- The SQL function resource is a provider preview feature, added to the enabled list.
