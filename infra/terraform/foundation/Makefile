# Snowflake foundation (S1.1.1)
#
#   make check            format check, validate, unit tests, tfvars checks (no account needed)
#   make test             unit tests only
#   make check-env ENV=qa prove one environment's tfvars file plans completely
#   make plan ENV=dev     plan against a real account
#   make apply ENV=dev    apply against a real account
#   make verify ENV=dev   apply, then prove a second plan is empty

ENV ?= dev
TF  ?= terraform

.PHONY: init init-local fmt fmt-check validate test check plan apply verify clean

init-local:
	$(TF) init -input=false -backend=false >/dev/null

init:
	$(TF) init -input=false -reconfigure -backend-config=environments/backend-$(ENV).hcl

fmt:
	$(TF) fmt -recursive

fmt-check:
	$(TF) fmt -check -recursive -diff

validate: init-local
	$(TF) validate

test: init-local
	$(TF) test
	cd modules/private-bucket && $(TF) init -input=false -backend=false >/dev/null && $(TF) test

# Proves that one environment's tfvars file produces a complete plan.
check-env: init-local
	$(TF) test -test-directory=tests/environments -var-file=environments/$(ENV).tfvars

check-envs: init-local
	@for f in environments/*.tfvars; do \
		e=$$(basename $$f .tfvars); \
		echo "== $$e"; \
		$(TF) test -test-directory=tests/environments -var-file=$$f || exit 1; \
	done

# Environments differ only by variables (S1.2.1).
parity:
	sh scripts/check-env-parity.sh

# Scaffold a new environment from a template: make new-env ENV=perf FROM=qa
FROM ?= qa
new-env:
	sh scripts/new-environment.sh $(ENV) $(FROM)

check: fmt-check validate test check-envs parity

plan: init
	$(TF) plan -input=false -var-file=environments/$(ENV).tfvars

apply: init
	$(TF) apply -input=false -var-file=environments/$(ENV).tfvars

verify:
	sh scripts/verify-idempotent.sh $(ENV)

clean:
	rm -rf .terraform
