# Unit tests for the Snowflake foundation (S1.1.1).
#
# These run against a mocked provider, so they need no Snowflake account and
# no credentials. They prove that the configuration produces the objects the
# story asks for and that every name and size is derived from variables.
# The live acceptance run (apply on an empty account, then a no-change plan)
# is scripts/verify-idempotent.sh.

mock_provider "snowflake" {}
mock_provider "aws" {}

variables {
  environment = "dev"
}

# ---------------------------------------------------------------------------
# Objects created from defaults
# ---------------------------------------------------------------------------

run "creates_every_object_from_defaults" {
  command = plan

  assert {
    condition     = snowflake_database.this.name == "ASTRA_DEV"
    error_message = "Database must be named <PREFIX>_<ENV>."
  }

  assert {
    condition     = toset(keys(snowflake_schema.this)) == toset(["BRONZE", "SILVER", "GOLD", "EXCEPTIONS", "CONTROL", "REFERENCE", "ARCHIVE"])
    error_message = "Default schemas must be BRONZE, SILVER, GOLD, EXCEPTIONS, CONTROL, REFERENCE and ARCHIVE."
  }

  assert {
    condition     = alltrue([for s in snowflake_schema.this : s.database == "ASTRA_DEV" && s.with_managed_access == "true"])
    error_message = "Every schema must live in the environment database with managed access."
  }

  assert {
    condition     = toset(keys(snowflake_warehouse.tier)) == toset(["simple", "medium", "complex"])
    error_message = "One warehouse per tier: simple, medium, complex."
  }

  assert {
    condition = (
      snowflake_warehouse.tier["simple"].name == "ASTRA_DEV_WH_SIMPLE" &&
      snowflake_warehouse.tier["medium"].name == "ASTRA_DEV_WH_MEDIUM" &&
      snowflake_warehouse.tier["complex"].name == "ASTRA_DEV_WH_COMPLEX"
    )
    error_message = "Warehouses must be named <PREFIX>_<ENV>_WH_<TIER>."
  }

  assert {
    condition = (
      snowflake_warehouse.tier["simple"].warehouse_size == "XSMALL" &&
      snowflake_warehouse.tier["medium"].warehouse_size == "SMALL" &&
      snowflake_warehouse.tier["complex"].warehouse_size == "MEDIUM"
    )
    error_message = "Default tier sizes must be XSMALL, SMALL and MEDIUM."
  }

  assert {
    condition = alltrue([
      for w in snowflake_warehouse.tier :
      w.initially_suspended && w.auto_resume == "true" && w.auto_suspend == 60 && w.min_cluster_count == 1
    ])
    error_message = "Warehouses must start suspended, auto-resume, and auto-suspend after 60 seconds."
  }

  assert {
    condition = toset([for r in snowflake_account_role.this : r.name]) == toset([
      "ASTRA_DEV_ADMIN", "ASTRA_DEV_ENGINEER", "ASTRA_DEV_PIPELINE",
      "ASTRA_DEV_STEWARD", "ASTRA_DEV_CONSUMER", "ASTRA_DEV_AUDITOR", "ASTRA_DEV_SANDBOX",
    ])
    error_message = "Seven functional roles must be created with the environment prefix."
  }

  assert {
    condition     = length(snowflake_resource_monitor.this) == 0
    error_message = "No resource monitor is created unless a credit quota is set."
  }

  assert {
    condition     = alltrue([for w in snowflake_warehouse.tier : w.resource_monitor == null])
    error_message = "Warehouses must not reference a resource monitor when none is configured."
  }
}

# ---------------------------------------------------------------------------
# Names and sizes are driven by variables
# ---------------------------------------------------------------------------

run "warehouse_sizes_come_from_tier_variables" {
  command = plan

  variables {
    warehouse_tiers = {
      simple  = { size = "SMALL" }
      medium  = { size = "LARGE", auto_suspend_seconds = 300 }
      complex = { size = "XLARGE", max_cluster_count = 3, statement_timeout_seconds = 7200 }
    }
  }

  assert {
    condition = (
      snowflake_warehouse.tier["simple"].warehouse_size == "SMALL" &&
      snowflake_warehouse.tier["medium"].warehouse_size == "LARGE" &&
      snowflake_warehouse.tier["complex"].warehouse_size == "XLARGE"
    )
    error_message = "Warehouse size must follow the tier variable."
  }

  assert {
    condition = (
      snowflake_warehouse.tier["medium"].auto_suspend == 300 &&
      snowflake_warehouse.tier["complex"].max_cluster_count == 3 &&
      snowflake_warehouse.tier["complex"].statement_timeout_in_seconds == 7200
    )
    error_message = "Auto-suspend, cluster count and statement timeout must follow the tier variable."
  }
}

