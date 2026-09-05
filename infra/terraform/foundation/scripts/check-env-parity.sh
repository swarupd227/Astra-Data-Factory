#!/usr/bin/env sh
#
# Environment parity check (S1.2.1).
#
# Every environment is created from the same code. The only things allowed
# to differ are the values in environments/<env>.tfvars and the state
# location in environments/backend-<env>.hcl. This script fails when:
#
#   - any .tf file branches on the environment name (that would be an
#     environment-specific object definition hiding in code)
#   - any code file is named after an environment
#   - one of the four standard environments is missing
#   - an environment has a tfvars file without a backend file, or vice versa
#   - a tfvars file does not set `environment` to its own name
#   - a backend file's state key is not scoped to its environment
#
# It then prints the complete difference between environments: a unified
# diff of each tfvars file against dev. That diff is, by construction, the
# whole difference between the environments.
#
# Usage: scripts/check-env-parity.sh    (from infra/terraform/foundation)

set -u

cd "$(dirname "$0")/.."
status=0

fail() {
  echo "FAIL: $*" >&2
  status=1
}

echo "== environment-conditional logic in code"
# Comments are stripped before matching so a remark about the rule cannot trip it.
if grep -nE 'var\.environment[[:space:]]*(==|!=)|contains\([^)]*var\.environment' -- *.tf modules/*/*.tf 2>/dev/null | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#'; then
  fail "code branches on the environment name; move the difference into a variable"
else
  echo "none"
fi

echo "== environment-specific code files"
found=0
for f in *.tf modules/*/*.tf; do
  case "$(basename "$f")" in
    *dev*|*qa*|*uat*|*prod*) fail "code file named after an environment: $f"; found=1 ;;
  esac
done
[ "$found" -eq 0 ] && echo "none"

echo "== standard environments"
for env in dev qa uat prod; do
  [ -f "environments/$env.tfvars" ] || fail "missing environments/$env.tfvars"
done

echo "== every environment has tfvars, backend, matching name and scoped state key"
for tfvars in environments/*.tfvars; do
  env=$(basename "$tfvars" .tfvars)
  backend="environments/backend-$env.hcl"
  [ -f "$backend" ] || fail "$env has no $backend"
  grep -Eq "^[[:space:]]*environment[[:space:]]*=[[:space:]]*\"$env\"" "$tfvars" || fail "$tfvars does not set environment = \"$env\""
  if [ -f "$backend" ]; then
    grep -Eq "^[[:space:]]*key[[:space:]]*=.*/$env/" "$backend" || fail "$backend state key is not scoped to /$env/"
  fi
done
for backend in environments/backend-*.hcl; do
  env=$(basename "$backend" .hcl | sed 's/^backend-//')
  [ -f "environments/$env.tfvars" ] || fail "$backend has no environments/$env.tfvars"
done
[ "$status" -eq 0 ] && echo "ok"

echo "== difference between environments (tfvars against dev)"
for tfvars in environments/*.tfvars; do
  env=$(basename "$tfvars" .tfvars)
  [ "$env" = "dev" ] && continue
  echo "--- dev -> $env"
  diff -u environments/dev.tfvars "$tfvars" | sed -n '3,$p' || true
done

if [ "$status" -eq 0 ]; then
  echo "PASS: environments differ only by variables."
else
  echo "FAIL: see messages above." >&2
fi
exit "$status"
