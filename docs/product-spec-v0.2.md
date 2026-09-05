Astra Data Factory

Product specification — an agent-run platform that generates, tests and operates data pipelines, migrations and data quality

Working name. Version 0.2 · Artizent Product Engineering, Data & AI · 5 September 2026 · Internal · Companion: Astra Data Factory — Epics, Features and User Stories v0.1

In one sentence. Give the factory a source description, a target platform and a domain pack; it produces running, tested, governed pipelines — plus migration scripts, data-quality rules, documentation and gate evidence — with people approving, not typing.

Why now. Every data engagement we run has the same six stages: understand the source, recover the rules, model the target, build the pipeline, prove parity, operate. We do them by hand each time. The Envestnet custodian pursuit is the first design partner; the same factory serves the BlackRock Tableau migration, the UNFCU Fabric pilot and the GE HealthCare HL7 work with different domain packs.

Contents


# 1. What the product is

Astra Data Factory is a platform where agents do the data engineering and people do the deciding. It takes three inputs and produces one output:

| Input | What it is | Example (Envestnet) |
|---|---|---|
| Source description | Layout documents, sample files, existing code or database schemas, API specs | Pershing GCUS layout PDF + sample file; Splitter/Loader Java |
| Target profile | The platform and conventions to generate for | Snowflake-managed Iceberg, Dynamic Tables, dbt for Gold, Atlan for catalog |
| Domain pack | Canonical model, vocabulary, rule patterns and DQ patterns for an industry area | Custodial / wealth: Account, Security, Position, Transaction, Price; reconciliation identity; rejection taxonomy |

Output: a release bundle — configuration, generated code, DDL, DQ rules, tests, golden datasets, migration scripts, lineage registrations, documentation, runbooks and a gate evidence pack — committed to Git, deployed through CI/CD, and observed in production. Every artifact is traceable to the input that produced it and the person who approved it.

The factory is not a low-code ETL tool. It generates the same code a good engineer would write on the client's chosen platform, and the client keeps that code. The agents are the engineers; the platform is their workbench, their knowledge and their guardrails.

# 2. Product principles

- Agents propose, humans approve, deterministic code runs. No LLM sits in a production data path.

- Patterns before instances. The first source of a kind is engineered; the rest are configuration.

- Every artifact cites its source: spec line, code line, sample row, or approver.

- Generate native code for the client's platform. No runtime lock-in, no proprietary engine.

- Parity is proven with data, not asserted. Golden datasets and dual-run are built in.

- Knowledge compounds. Every approval improves the pattern library and the agents' evaluation set.

- Client data stays in the client's environment. The factory runs there or touches only metadata and samples.

- Autonomy is earned per task class, measured in shadow mode, and revocable.

- Plain language everywhere: a BSA can read a config, a steward can read a rule, an auditor can read a change.

- The evaluation harness is part of the product, not a project deliverable.

- Use the target platform's own migration and quality tooling where it exists (Snowflake SnowConvert AI, AIM, Snowpark Connect, native DMFs); Astra Data generates only what those tools do not cover.

# 3. Who uses it

| Persona | What they do in the factory | What they must never have to do |
|---|---|---|
| Data engineer (Artizent or client) | Reviews generated code and config for complex sources; extends patterns and target profiles; resolves parity breaks the agents cannot | Hand-write a parser, a mapping or a DQ rule that a pattern already covers |
| Ops / business analyst | Onboards simple and medium sources: profile, review draft, dry-run, request promotion; handles exceptions with agent suggestions | Raise a development ticket for a mapping change |
| Data steward / rule owner | Confirms recovered rules, approves CDM changes, signs gates | Reverse-engineer code to find out what a rule does |
| Solution architect | Sets target profile and domain pack; decides build/extend/replace; owns the evaluation thresholds | Draw the same architecture diagram again |
| Client sponsor / auditor | Reads gate evidence, throughput, change history | Trust a status report without evidence behind it |
| Artizent delivery lead | Runs the factory board, pace dial, and agent autonomy levels | Estimate effort per source by hand |

# 4. Core concepts

