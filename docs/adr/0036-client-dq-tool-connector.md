# ADR 0036: The connector describes, it does not integrate; parity.yaml stays the single source of what is compared

Date: 2026-09-11
Status: Accepted
Story: S4.2.4 Client DQ tool connector (E4, F4.2, WBS 2.4.7)

## Context

Envestnet validates a custodian's parity with its own tool — iceDQ or Datagaps — in addition to `astra-verify parity run` (S4.2.1). Both tools must reconcile the same two things: the golden capture's legacy output and the lakehouse table it compares against, with the same keys, fields and tolerances. `golden/<custodian>/parity.yaml` already says all of that, reviewed. There is no API to iceDQ or Datagaps in this repository, no sandbox account to integrate against, and no vendor project-file schema to target without guessing — but a QE engineer still has to configure the client's tool by hand, and the client's tool's own result still has to become gate evidence next to `astra-verify`'s own parity report.

## Decision

1. **The connector describes; it does not connect.** `astra-verify connector describe` renders a vendor-neutral connection sheet — where the golden CSV lives (a plain S3 or file prefix; both iceDQ and Datagaps have a native file connector), the Snowflake identity of the lakehouse table (both have a native JDBC connector), and the same keys, fields and tolerances `parity.yaml` already states. Nothing calls the client's tool; the sheet is what a QE engineer types into it, or hands to Envestnet's own admin to type in.

2. **`connector.yaml` carries only what `parity.yaml` does not already say and is not a runtime flag.** `describe` derives the golden location from the `--store` a run is pointed at (never committed, the same discipline every other command already follows) and the lakehouse identity from `--environment`/`--prefix` plus `parity.yaml`'s own `schema`/`table`/columns; restating those in a second file would let the two drift. What is genuinely new per custodian is which tool the custodian uses (`tool: icedq` or `datagaps`) and the Snowflake role the tool connects with (`role`) — a role this story does not provision. Granting `SILVER` read to a client tool's role is a Terraform change for whichever engagement needs it, made once the engagement confirms the role's exact name; this story only names it in the descriptor.

3. **The result is filed, hashed, next to `astra-verify`'s own parity report — by the same default directory `parity run` already writes to.** `astra-verify connector store-result --config golden/<custodian>/connector.yaml --business-date <date> --file <export>` copies the client tool's exported file to `<tool>-result.<ext>` and writes a `<tool>-result.meta.json` sidecar (source filename, byte count, SHA-256, when it was stored) under `work/parity/<custodian>-<date>/` by default — the exact directory `astra-verify parity run` already writes `parity.md`/`parity.json` into, so running both with their defaults leaves the two side by side without either command needing to know about the other's path convention. A file whose extension is not in the connector's `result_formats` is refused, so a wrong export does not silently become evidence.

## Consequences

- No credentials, account locators or bucket names are committed; `describe` needs no Snowflake connection at all (it reads and renders, nothing more), so it can be run to hand Envestnet a connection sheet before any role is even granted.
- The client tool's result is stored as delivered — this story does not parse or validate its content, only files it with a hash so a reviewer knows it has not changed since Envestnet produced it. Interpreting the result is Envestnet's tool's own job.
- One custodian (`golden/pershing/connector.yaml`, iceDQ) is configured, per the story's own scope; a second custodian or a second tool is the same two-line file, not new code.
