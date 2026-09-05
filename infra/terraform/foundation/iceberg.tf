# ---------------------------------------------------------------------------
# Snowflake: external volume and Open Catalog sync (S1.1.2)
# ---------------------------------------------------------------------------

locals {
  external_volume_name          = "${local.name_prefix}_ICEBERG"
  open_catalog_integration_name = "${local.name_prefix}_OPEN_CATALOG"

  # Roles that create Iceberg tables need USAGE on the volume even though the
  # database points at it by default.
  external_volume_users = ["ADMIN", "ENGINEER", "PIPELINE", "SANDBOX"]
}

# --- External volume ------------------------------------------------------

resource "snowflake_external_volume" "iceberg" {
  name         = local.external_volume_name
  comment      = "Iceberg storage for ${local.name_prefix} on ${local.iceberg_base_url}. Managed by Terraform."
  allow_writes = "true"

  storage_location {
    storage_location_name = "s3-${var.aws_region}"
    storage_provider      = "S3"
    storage_base_url      = local.iceberg_base_url
    storage_aws_role_arn  = local.snowflake_iceberg_role_arn
    encryption_type       = "AWS_SSE_S3"
  }
}

resource "snowflake_grant_privileges_to_account_role" "external_volume_usage" {
  for_each = toset(local.external_volume_users)

  account_role_name = snowflake_account_role.this[each.key].name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "EXTERNAL VOLUME"
    object_name = snowflake_external_volume.iceberg.name
  }
}

# --- Open Catalog sync ----------------------------------------------------
#
# Snowflake pushes the metadata of every Iceberg table in the database to the
# Open Catalog catalog, where the Iceberg REST API serves it to pg_lake, Spark
# and DuckDB. Snowflake stays the only writer of the data files.

resource "snowflake_catalog_integration_open_catalog" "sync" {
  count = var.open_catalog == null ? 0 : 1

  name    = local.open_catalog_integration_name
  enabled = true
  comment = "Syncs ${local.database_name} Iceberg tables to Open Catalog ${var.open_catalog.catalog_name}. Managed by Terraform."

  rest_config {
    catalog_uri  = "${trimsuffix(var.open_catalog.account_url, "/")}/polaris/api/catalog"
    catalog_name = var.open_catalog.catalog_name
  }

  rest_authentication {
    oauth_client_id      = var.open_catalog_client_id
    oauth_client_secret  = var.open_catalog_client_secret
    oauth_allowed_scopes = ["PRINCIPAL_ROLE:ALL"]
  }
}

# CATALOG_SYNC is a parameter the provider does not expose on databases yet,
# so it is set with an explicit statement and a matching revert.
resource "snowflake_execute" "catalog_sync" {
  count = var.open_catalog == null ? 0 : 1

  execute = "ALTER DATABASE \"${snowflake_database.this.name}\" SET CATALOG_SYNC = '${snowflake_catalog_integration_open_catalog.sync[0].name}'"
  revert  = "ALTER DATABASE \"${snowflake_database.this.name}\" UNSET CATALOG_SYNC"
  query   = "SHOW PARAMETERS LIKE 'CATALOG_SYNC' IN DATABASE \"${snowflake_database.this.name}\""
}
