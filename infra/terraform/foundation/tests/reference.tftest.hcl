# Unit tests for reference-data replication support (S2.3.3).
#
# Mocked providers. The replication procedures and tasks themselves are
# rendered by astra-data from the domain pack and deployed as a bundle.

mock_provider "snowflake" {}

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

variables {
  environment = "dev"
}

run "reference_snapshots_are_read_from_their_own_prefix_through_the_landing_integration" {
  command = plan

  variables {
    landing_bucket_name = "astra-dev-landing-123456789012"
  }

  assert {
    condition     = snowflake_stage_external_s3.reference.url == "s3://astra-dev-landing-123456789012/reference/" && snowflake_stage_external_s3.reference.schema == "REFERENCE"
    error_message = "The reference stage points at the reference prefix of the landing bucket, in the REFERENCE schema."
  }

  assert {
    condition     = contains(snowflake_storage_integration_aws.landing.storage_allowed_locations, "s3://astra-dev-landing-123456789012/reference/")
    error_message = "The landing integration must allow the reference prefix, or the stage cannot read it."
  }

  assert {
    condition     = snowflake_file_format_csv.reference.skip_header == 1 && snowflake_file_format_csv.reference.field_delimiter == "," && snowflake_file_format_csv.reference.empty_field_as_null == "true" && snowflake_file_format_csv.reference.error_on_column_count_mismatch == "true"
    error_message = "Snapshots are CSV with a header row; blank is NULL; a wrong column count fails the load rather than misaligning columns."
  }
}

run "reference_prefix_must_not_sit_inside_the_landing_prefix" {
  command = plan

  variables {
    reference_prefix = "landing/reference"
  }

  expect_failures = [var.reference_prefix]
}

run "runs_and_feeds_tables_carry_row_counts_and_expectations" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.reference_data_runs.column : c.name] == ["RUN_ID", "FEED_ID", "DOMAIN", "STARTED_AT", "FINISHED_AT", "STATUS", "SNAPSHOT_DATE", "FILES_LOADED", "ROWS_SOURCE", "ROWS_CONFLICT", "ROWS_INSERTED", "ROWS_UPDATED", "ROWS_DELETED", "ROWS_UNCHANGED", "ROWS_TOTAL", "ERROR"]
    error_message = "REFERENCE_DATA_RUNS records every run with the row counts the story asks for."
  }

  assert {
    condition     = [for c in snowflake_iceberg_table.reference_feeds.column : c.name] == ["FEED_ID", "DOMAIN", "NAME", "SYSTEM", "TABLE_NAME", "SCHEDULE_CRON", "TIMEZONE", "EXPECTED_EVERY_HOURS", "STALE_SEVERITY", "ENABLED", "SOURCE", "UPDATED_AT"]
    error_message = "REFERENCE_FEEDS holds each feed's schedule and how often it is expected."
  }
}

run "a_feed_with_no_recent_success_raises_a_stale_alert_once_a_day" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "WHERE f.ENABLED",
        "r.STATUS = 'succeeded'",
        "LAST_SUCCESS IS NULL OR LAST_SUCCESS < DATEADD('hour', -EXPECTED_EVERY_HOURS, SYSDATE())",
        "'reference_stale'",
        "s.STALE_SEVERITY",
        "'reference:' || s.FEED_ID || ':' || CURRENT_DATE()::STRING",
      ] : strcontains(snowflake_procedure_sql.detect_stale_reference_data.procedure_definition, needle)
    ])
    error_message = "The detector reads enabled feeds, compares the last successful run with the expected interval, raises at the feed's severity and dedupes per feed and day."
  }

  assert {
    condition     = strcontains(snowflake_procedure_sql.run_alerting.procedure_definition, "DETECT_STALE_REFERENCE_DATA")
    error_message = "RUN_ALERTING must call the stale reference-data detector."
  }
}
