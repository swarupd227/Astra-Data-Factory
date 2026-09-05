Astra Data Factory

Epics, Features and User Stories — companion to Product Specification v0.2

Version 0.2 · 5 September 2026 · For the development team · Internal

How to read this. Ten epics, one per plane or major concern. Each epic has features; each feature has user stories with acceptance criteria. Every story carries a priority (P1 = pilot must-have by week 10, P2 = needed for family releases by week 14, P3 = later) and the WBS task IDs it maps to in Estimation v5, so the backlog and the estimate stay tied together.

Definition of done (all stories). Code in Git with review; tests generated or written and passing in CI; deployed to the QA environment from IaC; documentation generated; if user-facing, checked by the persona named in the story; no PII in test data.

Story format. Acceptance criteria are written so a tester can run them without asking. Where a threshold is written as [T], the QE lead sets it from the pilot baseline.

Contents


# Epic map

| Epic | Plane / concern | Outcome | Priority |
|---|---|---|---|
| E1 Platform foundation | Control plane infrastructure | Snowflake, Iceberg, Open Catalog, environments, CI/CD, sandboxes, alerts exist from code | P1 |
| E2 Knowledge plane | Knowledge | Spec registry, pattern library, rule catalog, custodial domain pack | P1 |
| E3 Generation plane (Astra Data) | Generation | Approved config becomes native Snowflake code, DDL, DMFs, docs, tests | P1 |
| E4 Verification plane | Verification | Dry-run, golden replay, parity, DQ runner, NFR, agent evaluation | P1 |
| E5 Agents plane | Agents | Twelve bounded agents with guardrails and metrics | P1/P2 |
| E6 Control plane application | Control | UX research and design system; 20 workbench screens: board, studio, reviews, viewers, dashboards, admin | P1/P2 |
| E7 Envestnet instance | Client instance | Silver model, resolution, exception store, Gold read model, pg_lake, window | P1 |
| E8 Governance integration | Governance | Atlan, lineage, glossary, PII, RACI | P1/P2 |
| E9 Historical migration | Migration | SnowConvert AI / AIM driven SQL Server → Iceberg archive | P2 |
| E10 Cutover and operations | Operations | Consumer cutover, decommission, runbooks, DR, support handover | P3 |

# E1 — Platform foundation

Everything the factory runs on, created from code. Owner: DevOps/SRE.

## F1.1 Snowflake and Iceberg foundation

S1.1.1  Snowflake objects from Terraform   [P1 · WBS 2.1.1]

As a DevOps engineer, I want databases, schemas, roles and tier-sized warehouses defined in Terraform, so that every environment is identical and reproducible.

- terraform apply on an empty account creates all objects with no manual step

- Warehouse sizes per tier (simple/medium/complex) are variables

- Re-running apply on an unchanged config produces no diff

S1.1.2  Iceberg external volume and Open Catalog   [P1 · WBS 2.1.2]

As a data lead, I want Bronze, Silver and Gold tables created as Snowflake-managed Iceberg on an S3 external volume and exposed via Open Catalog, so that pg_lake and other engines can read the same files.

- A table created in Snowflake is listed by the Iceberg REST catalog within one minute

- An external Spark or DuckDB client can read the table via the catalog

- Access is denied for roles without the catalog grant

S1.1.3  Snowpipe on S3 events   [P1 · WBS 2.1.3]

As a data engineer, I want files landing in the S3 landing zone to appear as raw-line rows in Bronze automatically, so that parsing starts on arrival instead of at 6 AM.

- A file dropped in the landing prefix appears as rows in the raw-lines table within 60 seconds

- Each row carries file name, row number and ingest timestamp

- A duplicate file (same name, same hash) is not loaded twice and is logged

## F1.2 Environments, CI/CD and sandboxes

S1.2.1  Four environments from IaC   [P1 · WBS 2.1.4]

As a DevOps engineer, I want dev, QA, UAT and prod created from the same IaC with environment-specific variables only, so that environment parity is a fact, not a hope.

- Diff of object definitions across environments is empty except for variables

- A new environment can be created in under two hours

S1.2.2  CI/CD for config and generated code   [P1 · WBS 2.1.5]

As a data engineer, I want a merged pull request to validate config, run generated tests and deploy to the target environment, so that changes move through environments without manual steps.

- A PR with an invalid config fails with the validation message in the PR

- A passing PR deploys to dev automatically and to QA on approval

- Deploy time from merge to QA is under 30 minutes

S1.2.3  Ephemeral sandboxes   [P1 · WBS 2.1.6]

As an agent runtime, I want an isolated schema and warehouse created for a task and destroyed after, so that dry-runs never touch shared environments.

- Sandbox is created in under two minutes with the requested config deployed

- Sandbox is destroyed automatically after the task or after a time limit

