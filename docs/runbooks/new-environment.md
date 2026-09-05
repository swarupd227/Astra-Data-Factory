# Runbook: stand up a new environment

Story S1.2.1 requires that a new environment can be created in under two hours. This runbook is the timed path. Record the start and end time in the evidence pack when it is run for real.

Budget: 100 minutes of work, 20 minutes of slack.

| Step | Who | Budget | Done when |
|---|---|---|---|
| 1. Prerequisites | DevOps | 15 min | The AWS account has a state bucket (`infra/terraform/bootstrap`, once per account) and the Snowflake account has the Terraform service user and grants from the foundation README |
| 2. Scaffold | DevOps | 5 min | `scripts/new-environment.sh <env> [template]` created the tfvars and backend files; sizes and retention reviewed; bucket name in the backend file set |
| 3. Local checks | DevOps | 5 min | `make check` and `sh scripts/check-env-parity.sh` pass |
| 4. First apply | DevOps | 15 min | `make apply ENV=<env>` completes: database, schemas, roles, warehouses, buckets, IAM roles, external volume, landing pipeline |
| 5. Idempotency | DevOps | 5 min | `make verify ENV=<env>` reports no changes on the second plan |
| 6. Open Catalog | DevOps | 15 min | `opencatalog provision --environment <env> ...` created the catalog and principals; credentials stored in the secret manager; `open_catalog` set in the tfvars file |
| 7. Second apply | DevOps | 10 min | `make apply ENV=<env>` created the Open Catalog role, catalog integration and `CATALOG_SYNC` |
| 8. Acceptance | DevOps | 20 min | `opencatalog verify` and `scripts/verify_snowpipe.py` pass; results attached to the evidence pack |
| 9. Hand-over | DevOps | 10 min | Role grants to people and service users issued; pull request with the two new files merged |

## Notes

- Everything above is variables and commands. If a step needs a code change, stop: the environment is trying to differ from the others in a way the parity rule forbids.
- Step 6 and 7 are skipped when the environment does not need Open Catalog (for example a short-lived performance environment). Set nothing under `open_catalog` and the objects are simply not created.
- Steps 4 and 7 are where time varies: Snowflake and AWS object creation is usually under five minutes each; the budget allows for retries.
- Tearing an environment down is `terraform destroy` with the same tfvars file. The landing and Iceberg buckets must be emptied first; Terraform will not delete a bucket with objects.