run "extra_tiers_are_allowed" {
  command = plan

  variables {
    warehouse_tiers = {
      simple  = { size = "XSMALL" }
      medium  = { size = "SMALL" }
      complex = { size = "MEDIUM" }
      replay  = { size = "LARGE", users = ["ENGINEER"] }
    }
  }

  assert {
    condition     = snowflake_warehouse.tier["replay"].name == "ASTRA_DEV_WH_REPLAY"
    error_message = "Additional tiers must be created with the same naming rule."
  }

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.warehouse : split(".", k)[1] if split(".", k)[0] == "replay"]) == toset(["ENGINEER", "ADMIN", "AUDITOR"])
    error_message = "An extra tier grants its listed users plus ADMIN and AUDITOR."
  }
}

run "environment_is_part_of_every_name" {
  command = plan

  variables {
    environment = "prod"
  }

  assert {
    condition     = snowflake_database.this.name == "ASTRA_PROD"
    error_message = "Database name must carry the environment."
  }

  assert {
    condition     = alltrue([for w in snowflake_warehouse.tier : startswith(w.name, "ASTRA_PROD_WH_")])
    error_message = "Warehouse names must carry the environment."
  }

  assert {
    condition     = alltrue([for r in snowflake_account_role.this : startswith(r.name, "ASTRA_PROD_")])
    error_message = "Role names must carry the environment."
  }
}

run "prefix_is_configurable" {
  command = plan

  variables {
    prefix = "ENV"
  }

  assert {
    condition     = snowflake_database.this.name == "ENV_DEV" && snowflake_account_role.this["ADMIN"].name == "ENV_DEV_ADMIN"
    error_message = "Every object name must use the configured prefix."
  }
}

# ---------------------------------------------------------------------------
# Access matrix
# ---------------------------------------------------------------------------

run "grants_follow_the_schema_access_matrix" {
  command = plan

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.database_usage)) == toset(["ADMIN", "ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR", "SANDBOX"])
    error_message = "Every functional role gets USAGE on the database."
  }

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.future_objects : split(".", k)[0] if split(".", k)[1] == "CONSUMER"]) == toset(["GOLD", "CONTROL"])
    error_message = "CONSUMER reads GOLD and CONTROL only."
  }

  assert {
    condition = alltrue([
      for k, g in snowflake_grant_privileges_to_account_role.future_objects :
      g.privileges == toset(["SELECT"]) if split(".", k)[1] == "CONSUMER" || split(".", k)[1] == "AUDITOR"
    ])
    error_message = "CONSUMER and AUDITOR must hold SELECT only on future objects."
  }

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.future_objects : split(".", k)[0] if split(".", k)[1] == "AUDITOR"]) == toset(["BRONZE", "SILVER", "GOLD", "EXCEPTIONS", "CONTROL", "REFERENCE", "ARCHIVE"])
    error_message = "AUDITOR reads every schema."
  }

  assert {
    condition = alltrue([
      for s in ["BRONZE", "SILVER", "GOLD", "EXCEPTIONS", "CONTROL", "REFERENCE", "ARCHIVE"] :
      contains(keys(snowflake_grant_privileges_to_account_role.future_objects), "${s}.PIPELINE.TABLES") &&
      contains(keys(snowflake_grant_privileges_to_account_role.future_objects), "${s}.ENGINEER.TABLES")
    ])
    error_message = "PIPELINE and ENGINEER write tables in every schema."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.future_objects["SILVER.PIPELINE.TABLES"].privileges == toset(["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"])
    error_message = "Writers hold SELECT, INSERT, UPDATE, DELETE and TRUNCATE on future tables."
  }

  assert {
    condition     = contains(keys(snowflake_grant_privileges_to_account_role.future_objects), "EXCEPTIONS.STEWARD.TABLES") && snowflake_grant_privileges_to_account_role.future_objects["EXCEPTIONS.STEWARD.TABLES"].privileges == toset(["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"])
    error_message = "STEWARD writes the exception store."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.future_objects["SILVER.STEWARD.TABLES"].privileges == toset(["SELECT"])
    error_message = "STEWARD reads Silver only."
  }

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.schema_create : split(".", k)[1]]) == toset(["ENGINEER"]) && length(snowflake_grant_privileges_to_account_role.schema_create) == 7
    error_message = "Only ENGINEER creates objects, and it can do so in every schema."
  }

  assert {
    condition     = contains(snowflake_grant_privileges_to_account_role.schema_create["GOLD.ENGINEER"].privileges, "CREATE DYNAMIC TABLE") && contains(snowflake_grant_privileges_to_account_role.schema_create["GOLD.ENGINEER"].privileges, "CREATE DATA METRIC FUNCTION")
    error_message = "ENGINEER must be able to create the object types the generation plane renders."
  }

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.task_execution)) == toset(["ENGINEER", "PIPELINE"])
    error_message = "Only ENGINEER and PIPELINE may execute tasks."
  }

  assert {
    condition     = toset(keys(snowflake_grant_account_role.to_admin)) == toset(["ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR", "SANDBOX"])
    error_message = "Every non-admin role is granted to ADMIN."
  }

  assert {
    condition     = snowflake_grant_account_role.admin_to_parent.parent_role_name == "SYSADMIN"
    error_message = "ADMIN is granted to SYSADMIN by default."
  }
}