| Concept | Definition | Stored as |
|---|---|---|
| Source Spec | A machine-readable description of a source: files, records, fields, types, codes, effective dates, schedules | YAML in the spec registry, versioned |
| Pattern | A reusable way to handle a class of source or rule: fixed-width multi-record file; signed implied-decimal numbers; full-vs-delta merge; DRIP split; cancel/correct lifecycle | Pattern definition + renderer template + tests |
| Domain Pack | Canonical model, vocabulary, rule patterns, DQ patterns, rejection taxonomy, reference-data patterns for one industry area | Versioned package |
| Target Profile | Platform conventions: table format, ingestion service, transformation engine, orchestration, catalog, alerting, IaC | Profile definition + renderers |
| Canonical Model (CDM) | The shared target model for a domain, with versions and migration paths | DDL + semantic definitions + glossary |
| Rule Catalog | Every business rule, mapping, validation and exception rule, with citation, owner, status (recovered / confirmed / rejected) and lineage | Database + Git |
| Config | One versioned instance of a Source Spec mapped to a CDM under a Target Profile, with rules and resolution parameters | YAML in Git |
| Golden Dataset | Captured legacy outputs (or approved factory outputs) used as the oracle for parity | Object storage, versioned, hashed |
| Release | A set of configs promoted together with an evidence pack | Git tag + evidence pack |
| Gate | A named checkpoint with criteria, evidence and approvers | Workflow state |

# 5. Architecture — five planes

The platform is organised as five planes. Each plane has a clear owner and can be replaced independently.

| Plane | Responsibility | Main components |
|---|---|---|
| Knowledge | What the factory knows | Spec registry · Pattern library · Domain packs · CDM library · Rule catalog · Target profiles · Approval history |
| Agents | Who does the work | Agent catalog (Section 6) · Versioned task instructions · Tools via MCP (Snowflake, Git, Atlan, dbt, Jira, S3, code search) · Model routing |
| Generation (Astra Data) | Turning decisions into artifacts | Renderers per target profile · Code templates · Config compiler · DDL and semantic-view generators · Document generators · Integrations with the target platform's converters (Snowflake SnowConvert AI / AIM for SQL Server sources, Snowpark Connect for Spark code kept as-is) |
| Verification | Proving it works | Dry-run sandbox · Golden replay · Parity engine · DQ runner · Connector to the client's DQ / parity tool (e.g. iceDQ, Datagaps) · NFR runner (volume, chaos, DR) · Agent evaluation harness |
| Control | Running the factory | Workbench UI · Workflow and state · Approvals and autonomy levels · Git as system of record · Audit log · Throughput and cost metrics · Gate evidence compiler |

## 5.1 How the planes connect

- A source enters through the Control plane (workbench or API) and is placed on the factory board.

- Agents read the Knowledge plane (spec registry, patterns, domain pack) and produce decisions: a Source Spec, a pattern match, a mapping, a rule set.

- The Generation plane renders those decisions into artifacts for the Target Profile: config, DDL, pipeline code, DQ rules, tests, docs.

- The Verification plane runs the artifacts in a sandbox against samples and golden datasets and scores the result.

- The Control plane routes results to the right human at the right autonomy level, records approvals, promotes through environments, and compiles gate evidence.

- Approvals and corrections flow back into the Knowledge plane: the pattern library, the rule catalog and the agents' evaluation sets improve.

Git is the system of record for everything generated. The factory's database holds state, metrics and audit; it never holds the only copy of an artifact. A client can leave with a repository that runs without the factory.

# 6. Agent catalog

Each agent is a bounded worker with defined inputs, outputs, tools, a guardrail and a metric. Agents do not call each other freely; the workflow engine sequences them. Autonomy levels are per agent per task class (Section 8).

