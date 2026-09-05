# ---------------------------------------------------------------------------
# Database and schemas
# ---------------------------------------------------------------------------

resource "snowflake_database" "this" {
  name                        = local.database_name
  comment                     = "Astra Data Factory ${var.environment} environment. Managed by Terraform; do not edit by hand."
  data_retention_time_in_days = var.data_retention_days

  # Every table created in this database is a Snowflake-managed Iceberg table
  # on the environment's external volume unless a renderer says otherwise.
  # COMPATIBLE serialization keeps the Parquet files readable by external
  # engines (pg_lake, Spark, DuckDB) through Open Catalog.
  external_volume              = snowflake_external_volume.iceberg.name
  catalog                      = "SNOWFLAKE"
  storage_serialization_policy = "COMPATIBLE"
}

resource "snowflake_schema" "this" {
  for_each = var.schemas

  database                    = snowflake_database.this.name
  name                        = each.key
  comment                     = each.value.comment
  with_managed_access         = each.value.managed_access ? "true" : "false"
  data_retention_time_in_days = var.data_retention_days
}

# ---------------------------------------------------------------------------
# Cost control (optional)
# ---------------------------------------------------------------------------

resource "snowflake_resource_monitor" "this" {
  count = var.monthly_credit_quota == null ? 0 : 1

  name            = "${local.name_prefix}_MONITOR"
  credit_quota    = var.monthly_credit_quota
  frequency       = "MONTHLY"
  start_timestamp = "IMMEDIATELY"

  notify_triggers           = [50, 75, 90]
  suspend_trigger           = 100
  suspend_immediate_trigger = 110
}
