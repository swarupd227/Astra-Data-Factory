# ADR 0001: Snowflake foundation in Terraform

Date: 2026-09-05
Status: Accepted
Story: S1.1.1 Snowflake objects from Terraform (E1, F1.1, WBS 2.1.1)

## Context

Every Astra Data Factory environment needs the same Snowflake objects: a database with Bronze, Silver, Gold, Exceptions and Control schemas; roles for the personas in spec Section 3; and warehouses sized by processing tier. The story requires that an apply on an empty account creates everything with no manual step, that tier sizes are variables, and that a repeated apply produces no diff. Story S1.2.1 then requires four environments from the same code with only variables differing.

## Decision

1. **One root module at `infra/terraform/foundation`, one tfvars file per environment.** No Terraform workspaces and no per-environment directories. Environment parity is a property of the layout, not a review checklist item.

2. **Provider `snowflakedb/snowflake` 2.x with key-pair authentication from environment variables.** The provider reads organisation, account, user and private key from the environment. No connection detail or secret is stored in the repository.

3. **S3 remote state in the client account with native lock files.** State is partial-configured and completed per environment from `environments/backend-<env>.hcl`. The factory holds references only (spec Section 12).

4. **Access is expressed as data.** Each schema declares its readers, writers and creators; each warehouse tier declares its users. `locals.tf` flattens this into grant resources. A business analyst can read the access matrix in `variables.tf` without reading HCL logic.

5. **Six functional roles per environment**: ADMIN, ENGINEER, PIPELINE, STEWARD, CONSUMER, AUDITOR. Roles are granted to people and service users; privileges are never granted to users directly. All roles roll up to ADMIN and ADMIN rolls up to SYSADMIN.

6. **Future grants only.** The foundation creates no tables, so grants on existing objects would attach to nothing and drift as soon as generated code deploys. Future grants at schema level cover every object type the generation plane renders, including Iceberg tables, dynamic tables, tasks, pipes and data metric functions.

7. **Schemas use managed access** so that only the schema owner (the Terraform role) can grant object privileges. This keeps the Terraform access matrix authoritative.

8. **Idempotency is tested, not assumed.** Unit tests run against a mocked provider on every change. The live check `scripts/verify-idempotent.sh` applies and then requires a plan with exit code 0.

## Consequences

- Adding an environment is one tfvars file and one backend file.
- Changing who can read a schema is a change to a list in `variables.tf` or a tfvars file, reviewed like any other change.
- The resource monitor is optional because creating one requires ACCOUNTADMIN, which the steady-state Terraform role should not hold.
- Ephemeral sandboxes (S1.2.3) are deliberately outside this configuration; they are created at run time by the sandbox runner under the ADMIN role's `CREATE SCHEMA` privilege.
- Stages receive `USAGE` only on future grants. Internal stages needing `READ`/`WRITE` require an explicit grant when a pipeline introduces one.
