# ADR 0004: Environments from one root, proven by a parity check

Date: 2026-09-05
Status: Accepted
Story: S1.2.1 Four environments from IaC (E1, F1.2, WBS 2.1.4)

## Context

Dev, QA, UAT and prod must be created from the same infrastructure code with environment-specific variables only, so that parity is a fact rather than a hope. The story asks for two things: that the difference between environments is empty except for variables, and that a new environment can be created in under two hours.

ADR 0001 already chose one root module with a tfvars file per environment. This decision makes that property enforced and repeatable.

## Decision

1. **The environment is a name, not a branch in code.** `var.environment` may appear in names and tags only. Code that compares it (`var.environment == "prod"`, `contains([...], var.environment)`) is a parity violation and fails `scripts/check-env-parity.sh` in CI. Anything that must differ becomes a variable with a value in the tfvars file; optional features (resource monitor, Open Catalog) are gated by variables being set, not by which environment it is.

2. **Environment names are a pattern, not an enum.** Two to eight lower-case letters or digits. The standard four are dev, qa, uat and prod; a perf or dr environment is the same two files. The pattern is shared by Terraform, the Open Catalog tool and the verification scripts.

3. **Two files define an environment.** `environments/<env>.tfvars` holds values; `environments/backend-<env>.hcl` holds the state location, scoped to the environment by its key. `scripts/new-environment.sh` scaffolds both from a template environment and refuses to overwrite.

4. **The parity check is executable evidence.** It fails on environment-conditional code, environment-named code files, a missing standard environment, or tfvars and backend files that do not match. It then prints the unified diff of every tfvars file against dev. That diff is the complete difference between environments.

5. **Every tfvars file is planned against the standard inventory in CI.** The mock-provider test in `tests/environments` asserts the full set of objects for whichever tfvars file it is given: schemas, roles, warehouses, buckets, volume, landing pipeline.

6. **State storage is bootstrapped from code too.** `infra/terraform/bootstrap` creates the per-account state bucket with local state, using the same hardened bucket module, so nothing on the path to a new environment is manual except the two credentials from ADR 0001 and ADR 0002.

7. **The two-hour target is a timed runbook**, `docs/runbooks/new-environment.md`, with a budget per step. It is run for real when the next environment is created and the timing goes into the evidence pack.

## Consequences

- Adding an environment never touches a `.tf` file. If it seems to need one, the change belongs in a variable.
- Sizes, retention, credit quotas and Open Catalog settings are the whole legitimate difference between environments and are reviewable as a diff.
- Environments may share or split Snowflake and AWS accounts; the provider connection is per environment through the shell environment, and every object name carries the environment.
- The parity script is a shell script with grep and diff so it runs anywhere, including a developer laptop without Python.
