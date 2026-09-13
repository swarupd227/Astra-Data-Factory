# Astra Data Factory

An Artizent platform where agents generate, test and operate data pipelines, migrations and data quality, and people approve. Give it a source description, a target profile and a domain pack; it produces running, tested, governed pipelines with the evidence behind them.

Product specification: [docs/product-spec-v0.2.md](docs/product-spec-v0.2.md). Backlog: [docs/backlog-v0.2.md](docs/backlog-v0.2.md). Decisions: [docs/adr](docs/adr).

## Repository layout

| Path | Plane | Contents |
|---|---|---|
| `core` | shared | `astra-core`: line-aware YAML, problem reporting, schema error wording, Snowflake connection. Every plane depends on it. |
| `specs` | Knowledge (E2) | The spec registry: one directory per layout, one file per version, validated on every pull request |
| `knowledge` | Knowledge (E2) | `astra-spec`: the registry, resolution of the version in force, and later patterns, domain packs and the rule catalog |
| `domains` | Knowledge (E2) | Domain packs: glossary, canonical data model versions with rendered DDL and key tests, the rejection taxonomy and the reference-data feeds |
| `rules` | Knowledge (E2) | The rule catalog: one file per business rule with citation, class, owner, status and its history; configs reference rules by id |
| `configs` | Knowledge (E2) | Source configs, one YAML file per source, validated against the schema, the spec registry and the rule catalog on every pull request |
| `assessments` | Discovery (WBS 1.4) | Assessment memos with their evidence: the Normalizer decision's Spark transformers, their Snowpark Connect versions, recorded changes, samples, expected rows and harness results |
| `golden` | Verification (E4) | Golden datasets: per pilot custodian, how the legacy path is replayed, the index of every captured version (hash, source files, store reference), and how a captured output compares with the lakehouse (keys, fields, tolerances); the data lives in the golden bucket, read-only |
| `migrations` | Generation (E3) | Historical migrations driven through SnowConvert AI: one file per SQL Server source naming the schemas, the archive-store schema and the command line of each phase; results and logs land with the release |
| `releases` | Generation (E3) | Release bundles rendered from configs and domain packs (reference-data replication, Gold read models and the watermark) and written by migration runs (converted archive-store DDL with logs and results); deployed by the pipeline |
| `generation` | Generation (E3) | `astra-data`: config validation, bundle deploy and generated tests. The compiler and renderers grow here. |
| `verification` | Verification (E4) | `astra-verify`: ephemeral sandboxes per task, dry runs of a drafted config, golden dataset capture, replay of a config change against captured history, the parity engine and its report across a dual-run cycle window, the DQ runner and its per-entity scores, a connection sheet for the client's own DQ tool, the 3x volume test, chaos scenarios proving recoverability, a DR drill proving RTO and RPO, the agent evaluation harness, and the Snowpark Connect assessment harness. |
| `agents` | Agents (E5) | `astra-agents`: bounded agent workers. The Spec Reader (S5.1.1) turns a layout document into a Source Spec draft, with page citations, a person's approval away from the registry. The Profiler (S5.2.1) reads a sample file against a spec already in the registry and flags a field whose observed data disagrees with the type the spec declares — read-only, samples only. The Pattern Matcher (S5.3.1) assigns an unclassified spec a family, tier and pattern list by comparing detail-record field shapes, and routes a genuinely new shape to the architect queue instead of guessing. Rule Recovery (S5.4.1) turns legacy Splitter/Loader Java into rule catalog entries with file:line citations, always `recovered`, never `confirmed` by the agent; T-SQL found is routed to SnowConvert AI, not parsed. The Modeler (S5.5.1) maps a spec to the domain pack's canonical model — a mapping can only target a real CDM column, a breaking model change is raised as a request and never applied, and a rule needing SME confirmation is tagged `CONFIRM_WITH_LOADER`. The DQ Generator (S5.6.1) proposes control-total, sign-field, date, key and pairing DQ rules straight from a spec's own structure — deterministic, no fabricated thresholds. The Test Generator (S5.7.1) turns a config's own `dq_rules` into SQL assertion tests and synthetic edge files, one branch at a time, entirely from its own fixed synthetic vocabulary. Exception Triage (S5.8.1) suggests a resolution per exception from the domain pack's own rejection taxonomy, grouped by root cause, with confidence from a measured acceptance rate; auto-apply needs both the taxonomy's whitelist and a track record that has earned it. The Drift Watcher (S5.9.1) compares a delivered file against its spec and proposes a delta — a record-length or code-set change — never writing to the spec itself; more of the Agents plane (E5) is still to come (F5.10-F5.13). Per agent, a gold set (`eval.yaml`) of reviewed inputs and correct output, scored by `astra-verify agent-eval` (S4.3.4); `agents/examples` holds worked examples for the Spec Reader, Profiler, Rule Recovery, Modeler, Exception Triage and the Drift Watcher (all but Spec Reader's illustrative — no real sample file, legacy Java, live model run or exception data backs them yet), while `agents/pattern_matcher` and `agents/test_generator` are real gold sets, built from the real `specs/` registry and the real `configs/examples/pershing_position.yaml` respectively. |
| `infra/terraform/foundation` | Control (E1) | Per environment: Snowflake database, schemas, roles, tier-sized warehouses, Iceberg bucket and external volume, Open Catalog sync, landing zone with Snowpipe and file load log |
| `infra/terraform/bootstrap` | Control (E1) | Once per AWS account: the bucket that holds Terraform state for every environment |
| `tools/opencatalog` | Control (E1) | Provisions and verifies Snowflake Open Catalog, which has no Terraform provider |
| `.github` | Control (E1) | `ci` on every pull request; `deploy` to dev on merge and to qa on approval |
| `docs` | | Specification, backlog, architecture decision records and runbooks |

The workbench (Control, E1) is added as its own epic starts.

## Working in this repository

- Git is the system of record. Every generated artifact and every approval lands here; the factory database never holds the only copy of anything.
- Nothing secret is committed. Secret values live in the client's secret manager and are read by the pipeline at run time; a secret scan runs on every pull request and fails the build if one slips in.
- PII columns are tagged, masked for non-privileged roles, and their reads are recorded. See [ADR 0008](docs/adr/0008-secrets-access-history-masking.md).
- `make check` at the root runs everything CI runs on a pull request, without accounts. Each component has its own `make check` too.
- A merge to main deploys to dev; qa follows once a reviewer approves. See [docs/runbooks/ci-cd-setup.md](docs/runbooks/ci-cd-setup.md).