run "warehouse_grants_follow_tier_users" {
  command = plan

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.warehouse : split(".", k)[0] if split(".", k)[1] == "CONSUMER"]) == toset(["simple"])
    error_message = "CONSUMER may only use the simple warehouse."
  }

  assert {
    condition = alltrue([
      for t in ["simple", "medium", "complex"] :
      contains(snowflake_grant_privileges_to_account_role.warehouse["${t}.AUDITOR"].privileges, "MONITOR") &&
      snowflake_grant_privileges_to_account_role.warehouse["${t}.ADMIN"].privileges == toset(["MODIFY", "MONITOR", "OPERATE", "USAGE"])
    ])
    error_message = "AUDITOR monitors and ADMIN controls every warehouse."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.warehouse["medium.PIPELINE"].privileges == toset(["OPERATE", "USAGE"])
    error_message = "PIPELINE may operate and use the warehouses it runs on."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.warehouse["medium.ENGINEER"].privileges == toset(["USAGE"])
    error_message = "Other users get USAGE only."
  }
}

# ---------------------------------------------------------------------------
# Cost control
# ---------------------------------------------------------------------------

run "credit_quota_attaches_a_monitor_to_every_warehouse" {
  command = plan

  variables {
    monthly_credit_quota = 500
  }

  assert {
    condition     = length(snowflake_resource_monitor.this) == 1 && snowflake_resource_monitor.this[0].name == "ASTRA_DEV_MONITOR"
    error_message = "A credit quota creates exactly one monitor named <PREFIX>_<ENV>_MONITOR."
  }

  assert {
    condition     = snowflake_resource_monitor.this[0].credit_quota == 500 && snowflake_resource_monitor.this[0].suspend_trigger == 100
    error_message = "The monitor must carry the quota and suspend at 100 percent."
  }

  assert {
    condition     = alltrue([for w in snowflake_warehouse.tier : w.resource_monitor == "ASTRA_DEV_MONITOR"])
    error_message = "Every warehouse must be attached to the monitor."
  }
}

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

run "accepts_additional_short_environment_names" {
  command = plan

  variables {
    environment = "perf"
  }

  assert {
    condition     = snowflake_database.this.name == "ASTRA_PERF" && snowflake_warehouse.tier["simple"].name == "ASTRA_PERF_WH_SIMPLE"
    error_message = "A new environment is only a name; every object follows the same naming rule."
  }
}

run "rejects_malformed_environment_name" {
  command = plan

  variables {
    environment = "Prod-1"
  }

  expect_failures = [var.environment]
}

run "rejects_missing_required_tier" {
  command = plan

  variables {
    warehouse_tiers = {
      simple = { size = "XSMALL" }
      medium = { size = "SMALL" }
    }
  }

  expect_failures = [var.warehouse_tiers]
}

run "rejects_invalid_warehouse_size" {
  command = plan

  variables {
    warehouse_tiers = {
      simple  = { size = "XSMALL" }
      medium  = { size = "SMALL" }
      complex = { size = "HUGE" }
    }
  }

  expect_failures = [var.warehouse_tiers]
}

run "rejects_unknown_role_in_schema_access" {
  command = plan

  variables {
    schemas = {
      BRONZE = { comment = "raw", readers = ["ANALYST"] }
    }
  }

  expect_failures = [var.schemas]
}

run "rejects_lower_case_schema_name" {
  command = plan

  variables {
    schemas = {
      bronze = { comment = "raw" }
    }
  }

  expect_failures = [var.schemas]
}

run "rejects_negative_credit_quota" {
  command = plan

  variables {
    monthly_credit_quota = -1
  }

  expect_failures = [var.monthly_credit_quota]
}