| Agent | Input | Output | Guardrail | Metric |
|---|---|---|---|---|
| Spec Reader | Layout documents (PDF, Word, HTML), API specs, DDL | Source Spec in the registry, with page/line citations | Unparsed sections are flagged, never guessed | Field-level accuracy vs a reviewed spec |
| Profiler | Sample files or tables | Observed types, nulls, value sets, key candidates, record-type detection, drift vs spec | Read-only; samples only | Findings confirmed by reviewer |
| Pattern Matcher | Source Spec + profile | Family assignment, pattern list, tier, reuse candidates | New pattern requires architect approval | Correct family assignment rate |
| Rule Recovery (Astra RE Harness) | Legacy Java (MVP); configs, tickets. T-SQL and SSIS are routed to SnowConvert AI; Spark to Snowpark Connect assessment | Rule catalog entries with file:line citations, classified; candidate tests | Correctness is confirmed by the owner, not the agent | % of legacy behaviours traced; test pass on golden data |
| Modeler | Source Spec + domain pack CDM | Field mappings, transformations, resolution parameters, CDM change requests | Breaking CDM changes go to the steward | Mapping precision/recall vs reviewed configs |
| Pipeline Generator | Config + Target Profile | Ingestion, parse, merge, resolution, publish code; orchestration; IaC | Only renders from approved config; no free-form code | Generated code passes tests first time |
| DQ Generator | Spec, config, domain pack DQ patterns | File, record, pair, and business-rule checks; thresholds; ownership | Thresholds from the client's targets, never invented | Defect escape rate after go-live |
| Test Generator | Config, rules, samples | Unit and edge-case tests; synthetic edge files; regression pack | Synthetic data never contains real PII | Coverage of rule branches |
| Migration Planner | Legacy schema, volumes, target profile | Partitioning plan; SnowConvert AI / AIM commands for extract, convert, migrate and validate (SQL Server → Snowflake); validation harness | Plan approved before any load | GB/h, sampled parity, zero lost rows |
| Parity / Break Explainer | Dual-run differences, golden data | Grouped explanations: rule, field, source; fix suggestion or legacy defect | Reports only | Time to explain a break; % breaks auto-explained |
| Drift Watcher | New files vs spec/config | Drift detection and proposed config delta | Never touches production config | Drift caught before Silver |
| Exception Triage | Exception rows, reference data, approval history | Suggested resolution with confidence, grouped by root cause | Auto-apply only for whitelisted classes | Acceptance rate; queue reduction |
| Docs & Runbook Writer | Config, code, status model | Source docs, runbooks, KT pack, catalog entries | Generated from artifacts, not prose | Runbook used successfully in a drill |
| Gate Evidence Compiler | Verification results, approvals, metrics | Gate pack per release against named criteria | Cannot mark a criterion met without evidence | Gate decisions made on the pack alone |
| FinOps | Query tags, warehouse metrics | Cost per source per day; sizing suggestions | Suggests only | Cost per source within target |

# 7. End-to-end workflows

## 7.1 Onboard a new source

- Ops drops a layout document and sample files on the board.

- Spec Reader and Profiler run; Pattern Matcher assigns family and tier.

- Modeler drafts the config; DQ Generator and Test Generator add checks and tests.

- Pipeline Generator renders the target code; the sandbox dry-runs samples; Gate Evidence Compiler shows the diff and scores.

- Steward reviews (simple tier: BSA reviews). Approval promotes to QA, then to a dual-run in production alongside the legacy path.

- Parity engine compares against golden outputs for N days; Break Explainer handles differences.

- Cutover: watermark published, legacy path switched off for the source, runbook attached.

Release order: after the pilot, the first family in each stream is chosen by value — the highest-volume family so reconciliation savings show early, and the family with the most legacy-code reuse to prove the extend path.

## 7.2 Replace a legacy pipeline (reverse-engineer → regenerate → prove)

- Rule Recovery runs on the legacy code and produces the catalog with citations and candidate tests.

- Golden datasets are captured by replaying history through the legacy path in a non-production copy.

- Owners confirm or reject rules in the catalog; rejected rules are recorded as legacy defects.

- Modeler and Pipeline Generator regenerate on the target platform from the confirmed catalog — or, where the assessment scored it higher, the existing Spark code is kept and run through Snowpark Connect.

- Golden replay proves parity; the evidence pack shows every difference and its explanation.

- The legacy path is retired per source, not big-bang.

