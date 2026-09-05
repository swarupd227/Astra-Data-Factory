# Unit tests for Iceberg storage and Open Catalog sync (S1.1.2).
#
# Mocked providers: no AWS or Snowflake account is needed. The live checks
# (catalog lists a new table within a minute, DuckDB reads it, an ungranted
# principal is denied) are `tools/opencatalog verify`.

mock_provider "snowflake" {}

mock_provider "aws" {
  # The IAM role resource validates its trust policy at plan time, so the
  # mocked policy document must at least be a JSON object.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

variables {
  environment         = "dev"
  iceberg_bucket_name = "astra-dev-iceberg-123456789012"
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# The bucket hardening itself is tested in modules/private-bucket/tests.

run "creates_the_iceberg_bucket_with_the_configured_name" {
  command = plan

  assert {
    condition     = module.iceberg_bucket.name == "astra-dev-iceberg-123456789012"
    error_message = "The bucket must use the configured name."
  }
}

run "bucket_name_is_derived_when_not_set" {
  command = plan

  variables {
    iceberg_bucket_name = null
  }

  assert {
    condition     = startswith(module.iceberg_bucket.name, "astra-dev-iceberg-")
    error_message = "Derived bucket name must be <prefix>-<env>-iceberg-<account id>."
  }
}

# ---------------------------------------------------------------------------
# External volume
# ---------------------------------------------------------------------------

run "external_volume_points_at_the_bucket_through_the_snowflake_role" {
  command = plan

  assert {
    condition     = snowflake_external_volume.iceberg.name == "ASTRA_DEV_ICEBERG" && snowflake_external_volume.iceberg.allow_writes == "true"
    error_message = "External volume must be named <PREFIX>_<ENV>_ICEBERG and writable."
  }

  assert {
    condition = (
      snowflake_external_volume.iceberg.storage_location[0].storage_provider == "S3" &&
      snowflake_external_volume.iceberg.storage_location[0].storage_base_url == "s3://astra-dev-iceberg-123456789012/" &&
      snowflake_external_volume.iceberg.storage_location[0].encryption_type == "AWS_SSE_S3"
    )
    error_message = "Storage location must be the bucket root on S3 with SSE-S3."
  }

  assert {
    condition     = endswith(snowflake_external_volume.iceberg.storage_location[0].storage_aws_role_arn, ":role/ASTRA_DEV_SNOWFLAKE_ICEBERG")
    error_message = "The volume must reference the Snowflake IAM role by its composed ARN."
  }

  assert {
    condition     = aws_iam_role.snowflake_iceberg.name == "ASTRA_DEV_SNOWFLAKE_ICEBERG"
    error_message = "The Snowflake IAM role name must match the ARN the volume references."
  }
}

run "database_defaults_to_iceberg_on_the_volume" {
  command = plan

  assert {
    condition = (
      snowflake_database.this.external_volume == "ASTRA_DEV_ICEBERG" &&
      snowflake_database.this.catalog == "SNOWFLAKE" &&
      snowflake_database.this.storage_serialization_policy == "COMPATIBLE"
    )
    error_message = "Tables in the database must default to Snowflake-managed Iceberg on the volume with COMPATIBLE serialization."
  }
}

run "table_creating_roles_can_use_the_volume" {
  command = plan

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.external_volume_usage)) == toset(["ADMIN", "ENGINEER", "PIPELINE", "SANDBOX"])
    error_message = "ADMIN, ENGINEER, PIPELINE and SANDBOX get USAGE on the external volume; read-only roles do not."
  }

  assert {
    condition     = alltrue([for g in snowflake_grant_privileges_to_account_role.external_volume_usage : g.privileges == toset(["USAGE"])])
    error_message = "Only USAGE is granted on the volume."
  }
}

# ---------------------------------------------------------------------------
# Open Catalog
# ---------------------------------------------------------------------------

run "open_catalog_is_off_until_configured" {
  command = plan

  assert {
    condition = (
      length(snowflake_catalog_integration_open_catalog.sync) == 0 &&
      length(snowflake_execute.catalog_sync) == 0 &&
      length(aws_iam_role.open_catalog) == 0
    )
    error_message = "No catalog integration, sync parameter or Open Catalog role without open_catalog."
  }
}