- Sandbox cost is tagged to the task

S1.2.4  Alerts to Slack, Jira and email   [P1 · WBS 2.1.7]

As an operations user, I want pipeline failures and late custodians to raise alerts on the channels already used, so that nobody has to watch a dashboard.

- A failed Task raises an alert on all three channels within five minutes

- A custodian past its cutoff raises a 'late' alert with the missing files listed

- Alert severity is configurable per custodian

S1.2.5  Secrets, access history and masking baseline   [P1 · WBS 2.1.8]

As a security reviewer, I want secrets held in the client's secret manager, access history retained, and masking policies applied to PII columns, so that the platform meets the SOX / SOC 2 posture from day one.

- No secret value appears in Git, logs or config

- Access history queries return who read which PII column and when

- A non-privileged role sees masked values on tagged columns

# E2 — Knowledge plane

What the factory knows. Owner: platform engineering, with the architect for the domain pack.

## F2.1 Spec registry

S2.1.1  Store a versioned Source Spec   [P1 · WBS 2.2.1]

As a data engineer, I want to store a custodian layout as a machine-readable Source Spec with an effective date and citations to the layout document, so that the same spec is reused for every custodian on that layout.

- A Source Spec is YAML validated against the registry schema

- Each field carries positions, picture/type, and a page or line citation

- Two versions of the same spec can coexist with different effective_from dates

- Querying by custodian and business date returns the version in force

S2.1.2  Find specs by family and file type   [P1 · WBS 2.2.1]

As a pattern matcher agent, I want to search the registry by custodian, family, file type and effective date, so that the right spec is found without a human.

- Search returns matches ranked by exact custodian, then family

- A spec with no family is flagged for classification

## F2.2 Pattern library

S2.2.1  Fixed-width multi-record pattern   [P1 · WBS 2.2.2]

As a platform engineer, I want a pattern that parses fixed-width files with header, trailer and multiple detail record types, so that Pershing-style files need no custom code.

- Given the GCUS spec, the pattern produces one parsed row per detail record with correct types

- Header and trailer are parsed into file metadata

- Record type is read from the configured position and end marker

S2.2.2  Delimited-file pattern   [P1 · WBS 2.2.3]

As a platform engineer, I want a pattern for delimited files with quoting, escapes and optional headers, so that the second most common layout is covered.

- CSV and pipe-delimited samples with quoted fields parse correctly

- Column count mismatch is a file-level DQ failure

S2.2.3  Signed implied-decimal numerics   [P1 · WBS 2.2.4]

As a platform engineer, I want a single function that converts unsigned digits plus a separate sign field and implied decimals into a number, so that every custodian's numeric convention is config, not code.

- 9(13)v9(05) with sign '+' returns the positive decimal

- Blank sign returns NULL, not zero

- Unit tests cover +, −, blank and invalid

S2.2.4  A/B record pairing   [P1 · WBS 2.2.5]

As a platform engineer, I want a pattern that pairs detail records into one logical record on configured keys, so that positions split across two records are complete.

- Records A and B with the same account and CUSIP become one row

- A missing partner raises PAIR_INCOMPLETE with the row reference

S2.2.5  Full vs delta merge   [P1 · WBS 2.2.6]

As a platform engineer, I want the Silver merge mode decided by a header flag, so that refresh and update files behave correctly.

- REFRESHED replaces all positions for the remote ID as of the business date

- UPDATED merges on keys and carries untouched rows forward

- A refresh not seen for N days raises an alert

S2.2.6  Split-record and cancel/correct patterns   [P2 · WBS 2.2.7]

As a platform engineer, I want patterns that split one custodian record into several canonical records and that link cancels and corrections to originals, so that DRIP and correction lifecycles are config.

- The Envestnet DRIP example yields a dividend and a purchase transaction

- A cancel record marks the original cancelled and both are traceable

## F2.3 Custodial domain pack

S2.3.1  Canonical model tables and keys   [P1 · WBS 2.2.8]

As an architect, I want the custodial CDM (Account, Security, Position, Lot, Transaction, Price, Cash Balance, Firm, Exception) defined once with keys and a versioning rule, so that every client instance starts from the same model.

- DDL generated for all entities on Iceberg

- Breaking change requires a new major version and a migration note

- Definitions match the glossary terms

S2.3.2  Rejection taxonomy as data   [P1 · WBS 2.2.9]

As a data engineer, I want the 50+ rejection codes stored as data with descriptions, severity and owner, so that parity with the Loader is checkable.

- All codes from the Loader Rejections reference are loaded

- Every exception row references a code in this table

S2.3.3  Reference-data replication patterns   [P1 · WBS 2.2.10]

As a data engineer, I want patterns to replicate SOS security master and CAS account cross-reference into Snowflake on a schedule, so that resolution is set-based and fits the 20-minute window.

