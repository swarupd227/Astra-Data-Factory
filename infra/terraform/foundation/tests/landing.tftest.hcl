# Unit tests for the landing zone and Snowpipe (S1.1.3).
#
# Mocked providers. The live checks (a dropped file appears within 60 s, a
# re-dropped file is not loaded twice and is logged) are
# scripts/verify_snowpipe.py.

mock_provider "snowflake" {}

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

variables {
  environment         = "dev"
  landing_bucket_name = "astra-dev-landing-123456789012"
}

# ---------------------------------------------------------------------------
# AWS side
# ---------------------------------------------------------------------------

run "creates_the_landing_bucket_and_its_read_only_role" {
  command = plan

  assert {
    condition     = module.landing_bucket.name == "astra-dev-landing-123456789012"
    error_message = "The landing bucket must use the configured name."
  }

  assert {
    condition     = aws_iam_role.snowflake_landing.name == "ASTRA_DEV_SNOWFLAKE_LANDING"
    error_message = "The landing role must be named <PREFIX>_<ENV>_SNOWFLAKE_LANDING."
  }

  assert {
    condition     = aws_iam_role_policy.snowflake_landing.name == "landing-bucket-read"
    error_message = "The landing role carries a read-only policy."
  }
}

run "landing_bucket_name_is_derived_when_not_set" {
  command = plan

  variables {
    landing_bucket_name = null
  }

  assert {
    condition     = startswith(module.landing_bucket.name, "astra-dev-landing-")
    error_message = "Derived bucket name must be <prefix>-<env>-landing-<account id>."
  }
}

run "s3_events_on_the_landing_prefix_go_to_snowpipe" {
  command = plan

  assert {
    condition     = length(aws_s3_bucket_notification.landing.queue) == 1
    error_message = "Exactly one queue rule on the landing bucket."
  }

  assert {
    condition = alltrue([
      for q in aws_s3_bucket_notification.landing.queue :
      q.filter_prefix == "landing/" && contains(q.events, "s3:ObjectCreated:*")
    ])
    error_message = "The rule must cover object creation under the landing prefix."
  }
}

run "landing_prefix_is_configurable" {
  command = plan

  variables {
    landing_prefix = "inbound/custodians"
  }

  assert {
    condition     = snowflake_stage_external_s3.landing.url == "s3://astra-dev-landing-123456789012/inbound/custodians/"
    error_message = "The stage URL must follow the landing prefix."
  }

  assert {
    condition     = alltrue([for q in aws_s3_bucket_notification.landing.queue : q.filter_prefix == "inbound/custodians/"])
    error_message = "The notification prefix must follow the landing prefix."
  }

  assert {
    condition     = toset(snowflake_storage_integration_aws.landing.storage_allowed_locations) == toset(["s3://astra-dev-landing-123456789012/inbound/custodians/", "s3://astra-dev-landing-123456789012/sandbox/"])
    error_message = "The storage integration must be limited to the landing and sandbox prefixes."
  }
}

# ---------------------------------------------------------------------------
# Snowflake side
# ---------------------------------------------------------------------------

run "storage_integration_points_at_the_landing_role" {
  command = plan

  assert {
    condition     = snowflake_storage_integration_aws.landing.name == "ASTRA_DEV_LANDING" && snowflake_storage_integration_aws.landing.enabled
    error_message = "Storage integration must be named <PREFIX>_<ENV>_LANDING and enabled."
  }

  assert {
    condition     = endswith(snowflake_storage_integration_aws.landing.storage_aws_role_arn, ":role/ASTRA_DEV_SNOWFLAKE_LANDING")
    error_message = "The integration must reference the landing IAM role by its composed ARN."
  }

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.storage_integration_usage)) == toset(["ADMIN", "ENGINEER", "PIPELINE", "SANDBOX"])
    error_message = "ADMIN, ENGINEER, PIPELINE and SANDBOX may use the storage integration."
  }
}

run "file_format_keeps_lines_untouched" {
  command = plan

  assert {
    condition = (
      snowflake_file_format_csv.raw_lines.field_delimiter == "NONE" &&
      snowflake_file_format_csv.raw_lines.escape == "NONE" &&
      snowflake_file_format_csv.raw_lines.escape_unenclosed_field == "NONE" &&
      snowflake_file_format_csv.raw_lines.field_optionally_enclosed_by == "NONE"
    )
    error_message = "No delimiter, escaping or quoting may be interpreted."
  }

  assert {
    condition = (
      snowflake_file_format_csv.raw_lines.skip_header == 0 &&
      snowflake_file_format_csv.raw_lines.skip_blank_lines == "false" &&
      snowflake_file_format_csv.raw_lines.trim_space == "false" &&
      snowflake_file_format_csv.raw_lines.empty_field_as_null == "false" &&
      snowflake_file_format_csv.raw_lines.replace_invalid_characters == "false" &&
      length(snowflake_file_format_csv.raw_lines.null_if) == 0
    )
    error_message = "No line may be skipped, trimmed, altered or turned into NULL."
  }
}

run "stage_watches_the_landing_prefix_with_a_directory_table" {
  command = plan

  assert {
    condition     = snowflake_stage_external_s3.landing.name == "LANDING" && snowflake_stage_external_s3.landing.schema == "BRONZE"
    error_message = "The stage lives in BRONZE as LANDING."
  }

  assert {
    condition     = snowflake_stage_external_s3.landing.url == "s3://astra-dev-landing-123456789012/landing/"
    error_message = "The stage URL must be the landing prefix."
  }

  assert {
    condition     = snowflake_stage_external_s3.landing.storage_integration == "ASTRA_DEV_LANDING"
    error_message = "The stage must authenticate through the storage integration."
  }

  assert {
    condition     = snowflake_stage_external_s3.landing.directory[0].enable && snowflake_stage_external_s3.landing.directory[0].auto_refresh == "true"
    error_message = "The directory table must be enabled and auto-refreshing for duplicate detection."
  }

  assert {
    condition     = length(snowflake_stage_external_s3.landing.file_format) == 1
    error_message = "The stage defaults to the raw-lines file format."
  }
}