run "open_catalog_sync_is_wired_when_configured" {
  command = plan

  variables {
    open_catalog = {
      account_url  = "https://artizent-astra_oc.snowflakecomputing.com"
      catalog_name = "astra_dev"
    }
    open_catalog_client_id     = "client-id"
    open_catalog_client_secret = "client-secret"
  }

  assert {
    condition     = snowflake_catalog_integration_open_catalog.sync[0].name == "ASTRA_DEV_OPEN_CATALOG" && snowflake_catalog_integration_open_catalog.sync[0].enabled
    error_message = "Catalog integration must be named <PREFIX>_<ENV>_OPEN_CATALOG and enabled."
  }

  assert {
    condition = (
      snowflake_catalog_integration_open_catalog.sync[0].rest_config[0].catalog_uri == "https://artizent-astra_oc.snowflakecomputing.com/polaris/api/catalog" &&
      snowflake_catalog_integration_open_catalog.sync[0].rest_config[0].catalog_name == "astra_dev"
    )
    error_message = "REST config must point at the account's Polaris catalog API and the named catalog."
  }

  assert {
    condition     = snowflake_catalog_integration_open_catalog.sync[0].rest_authentication[0].oauth_allowed_scopes == tolist(["PRINCIPAL_ROLE:ALL"])
    error_message = "OAuth scope must be PRINCIPAL_ROLE:ALL."
  }

  assert {
    condition     = snowflake_execute.catalog_sync[0].execute == "ALTER DATABASE \"ASTRA_DEV\" SET CATALOG_SYNC = 'ASTRA_DEV_OPEN_CATALOG'"
    error_message = "CATALOG_SYNC must be set on the database to the integration."
  }

  assert {
    condition     = snowflake_execute.catalog_sync[0].revert == "ALTER DATABASE \"ASTRA_DEV\" UNSET CATALOG_SYNC"
    error_message = "Revert must unset CATALOG_SYNC."
  }

  assert {
    condition     = length(aws_iam_role.open_catalog) == 0
    error_message = "The Open Catalog IAM role waits for the catalog's IAM user and external id."
  }
}

run "open_catalog_role_is_created_once_the_catalog_reports_its_identity" {
  command = plan

  variables {
    open_catalog = {
      account_url  = "https://artizent-astra_oc.snowflakecomputing.com"
      catalog_name = "astra_dev"
      iam_user_arn = "arn:aws:iam::123456789012:user/polaris-user"
      external_id  = "external-id"
    }
    open_catalog_client_id     = "client-id"
    open_catalog_client_secret = "client-secret"
  }

  assert {
    condition     = length(aws_iam_role.open_catalog) == 1 && aws_iam_role.open_catalog[0].name == "ASTRA_DEV_OPEN_CATALOG"
    error_message = "Open Catalog IAM role must be created with the composed name."
  }

  assert {
    condition     = length(aws_iam_role_policy.open_catalog) == 1
    error_message = "Open Catalog IAM role must carry the read-only bucket policy."
  }
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

run "rejects_open_catalog_without_credentials" {
  command = plan

  variables {
    open_catalog = {
      account_url  = "https://artizent-astra_oc.snowflakecomputing.com"
      catalog_name = "astra_dev"
    }
  }

  expect_failures = [var.open_catalog]
}

run "rejects_open_catalog_url_with_a_path" {
  command = plan

  variables {
    open_catalog = {
      account_url  = "https://artizent-astra_oc.snowflakecomputing.com/polaris"
      catalog_name = "astra_dev"
    }
    open_catalog_client_id     = "client-id"
    open_catalog_client_secret = "client-secret"
  }

  expect_failures = [var.open_catalog]
}

run "rejects_half_set_open_catalog_identity" {
  command = plan

  variables {
    open_catalog = {
      account_url  = "https://artizent-astra_oc.snowflakecomputing.com"
      catalog_name = "astra_dev"
      iam_user_arn = "arn:aws:iam::123456789012:user/polaris-user"
    }
    open_catalog_client_id     = "client-id"
    open_catalog_client_secret = "client-secret"
  }

  expect_failures = [var.open_catalog]
}

run "rejects_invalid_bucket_name" {
  command = plan

  variables {
    iceberg_bucket_name = "Not_Valid"
  }

  expect_failures = [var.iceberg_bucket_name]
}