- Nightly replication job runs and records row counts

- Delta since last run is visible

- Resolution joins use the replicated tables, not API calls

## F2.4 Rule catalog

S2.4.1  Rule catalog store   [P1 · WBS 2.2.11]

As a steward, I want every business rule stored with citation, classification, owner, status and lineage to configs that use it, so that rules are governed, not buried in code.

- A rule has: id, text, source citation (file:line or spec page), class (ingestion / business / normalisation), owner, status (recovered / confirmed / rejected / legacy defect)

- Changing a rule's status records who and when

- A config that references a rejected rule fails validation

# E3 — Generation plane (Astra Data)

Approved decisions become native Snowflake artifacts. Owner: platform engineering.

## F3.1 Config compiler

S3.1.1  Validate a config against the schema   [P1 · WBS 2.3.1]

As a data engineer, I want a config rejected with a precise message when it does not match the schema or references unknown patterns or rules, so that bad configs never reach a renderer.

- Missing required field → error names the field

- Unknown pattern or rule id → error names it

- Valid config → compiled intermediate model returned

S3.1.2  Render a release bundle   [P1 · WBS 2.3.1, 2.3.14]

As a data engineer, I want one command that renders every artifact for a config into a release folder, so that a release is one reviewable unit.

- Output contains DDL, pipeline SQL, Tasks, DMFs, tests, docs, Atlan payloads and PROVENANCE.json

- Re-rendering an unchanged config produces byte-identical output

## F3.2 Snowflake renderers

S3.2.1  Bronze DDL and Snowpipe   [P1 · WBS 2.3.2]

As a renderer, I want raw-lines table DDL and a Snowpipe definition generated from the config, so that landing to Bronze needs no hand-written SQL.

- Rendered SQL deploys without edit

- Pipe points at the custodian's landing prefix

S3.2.2  Parse Dynamic Table   [P1 · WBS 2.3.3]

As a renderer, I want a Dynamic Table that turns raw lines into typed columns using offsets and types from the spec, so that parsing is config-driven.

- For GCUS, every configured field is present with the right type

- Rows failing record-level DQ are excluded and counted

- TARGET_LAG matches the config

S3.2.3  Silver MERGE   [P1 · WBS 2.3.4]

As a renderer, I want a MERGE into Silver that honours merge mode, pairing and deduplication, so that Loader behaviour is reproduced.

- Full mode replaces; delta mode merges

- Paired rows only; unpaired go to exceptions

- Output matches the golden dataset on the pilot custodian

S3.2.4  Resolution steps   [P1 · WBS 2.3.5]

As a renderer, I want account, security, transaction-code and price resolution rendered as set-based joins with the configured rejection codes, so that the four Loader resolutions are reproduced.

- Unresolved account → ACCOUNT_NOT_FOUND row with payload

- Security resolved by CUSIP, ISIN or OCC set as configured

- Missing tx-code mapping → the configured rejection code

S3.2.5  Exception routing   [P1 · WBS 2.3.6]

As a renderer, I want every rejected row written to the exception store with code, payload and state NEW, so that no data is silently dropped.

- Count of rejected rows equals count of exception rows per run

- Payload contains the full source record

S3.2.6  Per-custodian Tasks DAG with completeness   [P1 · WBS 2.3.7]

As a renderer, I want a Tasks DAG that starts resolution only when the custodian's expected file set is complete, so that the post-arrival work fits the window.

- DAG starts within one minute of the last expected file

- A late file after cutoff triggers the late alert and a rerun on arrival

- DAG for one custodian cannot block another

S3.2.7  Data Metric Functions from DQ rules   [P1 · WBS 2.3.8]

As a renderer, I want DMFs generated from dq_rules and attached to the right tables, so that DQ is native and visible.

- Each dq_rule becomes one DMF or one DMF association

- Trailer control-total check returns the gap value

S3.2.8  Gold read model and watermark   [P1 · WBS 2.3.9]

As a renderer, I want Gold tables shaped for the consumer and a watermark written last, so that UMP never reads a half day.

- Watermark row per custodian per business date written after all Gold refreshes

- A consumer query filtered by watermark returns only complete days

S3.2.9  Terraform, docs, Atlan payloads, tests   [P1 · WBS 2.3.10–2.3.13]

As a renderer, I want infrastructure, documentation, catalog registrations and tests generated from the same config, so that the bundle is complete.

- Terraform plan is clean

- Docs list every field, rule and DQ check with citations

- Atlan payload registers dataset, owner, PII tags

- Generated tests run green in CI

## F3.3 Migration tooling integration

S3.3.1  SnowConvert AI CLI integration   [P2 · WBS 2.3.15]

As a migration engineer, I want the factory to drive SnowConvert AI for extract, convert, migrate and validate against a SQL Server source, so that the historical migration uses Snowflake's tooling.