## 7.3 Historical data migration

- Migration Planner profiles volumes and proposes partitioning (e.g. by source and year).

- DDL mapping generated for the target; PII tags applied from the domain pack.

- Load scripts generated for the target's bulk path (Snowflake: SnowConvert AI data migrate / COPY; Fabric pipelines; Databricks Auto Loader).

- Validation harness: counts, checksums, sampled record parity; sign-off pack generated.

## 7.4 Data quality programme

- DQ Generator derives rules from spec, config and domain pack; thresholds from client targets.

- Rules rendered as native checks (Snowflake DMFs, dbt tests, Great Expectations) and registered in the catalog with owners.

- Scores flow to the client's governance tool; exceptions flow to the exception store.

- Exception Triage proposes resolutions; whitelisted classes self-heal; the rest go to people with explanations.

## 7.5 Change with replay

- A drift event, a rule change or a CDM change opens a change on the board.

- The agent proposes the config delta; the golden replay harness runs N days of history against old and new.

- The diff and impact list (which sources, which consumers) go to the steward.

- Approval promotes the change; the pattern library records it if it generalises.

# 8. Human-in-the-loop and autonomy

Every agent task class has an autonomy level. Levels are set per client, measured in shadow mode, and can be lowered at any time.

| Level | Meaning | Typical use |
|---|---|---|
| L0 · Observe | Agent runs, output is logged, nobody sees it in the flow | New agent or new domain pack in shadow mode |
| L1 · Suggest | Output shown to a human who decides | Rule recovery, CDM changes, complex-tier mappings |
| L2 · Prepare | Agent prepares the change and the evidence; human approves with one click | Simple/medium onboarding, DQ rules, docs |
| L3 · Act with audit | Agent applies the change; human notified; reversible | Whitelisted exception classes, drift config deltas in non-prod, cost sizing |

- No agent ever decides that a business rule is correct. Owners do.

- No LLM output executes in a production data path. Generated code is deterministic and reviewed.

- Every approval records who, what, the evidence seen, and the agent version.

- Promotion to L3 requires a measured acceptance rate above a threshold over a stated window, and is reviewed monthly.

# 9. Knowledge and learning loop

- Pattern library: grows: every approved config that introduces a new structure is proposed as a pattern; architects accept or merge.

- Rule catalog: remembers: confirmed rules, rejected rules and legacy defects are searchable across engagements (with client isolation).

- Evaluation sets: tighten: every human correction becomes a test case for the agent that missed it.

- Domain packs: deepen: custodial/wealth first; then insurance claims, HL7/FHIR, general ledger, BI semantic migration.

- Target profiles: widen: Snowflake first; Microsoft Fabric, Databricks, Postgres/pg_lake next.

Client isolation is a hard rule: a pattern learned at one client is generalised only as a structural pattern, never with that client's data, names or thresholds.

# 10. Target platform support

| Capability | Snowflake (MVP) | Microsoft Fabric | Databricks | Postgres / pg_lake |
|---|---|---|---|---|
| Storage format | Managed Iceberg on S3 | OneLake Delta | Delta / Iceberg | Iceberg via catalog |
| Ingestion | Snowpipe, COPY | Pipelines, Eventstream | Auto Loader | COPY / foreign tables |
| Transformation | Dynamic Tables, dbt, Snowpark | Notebooks, Dataflows, dbt | DLT, dbt | SQL, dbt |
| Orchestration | Tasks | Pipelines | Workflows | External |
| DQ | Data Metric Functions | Purview + dbt tests | Expectations | dbt tests / GE |
| Catalog / lineage | Horizon + Atlan | Purview | Unity Catalog | Atlan / OpenMetadata |
| Migration tooling | SnowConvert AI, AIM, Snowpark Connect (free with the account) | Fabric migration assistant | Lakebridge | — |
| Status | Renderers built for Envestnet | Roadmap phase 2 | Roadmap phase 2 | Read path in MVP; write path phase 3 |

# 11. Evaluation harness

The harness is what makes the factory trustworthy and sellable. It ships with the product.

