#!/usr/bin/env sh
#
# Scaffolds a new environment (S1.2.1).
#
# A new environment is two files: environments/<env>.tfvars and
# environments/backend-<env>.hcl. Both are copied from a template environment
# with the environment name and state key substituted. No code changes.
#
# Usage: scripts/new-environment.sh <env> [template-env]
#   env           2 to 8 lower-case letters or digits, starting with a letter
#   template-env  existing environment to copy values from (default: qa)
#
# Afterwards follow docs/runbooks/new-environment.md.

set -eu

cd "$(dirname "$0")/.."

env="${1:-}"
template="${2:-qa}"

case "$env" in
  "") echo "usage: $0 <env> [template-env]" >&2; exit 64 ;;
esac
if ! printf '%s' "$env" | grep -Eq '^[a-z][a-z0-9]{1,7}$'; then
  echo "error: environment name must be 2 to 8 lower-case letters or digits, starting with a letter" >&2
  exit 64
fi
[ -f "environments/$template.tfvars" ] || { echo "error: template environment '$template' has no environments/$template.tfvars" >&2; exit 66; }
[ -f "environments/backend-$template.hcl" ] || { echo "error: template environment '$template' has no environments/backend-$template.hcl" >&2; exit 66; }
[ ! -e "environments/$env.tfvars" ] || { echo "error: environments/$env.tfvars already exists" >&2; exit 73; }
[ ! -e "environments/backend-$env.hcl" ] || { echo "error: environments/backend-$env.hcl already exists" >&2; exit 73; }

{
  echo "# $env environment, scaffolded from $template on $(date -u +%Y-%m-%d). Only values"
  echo "# that differ between environments live in this file; object definitions are"
  echo "# shared (S1.2.1)."
  echo
  # Drop the template's leading comment block and blank lines, then substitute the name.
  awk 'started || (!/^#/ && !/^[[:space:]]*$/) { started = 1; print }' "environments/$template.tfvars" \
    | sed -E "s/^([[:space:]]*environment[[:space:]]*=[[:space:]]*)\"$template\"/\1\"$env\"/"
} > "environments/$env.tfvars"

sed -E "s#(/)$template(/)#\1$env\2#g; s/\b$template\b environment/$env environment/" "environments/backend-$template.hcl" > "environments/backend-$env.hcl"

echo "created environments/$env.tfvars (from $template)"
echo "created environments/backend-$env.hcl"
echo
echo "next:"
echo "  1. review both files; set warehouse sizes and retention for $env"
echo "  2. make check                      # unit tests, including the new tfvars file"
echo "  3. sh scripts/check-env-parity.sh  # confirms the environment differs only by variables"
echo "  4. follow docs/runbooks/new-environment.md to stand it up"