- Commands run end-to-end on a test schema from the factory

- Results and logs are stored with the release

- Converted DDL lands in the archive-store schema

S3.3.2  Snowpark Connect assessment hook   [P1 · WBS 1.4.3]

As an architect, I want to run a Spark transformer through Snowpark Connect and record effort and result, so that the third candidate in the Normalizer decision is scored on evidence.

- Two Normalizer transformers run on Snowflake with recorded changes

- Result is attached to the assessment memo

# E4 — Verification plane

Proof, not assertion. Owner: QE lead with AI-native test engineers.

## F4.1 Dry-run and golden replay

S4.1.1  Dry-run a config in a sandbox   [P1 · WBS 2.4.1]

As a BSA, I want to run sample files through a drafted config and see a report before asking for promotion, so that mistakes are found before they reach QA.

- Report shows rows parsed, rejected by code, DQ results and control-total gaps

- Runs in under 10 minutes for a sample of [T] rows

- Sandbox is destroyed afterwards

S4.1.2  Capture golden datasets from the legacy path   [P1 · WBS 2.4.2, 2.4.3]

As a data engineer, I want to replay historical files through Splitter/Loader in non-prod and store outputs and rejections as hashed, versioned datasets, so that the true legacy behaviour is the oracle.

- 30–60 business days captured per pilot custodian

- Each dataset has a hash and the source file list

- Datasets are read-only after capture

S4.1.3  Replay N days after a config change   [P2 · WBS 2.4.3]

As a steward, I want any config change replayed against N days of history with a difference report, so that self-service changes are safe.

- Diff report groups differences by rule and field

- A change with zero differences can be promoted without SME review

## F4.2 Parity and DQ

S4.2.1  Parity engine   [P1 · WBS 2.4.4]

As a QE engineer, I want row and field comparison between legacy output and lakehouse output with configurable keys and tolerances, so that the ≥99.5% match rate is measured, not estimated.

- Match rate per custodian per business date

- Differences classified: missing, extra, value mismatch (by field)

- Tolerances per field type (e.g. price to 4 dp)

S4.2.2  Parity report   [P1 · WBS 2.4.5]

As a steward, I want a readable report per dual-run cycle, so that gate evidence is generated.

- Report includes match rate, difference groups, trend over cycles

- Exported into the release evidence pack

S4.2.3  DQ runner and scores   [P1 · WBS 2.4.6]

As a governance engineer, I want DMF results collected into entity-level scores against Section 5.1 targets, so that quality is visible per entity.

- Score per entity per day

- Breach of threshold raises an alert

S4.2.4  Client DQ tool connector   [P2 · WBS 2.4.7]

As a QE engineer, I want the client's DQ / parity tool (iceDQ or Datagaps) to read golden and lakehouse tables, so that Envestnet can validate with its own tool.

- Connector configured for one custodian

- Tool's result is stored alongside the parity report

## F4.3 Non-functional and agent evaluation

S4.3.1  3× volume test   [P1 · WBS 2.4.8]

As a QE engineer, I want to run a custodian's daily set at three times normal volume, so that the 20-minute window is proven for peak days.

- End-to-end ≤ 20 minutes after the last file at 3×

- Warehouse size used is recorded

S4.3.2  Chaos scenarios   [P1 · WBS 2.4.9]

As a QE engineer, I want late, malformed, duplicate and truncated files injected, so that recoverability is proven.

- Each scenario ends in the documented state with alerts raised

- Retry of a fixed file needs no manual data surgery

S4.3.3  DR drill   [P3 · WBS 2.4.10]

As a DevOps engineer, I want a scripted drill that restores the standardized zone, so that RTO/RPO are demonstrated.

- Restore completes within the proposed RTO

- Data loss within RPO

S4.3.4  Agent evaluation harness   [P1 · WBS 2.4.11, 2.4.12]

As an agent engineer, I want every agent scored against a gold set on each change, with weekly published results, so that agent trust is measured.

- Gold set per agent versioned in Git

- Precision/recall by tier reported weekly

- A regression below threshold blocks release of the agent

# E5 — Agents plane

Each agent is a bounded worker. Stories follow the same shape: build, evaluate, guardrail. Owner: agent engineering.

## F5.1 Spec Reader

S5.1.1  Spec Reader: build and evaluate   [P1 · WBS 2.5.1, 2.5.2]

As an agent engineer, I want a layout document (PDF/Word) turned into a Source Spec with page citations, so that the factory does this step without a person typing.

- Field-level accuracy ≥ [T] on the Pershing/Schwab/Fidelity gold set

- Unparsed sections are listed, never guessed

- Output validates against the registry schema

## F5.2 Profiler

