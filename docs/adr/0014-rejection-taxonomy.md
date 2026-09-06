# ADR 0014: The rejection taxonomy as data

Date: 2026-09-06
Status: Accepted
Story: S2.3.2 Rejection taxonomy as data (E2, F2.3, WBS 2.2.9)

## Context

The legacy Loader rejects custodian data with more than fifty codes, and the product spec sets parity with them as a success measure: every rejection code traced, every code reproducible as an exception row (S7.1.6). The factory raises its own problems from the pattern library, resolution and reconciliation, and the Exception entity of the canonical model (ADR 0013) carries a rejection code. Codes scattered through code and SQL cannot be checked against the Loader, owned or explained; the taxonomy has to be data.

## Decision

1. **The taxonomy is a file in the domain pack**, `domains/<name>/rejections.yaml`, one entry per code with its name, description, level (file, record, field), severity (critical, error, warning), category, owner, resolution, whether Exception Triage may apply the resolution on its own, and the Loader codes it reproduces. A level constrains the severity: a rejected file is critical, a rejected record an error or a warning, a value either held back or flagged. The pack loader validates it with the glossary and the model, and the entity a code names must be an entity of the model.

2. **The pattern library raises taxonomy codes.** Every problem the reference implementations report carries a code from the taxonomy: value conversion, required fields, record types, headers and trailers, column counts and quoting, pairing, merge, split and lifecycle. A test parses malformed files through every pattern and asserts that no problem is without a code and that every code raised is in the taxonomy. Rendered SQL (E3) routes exceptions with the same codes, and the taxonomy is the list it may use.

3. **CONTROL.REJECTION_CODES is synced from the file**, not maintained by hand. The foundation creates the table; `astra-data rejections sync` merges the taxonomy into it on every deploy and retires codes that left the taxonomy rather than deleting them, so exception rows keep their referent. The Exception entity's `REJECTION_CODE` column declares a lookup on that table, and the canonical model renders a test that returns exception rows whose code is not in it. That is how "every exception row references a code in this table" is checked, in every environment, by the same test runner as the key tests.

4. **Parity with the Loader is a validation, not a report.** The Loader Rejections reference is exported from the client's document to `domains/<name>/loader-rejections.csv` (columns `code`, `description`). When that file is present, pack validation fails for every Loader code no taxonomy code reproduces and for every Loader code the taxonomy names that the reference does not. `astra-spec rejections parity` prints the same report on demand. The reference is client material and is not in this repository yet; the mechanism is, and CI enforces it the moment the file lands.

5. **Codes are never renamed or deleted.** A code that no longer applies is marked retired and stays in the file and the table. New codes are additive; the taxonomy has no version of its own, because it is part of the pack and the model's Exception entity is what changes shape.

## Consequences

- The DQ Generator (S3.3.x) and the resolution renderer (S3.2.4) take their rejection codes from the taxonomy; a code that is not in it fails validation before a pipeline is rendered.
- S7.1.6 reproduces every active code as an exception row on seeded data; the taxonomy is its checklist.
- The Exception Triage agent reads `owner`, `resolution` and `auto_resolve` from CONTROL.REJECTION_CODES to route and, where allowed, apply resolutions.
- Adding the Loader reference file is the remaining step for the parity criterion; it needs the client's document, not code.
