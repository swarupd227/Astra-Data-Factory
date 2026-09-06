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

## Turning off the catch-all pipe

A fresh environment loads every landed file into `BRONZE.RAW_LINES` through the foundation's catch-all pipe, which is how the landing zone is proved before any source exists. Once the environment's sources are rendered (`astra-data render`) each source has its own pipe, `BRONZE.<SOURCE>_PIPE`, watching its custodian's folder, and a file that matches both pipes is loaded twice. The order is:

1. Deploy the release bundles (the deploy pipeline does this on merge), so every source's pipe exists.
2. Set `landing_catch_all_pipe = false` in the environment's tfvars and apply the foundation. The catch-all pipe object stays, pointed at a folder nothing is delivered to, because the bucket notification targets its queue.
3. Watch `CONTROL.FILE_LOAD_LOG`: a file that arrives and is logged `UNCLAIMED` after `landing_unclaimed_minutes` matches no source's delivery patterns and needs a config.

Changing a source's folder or delivery patterns does not change its pipe (a pipe's COPY cannot be altered and recreating it loses in-flight events and load history): drop `BRONZE.<SOURCE>_PIPE` in a quiet window and redeploy the bundle.