S5.2.1  Profiler: build and evaluate   [P1 · WBS 2.5.3, 2.5.4]

As an agent engineer, I want sample files profiled for types, nulls, value sets and record types, and drift against the spec noted, so that the factory does this step without a person typing.

- Profile produced for a 750-char fixed-width sample

- Fields whose observed type disagrees with the spec are flagged

## F5.3 Pattern Matcher

S5.3.1  Pattern Matcher: build and evaluate   [P1 · WBS 2.5.5, 2.5.6]

As an agent engineer, I want a custodian assigned to a family, tier and pattern list, so that the factory does this step without a person typing.

- Assignment accuracy ≥ [T] on known custodians

- A new pattern proposal goes to the architect queue

## F5.4 Rule Recovery (Astra RE Harness)

S5.4.1  Rule Recovery (Astra RE Harness): build and evaluate   [P1 · WBS 2.5.7, 2.5.8]

As a steward, I want Splitter/Loader Java turned into catalog entries with file:line citations, classified, with candidate tests, so that the factory does this step without a person typing.

- Every Loader rejection code is traced to at least one entry

- No entry is marked confirmed by the agent

- T-SQL found is routed to SnowConvert AI, not parsed

## F5.5 Modeler

S5.5.1  Modeler: build and evaluate   [P1 · WBS 2.5.9, 2.5.10]

As a data engineer, I want a config draft with field mappings to the CDM, resolution parameters and DQ suggestions, so that the factory does this step without a person typing.

- Mapping precision/recall ≥ [T] by tier

- Rules needing SME confirmation are tagged CONFIRM_WITH_LOADER

- Breaking CDM changes are raised as requests, not applied

## F5.6 DQ Generator

S5.6.1  DQ Generator: build and evaluate   [P1 · WBS 2.5.11, 2.5.12]

As a data engineer, I want file, record and pair-level DQ rules proposed from the spec and domain pack, so that the factory does this step without a person typing.

- Control-total, sign-field, date, key and pairing rules generated for GCUS

- Thresholds default to the client's targets

## F5.7 Test Generator

S5.7.1  Test Generator: build and evaluate   [P2 · WBS 2.5.13, 2.5.14]

As a QE engineer, I want tests and synthetic edge files generated from the config, so that the factory does this step without a person typing.

- Branch coverage of rules ≥ [T]

- Synthetic files contain no real records

## F5.8 Exception Triage

S5.8.1  Exception Triage: build and evaluate   [P1 · WBS 2.5.15–2.5.17]

As an operations user, I want suggested resolutions per exception with confidence, grouped by root cause, and a whitelist for self-healing classes, so that the factory does this step without a person typing.

- Suggestion for NEW_SECURITY, MISSING_TX_CODE, ACCOUNT_NOT_FOUND

- Acceptance rate tracked per code

- Auto-apply only for whitelisted classes at level L3

## F5.9 Drift Watcher

S5.9.1  Drift Watcher: build and evaluate   [P2 · WBS 2.5.18, 2.5.19]

As a data engineer, I want layout changes detected against the spec and a config delta proposed, so that the factory does this step without a person typing.

- Injected record-length and code-set changes detected before Silver

- Never modifies production config

## F5.10 Parity / Break Explainer

S5.10.1  Parity / Break Explainer: build and evaluate   [P1 · WBS 2.5.20, 2.5.21]

As a steward, I want every dual-run difference explained by rule, field and cause, so that the factory does this step without a person typing.

- ≥ [T]% of differences auto-explained

- Explanation cites the catalog rule id

## F5.11 Gate Evidence Compiler

S5.11.1  Gate Evidence Compiler: build and evaluate   [P1 · WBS 2.5.22]

As a project manager, I want a gate pack assembled from verification results, approvals and metrics, so that the factory does this step without a person typing.

- Pack lists each gate criterion with its evidence

- A criterion with no evidence is shown as NOT MET

## F5.12 Docs & Runbook Writer

S5.12.1  Docs & Runbook Writer: build and evaluate   [P2 · WBS 2.5.23]

As an operations user, I want source documentation and runbooks generated from artifacts, so that the factory does this step without a person typing.

- Runbook used successfully in the chaos drill

- Docs regenerate on every release

## F5.13 Guardrails and autonomy

S5.13.1  Guardrails and autonomy: build and evaluate   [P1 · WBS 2.5.24]

As an architect, I want autonomy levels L0–L3 enforced per agent per task class, so that the factory does this step without a person typing.

- Level change requires approval and is logged

- L3 requires measured acceptance above threshold over a window

# E6 — Control plane application

The workbench. Owner: platform engineering with UX input from the Envestnet ops team.

## F6.0 UX research, wireframes and design system

S6.0.1  Personas and task flows validated with Envestnet   [P1 · WBS 2.6.9]