- A gold set per agent: reviewed inputs and correct outputs (e.g. 20 custodian specs with approved configs). Every agent change runs against it before release.

- Shadow mode on every new client: agents run at L0 for a defined window; acceptance and precision/recall are published weekly.

- Regression on generated code: every target profile has a reference source that must pass end-to-end in the sandbox on every platform change.

- Drift injection: known layout changes are injected into historical files; the Drift Watcher must catch them.

- Metrics visible to the client: per agent, per tier, per week. Not a black box.

# 12. Security, tenancy and data handling

- Deployment options: (a) inside the client's cloud account, or (b) Artizent-hosted control plane with agents and sandboxes running in the client's account. Client data never leaves the client's account in either option.

- Agents see samples and metadata by default; production data access is a per-task grant with audit.

- Secrets in the client's secret manager; the factory holds references only.

- Model routing: tasks are tagged by sensitivity; sensitive tasks can be pinned to models and endpoints the client approves.

- Every generated artifact carries a provenance header: inputs, agent version, model, approver.

- Synthetic test data is generated from specs, never derived from real records containing PII.

# 13. Proposed technology

| Layer | Choice | Why |
|---|---|---|
| Agent runtime | Claude Agent SDK; agent instructions as versioned files in Git; MCP servers for tools | Same tooling we use in delivery; instructions are readable by humans |
| Tools (MCP) | Snowflake, Git, Atlan, dbt, Jira, S3/ADLS, code search, sandbox runner, SnowConvert AI CLI | Agents act through the same interfaces engineers use |
| Ingestion options | Snowpipe by default; Snowflake Openflow where a client wants managed connectors | Consumption-priced; no licence |
| Control plane | Python services; Postgres for state; Git as system of record; workflow engine (Temporal or equivalent) | Durable, replayable workflows; nothing lives only in memory |
| Workbench | React web app: factory board, review diffs, approvals, metrics, evidence packs | One screen for engineers, BSAs and stewards |
| Sandboxes | Ephemeral compute per task in the client account (containers, Snowflake schemas, Fabric workspaces) | Dry-runs and tests without touching shared environments |
| Generation | Template renderers per target profile; config compiler validates against schema | Deterministic output from approved decisions |
| Observability | Query tagging, cost per source, agent metrics, audit log | Throughput and cost are product features |

# 14. MVP scope and roadmap

## 14.1 MVP (design partner: Envestnet custodian ingestion)

- Sources: fixed-width and delimited files; multi-record layouts; header/trailer control totals.

- Domain pack: custodial / wealth (Account, Security, Position, Lot, Transaction, Price, Cash Balance, Exception; reconciliation identity; rejection taxonomy).

- Target profile: Snowflake-managed Iceberg, Snowpipe, Dynamic Tables, Tasks, DMFs, Atlan, Terraform; pg_lake read path.

- Agents: Spec Reader, Profiler, Pattern Matcher, Rule Recovery (RE Harness), Modeler, Pipeline Generator, DQ Generator, Test Generator, Parity, Drift, Exception Triage, Gate Evidence Compiler. (The Envestnet response presents five of these to the client — onboarding, rule recovery, triage, drift, parity — as the agents they interact with; the rest work behind them.)

- Workbench: factory board, config review, dry-run diff, approvals, gate packs, throughput metrics.

- Evaluation harness with the Pershing, Schwab and Fidelity gold set.

## 14.2 Roadmap

| Phase | Adds | Signal to start |
|---|---|---|
| 1 · MVP | Everything above; one design partner | Envestnet Go at pilot gate |
| 2 · Second target and domain | Microsoft Fabric and Databricks profiles; BI semantic migration domain pack (Tableau → Power BI) | BlackRock or UNFCU adoption |
| 3 · Legacy code breadth | RE Harness for .NET, SSIS, Informatica, Talend; write path for Postgres | Two clients with mixed legacy estates |
| 4 · Streaming and APIs | Event sources, API sources, CDC patterns | Client demand for intra-day |
| 5 · Marketplace | Shared spec registry for common providers; certified domain packs | Three domain packs in production |

