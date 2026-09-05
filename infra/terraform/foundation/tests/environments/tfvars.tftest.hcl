# Checks that an environment's tfvars file produces a complete plan.
# Kept in its own directory because it takes its variables from the file
# under test rather than from the defaults:
#
#   terraform test -test-directory=tests/environments -var-file=environments/prod.tfvars
#
# `make check-env ENV=prod` runs this for one environment; `make check` runs
# it for all four.

mock_provider "snowflake" {}

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

run "environment_file_plans_completely" {
  command = plan

  assert {
    condition     = contains(["dev", "qa", "uat", "prod"], var.environment)
    error_message = "The tfvars file must set environment to dev, qa, uat or prod."
  }

  assert {
    condition     = snowflake_database.this.name == "${var.prefix}_${upper(var.environment)}"
    error_message = "Database name must be derived from prefix and environment."
  }

  assert {
    condition     = alltrue([for t in ["simple", "medium", "complex"] : contains(keys(snowflake_warehouse.tier), t)])
    error_message = "The environment must define the simple, medium and complex warehouses."
  }

  assert {
    condition     = length(snowflake_schema.this) >= 5 && length(snowflake_account_role.this) == 6
    error_message = "The environment must define the default schemas and six functional roles."
  }
}