As a UX designer, I want the persona set (ops reconciler, BSA, steward, engineer, PM, auditor) and their daily task flows validated in two sessions with Envestnet ops and BSAs, so that we build the screens people will actually use.

- Six personas documented with their top three tasks

- Task flows reviewed against Envestnet's self-servicing draft; gaps listed

- Sign-off from the ops lead and one BSA

S6.0.2  Wireframes and clickable prototype for the four high-traffic screens   [P1 · WBS 2.6.10]

As a UX designer, I want wireframes and a clickable prototype for the exception UI, suggestion review, custodian page and run status, so that layout problems are found before code.

- Prototype walked through with three Envestnet users; findings logged

- Each screen has empty, loading, error and success states drawn

S6.0.3  Design system   [P1 · WBS 2.6.11]

As a front-end engineer, I want a component library (tables, diff view, status chips, approval bar, filters, forms) with plain-language error messages, so that screens are consistent and quick to build.

- Components documented with states

- Every error message says what happened and what to do next

- Colour is never the only carrier of meaning

S6.0.4  Usability test on the pilot and accessibility check   [P1 · WBS 2.6.12]

As a UX designer, I want a usability session with real ops users during the pilot and a WCAG AA check, so that the tool is adopted, not tolerated.

- Five tasks completed by three users; issues ranked and fixed before G1

- Keyboard navigation and contrast pass on all P1 screens

## F6.1 Factory board and config studio

S6.1.1  Factory board   [P1 · WBS 2.6.1]

As a delivery lead, I want every custodian shown at its station with WIP limits per stream, so that flow and blockers are visible.

- Stations: profile, draft, dry-run, dual-run, cut over

- WIP limit per stream configurable; exceeding it is blocked

- Custodians live per week is shown

S6.1.2  Config studio   [P2 · WBS 2.6.2]

As a BSA, I want to profile a sample, review the drafted config, dry-run it and request promotion without engineering, so that simple custodians are self-service.

- A simple custodian goes sample → promotion request with no engineer involved

- Every step is recorded with who and when

S6.1.3  Diff review   [P1 · WBS 2.6.3]

As a steward, I want a side-by-side diff of a config change with citations and impact, so that approvals are informed.

- Diff shows changed fields, rules and affected custodians

- Citation link opens the spec page or code line

## F6.3 Workbench screens

S6.3.1  Sign-in, roles and permissions   [P1 · WBS 2.6.13]

As a platform administrator, I want SSO sign-in and role-based permissions (steward, BSA, engineer, ops, PM, read-only auditor), so that each person sees only the actions they are allowed.

- SSO via the client's identity provider

- Each role's allowed actions listed and enforced server-side

- An auditor role can read everything and change nothing

S6.3.2  Home / my queue   [P1 · WBS 2.6.14]

As any user, I want a home page that lists what needs me today: approvals, exceptions assigned, breaks to explain, drift changes, so that nobody hunts for work.

- Queue filtered by my role and assignments

- Counts refresh without reload

- Items open the right screen in one click

S6.3.3  Custodian page   [P1 · WBS 2.6.15]

As an operations user, I want one page per custodian: family, tier, config version, current station, files today, parity trend, open exceptions, cost, so that all context about a custodian is in one place.

- Loads in under two seconds

- Links to spec, config diff, parity viewer, exceptions

- Shows expected vs arrived files for today with the cutoff time

S6.3.4  Spec registry viewer   [P2 · WBS 2.6.16]

As an engineer, I want to browse a Source Spec with its citations and compare two versions, so that layout changes are understood before they bite.

- Field list with position, type, citation link

- Version compare highlights added, removed and shifted fields

S6.3.5  Rule catalog browser and review   [P1 · WBS 2.6.17]

As a steward, I want to see each recovered rule beside its code citation and mark it confirmed, rejected or legacy defect with a comment, so that Stage 1 confirmation is a screen, not a spreadsheet.

- Rule text, class, source file:line rendered side by side

- Status change records who, when, comment

- Filter by rejection code, custodian, status; bulk confirm for identical rules

S6.3.6  Agent suggestion review   [P1 · WBS 2.6.18]

As a steward or BSA, I want to see an agent's draft, its reasoning and citations, and accept, edit or reject it, so that approval is informed and fast.

- Draft shown with the source evidence beside it

- Edit keeps the original for comparison

- Accept / reject feeds the agent evaluation set automatically

S6.3.7  Dual-run and parity viewer   [P1 · WBS 2.6.19]

As a steward or QE engineer, I want match rate by day, break groups, drill-down to a record pair and the break explanation, so that gate evidence can be inspected, not just read.

- Trend chart of match rate per custodian

- Break groups by rule and field with counts

- Record pair shown legacy vs lakehouse with differing fields highlighted

S6.3.8  Run status dashboard   [P1 · WBS 2.6.20]

