# ADR 0035: The DQ runner collects DMF results, weighted by severity, into a score per entity; a breach reuses the alert table

Date: 2026-09-10
Status: Accepted
Story: S4.2.3 DQ runner and scores (E4, F4.2, WBS 2.4.6)

## Context

A source's dq_rules already become data metric functions, deployed and scheduled by the generation plane (S3.2.7, ADR 0025): one DMF or one system-function association per rule, each returning a number that is zero when the rule holds. Snowflake records every one of those measurements in `SNOWFLAKE.LOCAL.DATA_QUALITY_MONITORING_RESULTS`. Nothing so far turns those per-rule measurements into a single number a governance engineer can look at, per entity, per day, or tells anyone when one goes bad. Re-running the checks to compute that number would duplicate the DMFs; the runner's job is to read what they already measured.

## Decision

1. **The runner reads DMF results; it does not recompute them.** `astra-verify dq run` compiles a source's config for its dq_rules alone (no bundle is rendered or written), then for each rule queries `DATA_QUALITY_MONITORING_RESULTS` for the business date, filtered by the rule's table (resolved against the real target database, not the bundle's `{{ DATABASE }}` template) and its metric identity (`SNOWFLAKE.CORE.<system function>` or the custom DMF `astra_data.render.dq.dmf_name` already names) and its argument columns. A rule the DMF has not triggered for that day contributes no measurement and does not count against the score, rather than being scored as a failure; an entity with no measured rule at all is reported unscored, not zero — a stale DMF is a different condition (closer to `refresh_stale`, ADR 0007) than a rule that ran and found a problem.

2. **Entity is exactly what `astra_data.dq.CompiledDqRule.record` already means**: the physical detail record a record-level rule measures, or the Silver logical record a pair- or business-level rule measures. No new grouping concept is introduced; a rule already declares the entity it belongs to.

3. **The score is the severity-weighted share of a day's measurements that held**, not a row-weighted rate. Row counts are not uniformly available — every Bronze detail table has one from the automatic `ROW_COUNT` DMF, but a pair- or business-level rule measures the Silver table, which has none rendered today — while every rule's DMF already answers "did this measurement hold" uniformly, by design (0 = holds). Weighting by severity (`error` 4, `warning` 2, `info` 1) means one broken business rule can single-handedly breach the target the way a scattering of informational nits cannot; weighting by row count is a natural refinement once Silver-level row counts exist; it is not this story's scope to add them.

4. **The target is a platform default, not a number claimed from the spec.** The backlog names "Section 5.1 targets", but product-spec-v0.2.md's Section 5.1 describes how the planes connect, not a numeric quality bar — unlike parity's explicit ≥99.5% (ADR 0033), there is no MVP-wide DQ score number in the spec. `DQ_SCORE_TARGET = 0.98` is this story's default, overridden per run with `--target`; a client engagement supplies its own number the same way it supplies rule-level thresholds (the DQ Generator's own guardrail: thresholds from the client's targets, never invented). What is not invented is the ranking underneath it: a rule's own `severity`, already reviewed and approved when the rule was written.

5. **A breach reuses ADR 0007's alert table and dispatcher; nothing new is provisioned.** An entity below target raises one `dq_score_breach` alert into `CONTROL.ALERTS`, at the highest severity among its breached rules, deduplicated per business date by `SOURCE_KEY = dq:<custodian>:<entity>:<date>` — the same idempotent `INSERT ... WHERE NOT EXISTS` shape every other detector in `alerting.tf` already uses. `DISPATCH_ALERTS` picks it up and delivers it to Slack, Jira and email exactly as it does a failed task or a late custodian, with no new channel, integration or delivery path. `--no-alert` writes the report only, for a trial run.

## Consequences

- Only rules the source config declares (`dq_rules`) are scored — not the automatic sanity DMFs `render_dmfs` also attaches (row counts, required-field nulls, a single-column merge key's duplicates). Those are an unreviewed safety net; the rules a steward wrote and cited are the reviewed, governance-approved set this story scores against.
- A score is single-day, matching the story's own scope ("score per entity per day"); a trend across days, the way S4.2.2 built one for parity, is not part of this story.
- Because the score is a share of measurements, not of rows, a rule that fails once in a day's single trigger counts the same as one that fails on every row of a much larger trigger; a row-weighted refinement is future work once Silver row counts exist.