# 15. Positioning and commercial model

- Internal first: the factory is how Artizent delivers data engagements at fixed price and at risk (as proposed to Envestnet). Its first return is margin and speed.

- Client-facing second: after two design partners, offer it as a platform with a per-source-onboarded price and a subscription for the workbench and agents. The generated code always belongs to the client.

- Relationship to the Astra family: Astra Data is the Generation plane (the engine that renders native code from approved config); the Astra RE Harness is the Rule Recovery agent for legacy Java. Nothing else in the Astra family is assumed. Confirm naming with the Astra product owner before external use.

- What we do not claim: full autonomy, or that the factory replaces stewards, architects or the client's platform. It removes typing and waiting, not judgement.

# 16. Product KPIs

| KPI | Definition | Target (MVP) |
|---|---|---|
| Sample-to-production time | Working days from sample file received to source live in production | Simple ≤5 · Medium ≤10 · Complex ≤20 |
| Agent acceptance rate | Share of agent outputs approved without material edit, by agent and tier | ≥80% simple · ≥60% medium after shadow window |
| Parity first-pass | Share of sources reaching ≥99.5% parity within one dual-run cycle | ≥70% |
| Legacy traceability | Share of legacy rules and rejection codes traced with citations | 100% of rejection codes |
| Manual exception reduction | Reduction in human-handled exceptions vs baseline | ≥30% within two quarters |
| Cost per source per day | Platform compute cost attributed per source | Within client target; visible weekly |
| Defect escape | Data defects found in production per source per month | Trend to zero critical |

# 17. Risks and open questions

- Naming and ownership inside Artizent: Astra Data and the RE Harness have owners; the factory needs one product owner. [DECIDE]

- Model dependence: agent quality tracks model quality; the evaluation harness is the mitigation, and model routing keeps options open.

- Over-generalisation: a pattern that works for custodial files may not fit HL7; domain packs must stay separate and be certified per domain.

- Client appetite for at-risk pilots depends on the harness being credible on day one; the Envestnet gold set is the first proof.

- Legacy code variety: the RE Harness scope for the MVP is Java; confirm what the Envestnet Splitter/Loader actually is.

- Regulatory review: some clients will want model and data-handling attestations before agents touch even samples; prepare a standard pack.

- Tooling cost: the Snowflake converters are free with the account, but AI-assisted conversion may draw Cortex credits; confirm metering before promising "no added licence cost".

# Appendix A — Example release bundle for one source

releases/pershing-position-2026.09/

spec/GCUS_20170725.yaml            # Source Spec with page citations

config/pershing_position.yaml      # mappings, rules, DQ, resolution, effective dates

ddl/bronze_pershing_position.sql   # Iceberg tables

pipeline/bronze_parse.sql          # Dynamic Table rendered from config

pipeline/silver_merge.sql          # MERGE with rejection routing

pipeline/tasks.sql                 # per-custodian Tasks DAG

dq/dmf_pershing_position.sql       # Data Metric Functions

tests/unit/*.sql  tests/edge/*.dat # generated tests and synthetic edge files

golden/2026-08-01..08-31/          # captured legacy outputs (hashes + storage refs)

parity/report.md                   # dual-run results and break explanations

docs/source.md  docs/runbook.md    # generated documentation

catalog/atlan.json                 # registrations, owners, PII tags, lineage

evidence/G2-release-pack.pdf       # gate criteria with evidence

PROVENANCE.json                    # inputs, agent versions, models, approvers

# Appendix B — Control-plane API sketch

POST /sources                 register a source (spec docs, samples, owner)

POST /sources/{id}/run        run the onboarding workflow to a named stage

GET  /sources/{id}/board      current stage, blockers, pending approvals

POST /approvals/{id}          approve / reject with comment; records evidence seen

POST /changes                 open a change (drift, rule, CDM) with replay window

GET  /releases/{id}/evidence  gate pack

GET  /metrics/agents          acceptance, precision/recall by agent and tier

GET  /metrics/throughput      sources live per week, time-to-production by tier

GET  /metrics/cost            cost per source per day