As an operations user or SRE, I want per custodian, file and run: expected → arrived → parsed → resolved → published with timings against the 20-minute window, so that late or slow custodians are seen at a glance.

- Late list with missing files and cutoff

- Timing bar per custodian against the 20-minute budget

- Click-through to the run log

S6.3.9  Drift change review   [P2 · WBS 2.6.21]

As an engineer or steward, I want to see a detected layout change, the proposed config delta and the impact list, and approve it to non-prod, so that drift is handled before it breaks a run.

- Diff of spec versions and of config

- Impact list of affected custodians and consumers

- Approve creates a Git change; never touches prod

S6.3.10  Golden dataset viewer   [P2 · WBS 2.6.22]

As a QE engineer, I want to see which business days are captured per custodian, with hashes and gaps, so that parity runs are complete.

- Calendar view of captured days

- Gap highlighted; capture can be requested from the screen

S6.3.11  Audit log viewer   [P2 · WBS 2.6.23]

As an auditor or security reviewer, I want to search who approved what, when, with the evidence they saw and the agent version, so that SOX evidence is a query.

- Filter by user, custodian, date, action

- Export to CSV

S6.3.12  Admin: autonomy levels and whitelist   [P2 · WBS 2.6.24]

As an architect, I want to set L0–L3 per agent and task class and manage the self-healing whitelist, so that autonomy is a controlled setting.

- Change requires a reason and is logged

- L3 cannot be set unless the measured acceptance rate is shown above threshold

S6.3.13  Notification preferences   [P2 · WBS 2.6.25]

As any user, I want to choose which alerts reach me on which channel, so that alerts are read, not muted.

- Per-user settings for Slack, email, in-app

- Severity thresholds per custodian for ops roles

## F6.2 Approvals, audit and evidence

S6.2.1  Approvals and autonomy levels   [P1 · WBS 2.6.4]

As a steward, I want to approve or reject with a comment, and to see the autonomy level of the agent that proposed, so that every change has an accountable person.

- Approval stores user, time, evidence seen, agent version

- Rejection returns the item to the agent with the comment

S6.2.2  Git as system of record   [P1 · WBS 2.6.5]

As a data engineer, I want every approved change committed with provenance, so that the client can leave with a working repo.

- One commit per approval with PROVENANCE.json

- No artifact exists only in the factory database

S6.2.3  Throughput and cost metrics   [P2 · WBS 2.6.6]

As a project manager, I want custodians live per week, agent acceptance and credits per custodian per day, so that reporting is generated.

- Weekly report exported for the client cadence

- Cost per custodian visible from query tags

S6.2.4  Gate evidence pack UI   [P1 · WBS 2.6.7]

As a project manager, I want to assemble and export the evidence pack for a gate, so that gate reviews run on evidence.

- Pack exported as PDF with criteria, evidence, approvals

S6.2.5  Ops exception UI   [P1 · WBS 2.6.8]

As a reconciliation operator, I want to see exceptions grouped by cause, accept a suggestion, edit, resubmit and close, so that the manual queue shrinks.

- Exception moves through new → suggested → approved → resubmitted → closed

- Resubmission re-runs the record through resolution

- Ageing report by code

# E7 — Envestnet instance

The client-specific instance the factory produces. Owner: data lead.

## F7.1 Silver, resolution and reconciliation

S7.1.1  Silver CDM on Iceberg   [P1 · WBS 2.7.1]

As a data engineer, I want the Silver tables created from the domain pack, so that the target model exists.

- All entities present; keys enforced by tests

S7.1.2  SOS and CAS replication   [P1 · WBS 2.7.2, 2.7.3]

As a data engineer, I want security master and account cross-reference replicated nightly, so that resolution is set-based.

- Row counts logged; deltas visible; job alerts on failure

S7.1.3  Account, security, tx-code and price resolution configured   [P1 · WBS 2.7.5–2.7.8]

As a data engineer, I want the four resolutions configured to Loader behaviour with orphan/claim rules, so that rejection parity is achievable.

- Each rejection code reproduced on seeded data

- Orphan policy per custodian from config

S7.1.4  Exception store and state machine   [P1 · WBS 2.7.9]

As an operations user, I want exceptions stored with state transitions and owners, so that the workflow has a backbone.

- State transitions tested; invalid transitions rejected

S7.1.5  Reconciliation engine   [P1 · WBS 2.7.10]

As a steward, I want control totals and the position identity (prior + transactions) checked with tolerances, so that breaks are detected automatically.

- Seeded break detected and categorised

- Tolerance per field type configurable

S7.1.6  Rejection taxonomy parity   [P1 · WBS 2.7.11]

As a QE engineer, I want all 50+ codes reproducible as exception rows, so that parity with the Loader is complete.

- Test per code passes

