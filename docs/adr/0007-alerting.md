# ADR 0007: Alerts to Slack, Jira and email from inside Snowflake

Date: 2026-09-06
Status: Accepted
Story: S1.2.4 Alerts to Slack, Jira and email (E1, F1.2, WBS 2.1.7)

## Context

Operations must learn about pipeline failures and late custodians on the channels they already use, within five minutes, without watching a dashboard. A late alert must list the missing files. Severity must be configurable per custodian.

## Decision

1. **Detection and dispatch run inside Snowflake, serverless, every minute.** A task calls `CONTROL.RUN_ALERTING`, which detects failed tasks from `INFORMATION_SCHEMA.TASK_HISTORY`, detects late custodians from `CONTROL.CUSTODIANS` and `CUSTODIAN_FILES` against `FILE_LOAD_LOG`, and dispatches undelivered alerts. No runner, queue or external service has to be alive, and the five-minute bound holds by construction: one-minute cadence, at most a few seconds of work.

2. **Delivery goes through Snowflake notification integrations**: an email integration with an allow-list of recipients, and webhook integrations for Slack (incoming webhook) and Jira (issue REST API with basic authentication). Webhook secrets are Snowflake secret objects created from sensitive Terraform variables; nothing secret is in Git or in the integration definition.

3. **Routing is data.** `CONTROL.ALERT_ROUTES` maps severity to channel and integration (and recipients for email). Terraform seeds it from the channels configured for the environment (critical and error to all three; warning to Slack and email; info to Slack) and never overwrites an operator's later change.

4. **Severity, cutoff and expected files per custodian come from the source configs.** The config schema gains an optional `delivery` block (cutoff time, timezone, business days, expected file patterns) and an `alerts` block (late and task-failure severity). `astra-data custodians sync` folds every config into one row per custodian, refuses configs of the same custodian that disagree, and brings `CONTROL.CUSTODIANS` and `CUSTODIAN_FILES` in line in one transaction. The deploy pipeline runs it after the bundles, so what alerting acts on is what was reviewed in Git.

5. **A failed task is attributed to a custodian by name prefix**: a task named `<CUSTODIAN_ID>_...` takes that custodian's task-failure severity; any other task is a platform task at severity `error`. Per-custodian task DAGs rendered by S3.2.6 follow this convention.

6. **Every alert and every delivery attempt is recorded.** `CONTROL.ALERTS` holds one row per alert with a deduplication key (task query id, or custodian and business date for late alerts), so a failure is alerted once and a custodian is alerted once per business day. `CONTROL.ALERT_DELIVERIES` holds every attempt per channel with the outcome; delivery is retried on later runs up to a bounded number of attempts.

7. **Late means: cutoff passed in the custodian's timezone on one of its business days, and at least one expected file pattern has no arrival for that business date** in the file load log. The alert names the custodian, the business date, the cutoff in UTC, the count and the list of missing patterns.

## Consequences

- The alerting task cannot alert on its own failure. The task suspends after repeated failures, which the environment's monitoring should watch; Snowflake's own task error notifications are the fallback.
- Custodians removed from configs are disabled, not deleted, so past alerts keep their referent.
- A custodian with `alerts` but no `delivery` block has a task-failure severity and never goes late.
- Jira issues are created with the environment's project key and issue type; deduplication is per alert, so a recurring failure creates one issue per occurrence, which is the behaviour operations asked for in the backlog (one alert per failed run).
- The Slack and Jira integrations are created with `snowflake_execute` because the provider has no webhook integration resource; the email integration resource is a provider preview feature and is enabled explicitly.
- Timezone names are checked against the IANA database; `tzdata` is a dependency so the check behaves the same on every platform.
