# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------
#
# The access matrix is data (var.schemas and var.warehouse_tiers), flattened
# in locals.tf. Each resource below is one row of that matrix so that a plan
# reads as "role X gets privileges Y on Z".

# --- Database -------------------------------------------------------------

resource "snowflake_grant_privileges_to_account_role" "database_usage" {
  for_each = local.role_names

  account_role_name = snowflake_account_role.this[each.key].name
  privileges        = each.key == "ADMIN" ? ["USAGE", "MONITOR", "CREATE SCHEMA"] : ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.this.name
  }
}

# --- Schemas --------------------------------------------------------------

resource "snowflake_grant_privileges_to_account_role" "schema_usage" {
  for_each = local.schema_role_access

  account_role_name = snowflake_account_role.this[each.value.role].name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = snowflake_schema.this[each.value.schema].fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "schema_create" {
  for_each = { for k, a in local.schema_role_access : k => a if a.creator }

  account_role_name = snowflake_account_role.this[each.value.role].name
  privileges        = local.schema_create_privileges

  on_schema {
    schema_name = snowflake_schema.this[each.value.schema].fully_qualified_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "future_objects" {
  for_each = local.future_object_grants

  account_role_name = snowflake_account_role.this[each.value.role].name
  privileges        = each.value.privileges

  on_schema_object {
    future {
      object_type_plural = each.value.object_type
      in_schema          = snowflake_schema.this[each.value.schema].fully_qualified_name
    }
  }
}

# --- Warehouses -----------------------------------------------------------

resource "snowflake_grant_privileges_to_account_role" "warehouse" {
  for_each = local.warehouse_role_grants

  account_role_name = snowflake_account_role.this[each.value.role].name
  privileges        = each.value.privileges

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.tier[each.value.tier].name
  }
}

# --- Account --------------------------------------------------------------

resource "snowflake_grant_privileges_to_account_role" "task_execution" {
  for_each = toset(local.task_execution_roles)

  account_role_name = snowflake_account_role.this[each.key].name
  privileges        = ["EXECUTE TASK", "EXECUTE MANAGED TASK"]
  on_account        = true
}