## F7.2 Gold, read path and window

S7.2.1  UMP read model   [P1 · WBS 2.8.1]

As a UMP application owner, I want Gold tables in the shape UMP reads today, so that UMP queries run unchanged.

- Existing UMP queries return the same result set on the pilot custodians

S7.2.2  pg_lake read via Open Catalog   [P1 · WBS 2.8.4]

As a UMP application owner, I want UMP's Postgres reading the Gold Iceberg tables through pg_lake, so that cutover needs no data copy.

- UMP reads pilot custodians from Iceberg in production

- Catalog refresh latency under one minute

S7.2.3  Status table and watermark   [P1 · WBS 2.8.2, 2.8.3]

As an operations user, I want per custodian, file and run status with a published watermark, so that completeness is visible.

- Expected vs arrived vs parsed vs resolved vs published shown

- Watermark written last

S7.2.4  Window met at 3×   [P1 · WBS 2.8.6]

As a QE engineer, I want the end-to-end run measured at three times volume, so that the 20-minute requirement is evidenced.

- ≤ 20 minutes after last file

# E8 — Governance integration

S8.1  Atlan connector and OpenLineage   [P1 · WBS 2.9.1, 2.9.2]

As a governance engineer, I want datasets registered in Atlan from config with column-level lineage from file to Gold, so that governance is generated.

- 100% of live datasets registered with owner and PII tags

- Lineage traversable file → Bronze → Silver → Gold

S8.2  Glossary and semantic views   [P2 · WBS 2.9.3, 2.9.5]

As a steward, I want business definitions in Atlan linked to columns and encoded as Snowflake semantic views, so that one definition of position for all consumers.

- Glossary term for each CDM entity

- Semantic views for position, market value by firm, breaks

S8.3  DQ scores in Atlan   [P2 · WBS 2.9.4]

As a data consumer, I want quality scores visible on each dataset, so that trust is visible.

- DMF results appear in Atlan within a day

S8.4  PII tagging, masking and access history   [P1 · WBS 2.9.6, 2.9.8]

As a security reviewer, I want PII columns tagged, masked for non-privileged roles, and access retained, so that SOX / SOC 2 evidence exists.

- Masked values for non-privileged roles

- Access history query returns reads by user and column

S8.5  RACI recorded   [P1 · WBS 2.9.7]

As a governance architect, I want owners and stewards recorded per entity, stage and rule in Atlan, so that every dataset has an accountable person.

- No dataset without an owner at G0

# E9 — Historical migration

S9.1  Schema extract and convert with SnowConvert AI   [P2 · WBS 3.3.1]

As a migration engineer, I want the SQL Server schema extracted and converted to Snowflake DDL, so that the archive store matches the source shape.

- Converted DDL deploys; differences listed

S9.2  Partition plan by custodian and year   [P2 · WBS 3.3.2]

As an architect, I want a migration plan sized from Discovery volumes, so that loads are trackable.

- Plan approved; each partition is one WBS-sized task

S9.3  Data migrate and validate   [P2 · WBS 3.3.3, 3.3.4]

As a migration engineer, I want partitions loaded with counts, checksums and sampled parity, so that history is proven complete.

- Zero lost rows per partition; sampled parity ≥ [T]

S9.4  Archive store PII tags and runbook   [P2 · WBS 3.3.5, 3.3.6]

As a governance engineer, I want the archive store tagged and a runbook generated, so that the store is governed.

- Tags applied; runbook used for the first load

# E10 — Cutover and operations

S10.1  Consumer read models   [P3 · WBS 4.1.1–4.1.3]

As an application owner, I want Tamarac and other consumers reading from Gold, so that SQL Server can be retired.

- Query parity per consumer

S10.2  Decommission plan and execution   [P3 · WBS 4.1.6, 4.1.7]

As a platform engineer, I want the SQL Server ingestion path retired per custodian with validation, so that the legacy path is gone.

- Legacy reads = 0 after retirement

S10.3  Runbooks, DR drill and KT   [P3 · WBS 4.2.1–4.2.4]

As an operations lead, I want generated runbooks, a passed DR drill and knowledge transfer, so that support can run alone.

- Support team completes a week solo without escalation

# Appendix — Priority and sequencing

| Priority | Meaning | Target |
|---|---|---|
| P1 | Needed for the pilot: three custodians live via pg_lake, Go/No-Go evidence | Week 10 (G1) |
| P2 | Needed for family releases at scale, self-service and migration | Week 14 |
| P3 | Needed for cutover of remaining consumers and handover | Week 26 |

Sprint length two weeks. Sprint 1 starts week 3 (E1, E2, E3 foundations); E5 agents start in sprint 1 in parallel; E7 from sprint 2; pilot custodians from sprint 3. The WBS start/end weeks give the intended order.

