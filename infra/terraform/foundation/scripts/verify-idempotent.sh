#!/usr/bin/env sh
#
# Live acceptance check for S1.1.1 against a real Snowflake account.
#
#   1. terraform apply creates every object with no manual step.
#   2. A second plan on the unchanged configuration reports no changes.
#
# Usage:
#   scripts/verify-idempotent.sh <dev|qa|uat|prod>
#
# Requires the Snowflake connection variables described in providers.tf and
# credentials for the S3 state bucket named in environments/backend-<env>.hcl.
# Exit code is 0 only when both steps succeed.

set -eu

ENV="${1:-}"
case "$ENV" in
  dev|qa|uat|prod) ;;
  *) echo "usage: $0 <dev|qa|uat|prod>" >&2; exit 64 ;;
esac

cd "$(dirname "$0")/.."

echo "== init ($ENV)"
terraform init -input=false -reconfigure -backend-config="environments/backend-${ENV}.hcl" >/dev/null

echo "== apply ($ENV)"
terraform apply -input=false -auto-approve -var-file="environments/${ENV}.tfvars"

echo "== plan again ($ENV): expecting no changes"
set +e
terraform plan -input=false -detailed-exitcode -var-file="environments/${ENV}.tfvars" -lock=false
rc=$?
set -e

case "$rc" in
  0) echo "PASS: second plan reports no changes for $ENV." ;;
  2) echo "FAIL: second plan reports changes for $ENV. The configuration is not idempotent." >&2; exit 1 ;;
  *) echo "FAIL: terraform plan exited with status $rc." >&2; exit "$rc" ;;
esac
