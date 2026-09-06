# ADR 0008: Secrets, access history and masking baseline

Date: 2026-09-06
Status: Accepted
Story: S1.2.5 Secrets, access history and masking baseline (E1, F1.2, WBS 2.1.8)

## Context

A security reviewer needs three things from day one: no secret value in Git, logs or config, with secrets held in the client's secret manager; an answer to "who read which PII column and when" that is retained; and masking of tagged PII columns for roles that are not privileged.

## Decision

1. **Secrets live in the client's secret manager and in Snowflake secret objects; the repository holds references.** Terraform creates the four secrets the platform needs in AWS Secrets Manager (`<prefix>/<env>/snowflake/private-key`, `open-catalog/client-secret`, `alerts/slack-webhook`, `alerts/jira-token`) without values, plus an IAM policy that lets the deploy role read them. Values are put in by an operator or rotation, never by Terraform, so no value passes through state or a plan. Slack and Jira credentials become Snowflake secret objects; the integrations reference them by name.

2. **CI reads secrets from the secret manager through OIDC and masks them as it reads.** The deploy action and the dev plan resolve every secret from Secrets Manager under the environment's prefix and register each line with the log masker before use. GitHub secrets remain only as a fallback for a repository without Secrets Manager access. Terraform's own log is safe because every secret input is a sensitive variable.

3. **A secret in Git fails the build.** Gitleaks runs on every pull request and push over the full history, with an allow-list limited to test fixtures and documented placeholders. A pre-commit hook is provided for the same scan locally.

4. **Masking is tag-based and category-aware.** One `PII` tag in Control whose value is the category (`raw_record`, `name`, `account_number`, `tax_id`, `email`, `phone`, `address`, `date_of_birth`, `financial`). Three masking policies (string, number, date) are bound to the tag, so tagging a column is all a renderer has to do. Privileged roles (ADMIN, ENGINEER, PIPELINE, STEWARD by default, a variable) see values; every other role sees a mask shaped by the category: account numbers keep their last four characters, emails their domain, numbers become NULL, dates become the first day of their year, everything else five stars. AUDITOR and CONSUMER cannot be added to the unmasked list.

5. **Bronze raw lines are tagged from the start.** `BRONZE.RAW_LINES.LINE` carries raw custodian records with account numbers and names and is tagged `raw_record`, so an AUDITOR querying Bronze sees masks today, before any generated table exists. ENGINEER may apply the tag to the columns it creates.

6. **Access history is a view and a retained table.** `CONTROL.PII_ACCESS` joins column-level `ACCESS_HISTORY` to the current PII tag references and query history, giving user, role, time, object, column and category. A serverless task appends new rows hourly to `CONTROL.PII_ACCESS_LOG`, an Iceberg table in the client's bucket, so the record outlives Snowflake's own retention. Both are readable by AUDITOR.

7. **Verification is a command.** `astra-verify pii check` confirms the policies are bound and the column tagged, reads one raw line as a privileged and as a restricted role and expects the mask on the second, and queries the access view.

## Consequences

- The Terraform service role needs `IMPORTED PRIVILEGES` on the `SNOWFLAKE` database to create the access-history view; added to the bootstrap statements. `ACCESS_HISTORY` requires Enterprise Edition.
- Access history lags up to three hours; the retained log catches up over a seven-day window and never duplicates a read.
- Terraform state still contains the Slack and Jira values that Snowflake secrets are created from; state lives in a private, versioned, encrypted bucket per account and nowhere else. A future provider version with write-only secret arguments removes this.
- Snowflake redacts secret strings and integration credentials in its query history; the platform never echoes them and `astra-data` never prints connection parameters.
- Masking policies bound to a tag apply to every column tagged in any database of the account, including sandbox databases, so a dry-run's tagged columns are masked for restricted roles as well.
