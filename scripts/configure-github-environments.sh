#!/usr/bin/env sh
#
# Creates the GitHub environments the deploy pipeline uses (S1.2.2):
#
#   dev   no protection: every merge to main deploys here
#   qa    required reviewers: a deploy waits for one of them to approve
#
# and prints the variables and secrets each environment needs. Requires the
# GitHub CLI (gh) authenticated with admin rights on the repository.
#
# Usage:
#   scripts/configure-github-environments.sh --repo owner/name --qa-reviewers login1,login2
#
# Idempotent: environments are created or updated in place.

set -eu

repo=""
reviewers=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --qa-reviewers) reviewers="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$repo" ] || { echo "error: --repo owner/name is required" >&2; exit 64; }
[ -n "$reviewers" ] || { echo "error: --qa-reviewers login1,login2 is required" >&2; exit 64; }
command -v gh >/dev/null 2>&1 || { echo "error: the GitHub CLI (gh) is not installed" >&2; exit 69; }

echo "== dev: no protection"
gh api --method PUT "repos/$repo/environments/dev" \
  --input - <<'EOF' >/dev/null
{ "deployment_branch_policy": { "protected_branches": true, "custom_branch_policies": false } }
EOF
echo "ok"

echo "== qa: required reviewers $reviewers"
ids=""
for login in $(printf '%s' "$reviewers" | tr ',' ' '); do
  id=$(gh api "users/$login" --jq .id)
  ids="$ids${ids:+,}{\"type\":\"User\",\"id\":$id}"
done
gh api --method PUT "repos/$repo/environments/qa" \
  --input - <<EOF >/dev/null
{ "reviewers": [$ids], "deployment_branch_policy": { "protected_branches": true, "custom_branch_policies": false } }
EOF
echo "ok"

cat <<EOF

Now set, per environment (dev and qa), the connection details the deploy
action reads. Values differ per environment; names do not.

  gh variable set AWS_ROLE_ARN                 --env dev --repo $repo --body arn:aws:iam::<account>:role/<github-deploy-role>
  gh variable set AWS_REGION                   --env dev --repo $repo --body us-east-1
  gh variable set TF_STATE_BUCKET              --env dev --repo $repo --body <output of infra/terraform/bootstrap>
  gh variable set SNOWFLAKE_ORGANIZATION_NAME  --env dev --repo $repo --body <org>
  gh variable set SNOWFLAKE_ACCOUNT_NAME       --env dev --repo $repo --body <account>
  gh variable set SNOWFLAKE_USER               --env dev --repo $repo --body TERRAFORM_SVC
  gh variable set SNOWFLAKE_DEPLOY_ROLE        --env dev --repo $repo --body ASTRA_DEV_ENGINEER
  gh variable set SNOWFLAKE_DEPLOY_WAREHOUSE   --env dev --repo $repo --body ASTRA_DEV_WH_SIMPLE
  gh variable set SECRETS_PREFIX               --env dev --repo $repo --body astra/dev   # Terraform output secrets_prefix

Secret values belong in the client's secret manager, read by the deploy role
(see docs/runbooks/ci-cd-setup.md). Only a repository without Secrets Manager
access sets them as GitHub secrets instead:

  gh secret set SNOWFLAKE_PRIVATE_KEY --env dev --repo $repo < /path/outside/repo/terraform_svc.p8
  gh secret set OPEN_CATALOG_CLIENT_SECRET / SLACK_WEBHOOK_SECRET / JIRA_API_TOKEN --env dev --repo $repo

Repeat with --env qa. To post a dev plan on every pull request:

  gh variable set DEV_PLAN_ENABLED --repo $repo --body true
EOF
