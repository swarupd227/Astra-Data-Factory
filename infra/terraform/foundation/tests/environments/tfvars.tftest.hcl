# Checks that an environment's tfvars file produces the complete, standard
# inventory of objects. Kept in its own directory because it takes its
# variables from the file under test rather than from the defaults:
#
#   terraform test -test-directory=tests/environments -var-file=environments/prod.tfvars
#
# `make check-env ENV=prod` runs this for one environment; `make check` runs
# it for every environments/*.tfvars. Together with scripts/check-env-parity.sh
# this is the proof behind S1.2.1: every environment is the same set of
# objects, named by the environment, differing only in variable values.

mock_provider "snowflake" {}

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

run "environment_file_plans_the_standard_inventory" {
  command = plan

  assert {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "The tfvars file must set a valid environment name."
  }

  assert {
    condition     = snowflake_database.this.name == "${var.prefix}_${upper(var.environment)}"
    error_message = "Database name must be derived from prefix and environment."
  }

  # Snowflake foundation (S1.1.1)
  assert {
    condition     = contains(keys(snowflake_schema.this), "BRONZE") && contains(keys(snowflake_schema.this), "SILVER") && contains(keys(snowflake_schema.this), "GOLD") && contains(keys(snowflake_schema.this), "EXCEPTIONS") && contains(keys(snowflake_schema.this), "CONTROL")
    error_message = "The environment must define the five standard schemas."
  }

  assert {
    condition     = length(snowflake_account_role.this) == 6
    error_message = "The environment must define the six functional roles."
  }

  assert {
    condition     = alltrue([for t in ["simple", "medium", "complex"] : contains(keys(snowflake_warehouse.tier), t)])
    error_message = "The environment must define the simple, medium and complex warehouses."
  }

  assert {
    condition     = alltrue([for w in snowflake_warehouse.tier : startswith(w.name, "${var.prefix}_${upper(var.environment)}_WH_")])
    error_message = "Every warehouse is named by the environment."
  }

  # Iceberg storage (S1.1.2)
  assert {
    condition     = snowflake_external_volume.iceberg.name == "${var.prefix}_${upper(var.environment)}_ICEBERG" && aws_iam_role.snowflake_iceberg.name == "${var.prefix}_${upper(var.environment)}_SNOWFLAKE_ICEBERG"
    error_message = "The environment must have its own Iceberg volume and Snowflake role."
  }

  assert {
    condition     = strcontains(module.iceberg_bucket.name, "-${var.environment}-iceberg-") && strcontains(module.landing_bucket.name, "-${var.environment}-landing-")
    error_message = "The environment must have its own Iceberg and landing buckets."
  }

  # Landing zone (S1.1.3)
  assert {
    condition = (
      snowflake_storage_integration_aws.landing.name == "${var.prefix}_${upper(var.environment)}_LANDING" &&
      snowflake_stage_external_s3.landing.name == "LANDING" &&
      snowflake_iceberg_table.raw_lines.name == "RAW_LINES" &&
      snowflake_pipe.raw_lines.auto_ingest &&
      snowflake_iceberg_table.file_load_log.name == "FILE_LOAD_LOG" &&
      snowflake_task.reconcile_file_loads.started
    )
    error_message = "The environment must have the complete landing pipeline."
  }

  assert {
    condition     = length(aws_s3_bucket_notification.landing.queue) == 1
    error_message = "The landing bucket must notify Snowpipe."
  }
}
