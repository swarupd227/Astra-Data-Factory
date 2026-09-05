# ADR 0005: CI/CD for configs, infrastructure and release bundles

Date: 2026-09-05
Status: Accepted
Story: S1.2.2 CI/CD for config and generated code (E1, F1.2, WBS 2.1.5)

## Context

A merged pull request must validate config, run generated tests and deploy to the target environment without manual steps. An invalid config must fail the pull request with the message visible on it. A passing merge deploys to dev automatically and to QA on approval, within 30 minutes.

The config compiler and the renderers that produce release bundles belong to E3 and do not exist yet. The pipeline has to be complete now and stay unchanged when they arrive.

## Decision

1. **A versioned config schema starts now, at version 0.** `configs/**/*.yaml` are validated on every pull request against `config-v0.schema.json` plus the reference rules the schema cannot express: unique ids, mappings that name rules which exist and are not rejected, file name equal to source id, real calendar dates. Version 0 fixes identity, ownership and the shape of rules; the compiler (S3.1.1) extends the schema as version 1 and keeps this contract.

2. **Problems are reported on the pull request itself.** The validator prints GitHub workflow annotations with file, line and a plain sentence, so the person who opened the pull request sees the problem on the line that caused it. Line numbers come from a YAML loader that keeps positions and refuses duplicate keys rather than silently keeping the last one.

3. **Release bundles have a deploy contract, version 0.** A bundle is a directory under `releases/` with a manifest naming ordered SQL steps and SQL tests. SQL is environment-neutral through `{{ DATABASE }}`-style placeholders filled at deploy time from the environment name and prefix, so one bundle moves through every environment unchanged. A test returns failing rows; zero rows is a pass. The renderer (S3.1.2) targets this contract.

4. **Deploy renders everything before running anything.** An unknown placeholder or a missing file fails before a connection is opened; a failing step stops the bundle and names the step and the database's error.

5. **One composite action deploys an environment**: Terraform apply of the foundation, then bundle steps, then generated tests. `deploy.yml` runs it for dev on every push to main and for qa after dev, with qa gated by a GitHub environment protection rule (required reviewers). Promotion changes the target; the procedure is the same file.

6. **The 30-minute target is enforced on machine time and reported on wall-clock time.** Each deploy job is capped at 12 minutes. The qa job writes the time from the merge commit to deployment into the run summary and warns when it exceeds 30 minutes; the approval wait is included in that number and belongs to the reviewer, not the pipeline.

7. **Credentials are GitHub environment variables and secrets, never files.** AWS through OIDC, Snowflake through key-pair authentication, the same names the Terraform provider and `astra-data` read. `scripts/configure-github-environments.sh` creates the environments and the qa reviewer rule and prints the settings to fill.

8. **A dev plan on every pull request is optional.** When the repository variable `DEV_PLAN_ENABLED` is true, the pull request gets a Terraform plan for dev as a comment and in the job summary.

## Consequences

- Until E3 renders bundles, the deploy stage applies the foundation and reports "no release bundles"; the pipeline is exercised end to end from day one.
- Configs are validated on shape and references only. Semantic validation (unknown pattern, unknown field in the spec) arrives with the compiler and reports through the same annotation path.
- The bundle contract is a commitment: renderers emit `manifest.yaml`, placeholders, idempotent SQL and row-returning tests.
- Branch protection on main should require the `ci` workflow, so nothing reaches `deploy.yml` without passing it.
- UAT and prod are added to `deploy.yml` as further gated jobs when their promotion policy is agreed (S1.2.1 made their infrastructure identical already).
