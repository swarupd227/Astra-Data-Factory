# Runbook: set up CI/CD

What has to exist in GitHub and in the accounts for `.github/workflows/ci.yml` and `deploy.yml` to run (S1.2.2). Done once per repository; per environment afterwards.

## How the pipeline behaves

| Event | What runs | Outcome |
|---|---|---|
| Pull request opened or updated | `ci`: config validation, bundle check, unit tests for Terraform, astra-data and opencatalog, environment parity; optionally a dev plan | Problems appear as annotations on the changed files; the plan appears as a comment |
| Merge to main | `deploy`: apply foundation, deploy bundles, run generated tests against **dev** | Automatic |
| dev succeeded | The **qa** job waits for a reviewer on the qa environment, then does the same against qa | On approval |
| qa finished | Run summary shows each stage's duration and the time from merge to qa | Under 30 minutes is the target; a warning is raised if not |

## One-time setup

1. **Bootstrap the AWS account** (state bucket): `infra/terraform/bootstrap`, see its README.
2. **Create the deploy role in AWS** that GitHub assumes through OIDC. It needs the permissions the foundation uses (S3 buckets, IAM roles, bucket notifications) and read/write on the state bucket. Trust policy: the GitHub OIDC provider, limited to this repository and, for the qa role if you split them, the `qa` environment.
3. **Create the Snowflake service user** and grants from the foundation README.
4. **Create the GitHub environments** and the qa reviewer rule:

   ```bash
   scripts/configure-github-environments.sh --repo <owner>/<repo> --qa-reviewers <login1>,<login2>
   ```

5. **Set the environment variables** the script prints, for `dev` and for `qa`:

   | Name | Kind | Value |
   |---|---|---|
   | `AWS_ROLE_ARN` | variable | role from step 2 |
   | `AWS_REGION` | variable | region of the environment |
   | `TF_STATE_BUCKET` | variable | output of step 1 |
   | `SECRETS_PREFIX` | variable | Terraform output `secrets_prefix`, for example `astra/dev` |
   | `SNOWFLAKE_ORGANIZATION_NAME`, `SNOWFLAKE_ACCOUNT_NAME`, `SNOWFLAKE_USER` | variables | Snowflake account and service user |
   | `SNOWFLAKE_DEPLOY_ROLE`, `SNOWFLAKE_DEPLOY_WAREHOUSE` | variables | `ASTRA_<ENV>_ENGINEER`, `ASTRA_<ENV>_WH_SIMPLE` |

   **Secret values go in the client's secret manager, not in GitHub** (S1.2.5). The foundation creates the four secrets without values and a read policy; attach the policy (`read_secrets_policy_arn`) to the deploy role and put the values in:

   ```bash
   aws secretsmanager put-secret-value --secret-id astra/dev/snowflake/private-key      --secret-string file:///path/outside/repo/terraform_svc.p8
   aws secretsmanager put-secret-value --secret-id astra/dev/open-catalog/client-secret --secret-string '<from opencatalog provision>'
   aws secretsmanager put-secret-value --secret-id astra/dev/alerts/slack-webhook       --secret-string '<part after hooks.slack.com/services/>'
   aws secretsmanager put-secret-value --secret-id astra/dev/alerts/jira-token          --secret-string '<Jira API token>'
   ```

   The first apply of a brand-new environment runs before its secrets exist; run it from a workstation with the private key exported, as the foundation README describes, then switch the pipeline on. A repository that cannot reach Secrets Manager may instead set GitHub environment secrets `SNOWFLAKE_PRIVATE_KEY`, `OPEN_CATALOG_CLIENT_SECRET`, `SLACK_WEBHOOK_SECRET` and `JIRA_API_TOKEN` and leave `SECRETS_PREFIX` unset; the pipeline falls back to them.

6. **Protect main**: require the `ci` workflow checks and at least one review before merge, so nothing reaches `deploy` unchecked.
7. Optional: `gh variable set DEV_PLAN_ENABLED --body true` to post a Terraform plan for dev on every pull request.

## Adding uat and prod to the pipeline

Copy the `qa` job in `deploy.yml`, change `environment`, `needs` and the `with.environment` input, create the GitHub environment with its reviewers, and set the same variables and secrets. The infrastructure side needs nothing: S1.2.1 made every environment the same code.

## When a pull request fails validation

The `configs and release bundles` job fails and each problem is shown on the file and line in the Files changed tab, for example:

```
configs/pershing/pershing_position.yaml:31: rules[0].status: 'maybe' is not one of recovered, confirmed, rejected, legacy_defect
```

Fix the file and push; the check re-runs.