run "raw_lines_table_carries_file_name_row_number_and_ingest_time" {
  command = plan

  assert {
    condition     = snowflake_iceberg_table.raw_lines.name == "RAW_LINES" && snowflake_iceberg_table.raw_lines.schema == "BRONZE"
    error_message = "The raw-lines table lives in BRONZE."
  }

  assert {
    condition     = snowflake_iceberg_table.raw_lines.external_volume == "ASTRA_DEV_ICEBERG"
    error_message = "The raw-lines table is Iceberg on the environment volume."
  }

  assert {
    condition     = [for c in snowflake_iceberg_table.raw_lines.column : c.name] == ["FILE_NAME", "ROW_NUMBER", "LINE", "FILE_CONTENT_KEY", "FILE_LAST_MODIFIED", "INGESTED_AT"]
    error_message = "Columns must be FILE_NAME, ROW_NUMBER, LINE, FILE_CONTENT_KEY, FILE_LAST_MODIFIED, INGESTED_AT in that order."
  }

  assert {
    condition = alltrue([
      for c in snowflake_iceberg_table.raw_lines.column :
      c.not_null == "true" if contains(["FILE_NAME", "ROW_NUMBER", "INGESTED_AT"], c.name)
    ])
    error_message = "File name, row number and ingest timestamp are mandatory."
  }
}

run "pipe_auto_ingests_lines_with_metadata" {
  command = plan

  assert {
    condition     = snowflake_pipe.raw_lines.auto_ingest && snowflake_pipe.raw_lines.schema == "BRONZE"
    error_message = "The pipe must auto-ingest from S3 events."
  }

  assert {
    condition = alltrue([
      for needle in [
        "COPY INTO \"ASTRA_DEV\".\"BRONZE\".\"RAW_LINES\" (FILE_NAME, ROW_NUMBER, LINE, FILE_CONTENT_KEY, FILE_LAST_MODIFIED, INGESTED_AT)",
        "METADATA$FILENAME",
        "METADATA$FILE_ROW_NUMBER",
        "METADATA$START_SCAN_TIME",
        "FROM @\"ASTRA_DEV\".\"BRONZE\".\"LANDING\"",
        "FILE_FORMAT = (FORMAT_NAME = '\"ASTRA_DEV\".\"BRONZE\".\"RAW_LINES\"')",
      ] : strcontains(snowflake_pipe.raw_lines.copy_statement, needle)
    ])
    error_message = "The COPY must read from the landing stage with the raw-lines format and record file name, row number and scan time."
  }
}

run "file_load_log_is_reconciled_every_minute_serverlessly" {
  command = plan

  assert {
    condition     = snowflake_iceberg_table.file_load_log.name == "FILE_LOAD_LOG" && snowflake_iceberg_table.file_load_log.schema == "CONTROL"
    error_message = "The file load log lives in CONTROL."
  }

  assert {
    condition     = [for c in snowflake_iceberg_table.file_load_log.column : c.name] == ["FILE_NAME", "FILE_LAST_MODIFIED", "STATUS", "FILE_HASH", "FILE_SIZE", "ROW_COUNT", "DETAIL", "OBSERVED_AT"]
    error_message = "Log columns must identify the file version, the outcome and the reason."
  }

  assert {
    condition     = snowflake_task.reconcile_file_loads.started && snowflake_task.reconcile_file_loads.schedule[0].minutes == 1
    error_message = "The reconciliation task runs every minute and starts enabled."
  }

  assert {
    condition     = snowflake_task.reconcile_file_loads.warehouse == null && snowflake_task.reconcile_file_loads.user_task_managed_initial_warehouse_size == "XSMALL"
    error_message = "The task is serverless so no warehouse is kept awake by a one-minute schedule."
  }

  assert {
    condition = alltrue([
      for needle in [
        "INSERT INTO \"ASTRA_DEV\".\"CONTROL\".\"FILE_LOAD_LOG\"",
        "DIRECTORY(@\"ASTRA_DEV\".\"BRONZE\".\"LANDING\")",
        "INFORMATION_SCHEMA.COPY_HISTORY",
        "'DUPLICATE'",
        "'CONFLICT'",
        "'LOADED'",
        "'FAILED'",
      ] : strcontains(snowflake_task.reconcile_file_loads.sql_statement, needle)
    ])
    error_message = "Reconciliation must compare the stage directory with Snowpipe history and classify every arrival."
  }
}

run "reconcile_interval_is_configurable" {
  command = plan

  variables {
    landing_reconcile_interval_minutes = 5
  }

  assert {
    condition     = snowflake_task.reconcile_file_loads.schedule[0].minutes == 5
    error_message = "The task schedule must follow the variable."
  }
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

run "rejects_landing_prefix_with_slashes_at_the_ends" {
  command = plan

  variables {
    landing_prefix = "/landing/"
  }

  expect_failures = [var.landing_prefix]
}

run "rejects_reconcile_interval_out_of_range" {
  command = plan

  variables {
    landing_reconcile_interval_minutes = 0
  }

  expect_failures = [var.landing_reconcile_interval_minutes]
}

run "rejects_schemas_without_bronze_and_control" {
  command = plan

  variables {
    schemas = {
      SILVER = { comment = "canonical" }
    }
  }

  expect_failures = [var.schemas]
}
