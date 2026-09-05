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

5. **Set the environment variables and secrets** the script prints, for `dev` and for `qa`:

   | Name | Kind | Value |
   |---|---|---|
   | `AWS_ROLE_ARN` | variable | role from step 2 |
   | `AWS_REGION` | variable | region of the environment |
   | `TF_STATE_BUCKET` | variable | output of step 1 |
   | `SNOWFLAKE_ORGANIZATION_NAME`, `SNOWFLAKE_ACCOUNT_NAME`, `SNOWFLAKE_USER` | variables | Snowflake account and service user |
   | `SNOWFLAKE_DEPLOY_ROLE`, `SNOWFLAKE_DEPLOY_WAREHOUSE` | variables | `ASTRA_<ENV>_ENGINEER`, `ASTRA_<ENV>_WH_SIMPLE` |
   | `SNOWFLAKE_PRIVATE_KEY` | secret | PEM private key of the service user |
   | `OPEN_CATALOG_CLIENT_SECRET` | secret | only once Open Catalog is configured for the environment |

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
