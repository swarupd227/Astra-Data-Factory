# ---------------------------------------------------------------------------
# Reference-data replication (S2.3.3)
#
# Reference data (the security master, the account cross-reference) arrives
# as full snapshots under the landing bucket's reference prefix. The domain
# pack declares the feeds (domains/<pack>/reference-data.yaml); the
# generation plane renders one replication procedure and one nightly task
# per feed into a release bundle that deploys into the REFERENCE schema.
# The foundation provides what those need: the stage and file format the
# snapshots are read through, the run log every replication writes its row
# counts to, the feed table that says how often each feed is expected, and
# the detector that alerts when a feed goes stale.
# ---------------------------------------------------------------------------

locals {
  reference_schema         = "REFERENCE"
  reference_base_url       = "s3://${local.landing_bucket_name}/${var.reference_prefix}/"
  reference_data_runs_fqn  = "\"${local.database_name}\".\"${local.control_schema}\".\"REFERENCE_DATA_RUNS\""
  reference_feeds_fqn      = "\"${local.database_name}\".\"${local.control_schema}\".\"REFERENCE_FEEDS\""
  reference_stage_fqn      = "\"${local.database_name}\".\"${local.reference_schema}\".\"LANDING\""
  reference_csv_format_fqn = "\"${local.database_name}\".\"${local.reference_schema}\".\"CSV\""

  # A feed with no successful replication within its expected interval raises
  # one 'reference_stale' alert per day at the feed's severity.
  detect_stale_reference_data_definition = <<-SQL
    DECLARE
      raised INTEGER DEFAULT 0;
    BEGIN
      INSERT INTO ${local.alerts_fqn} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE)
      WITH latest AS (
        SELECT f.FEED_ID, f.NAME, f.SYSTEM, f.TABLE_NAME, f.EXPECTED_EVERY_HOURS, f.STALE_SEVERITY,
               (SELECT MAX(r.FINISHED_AT) FROM ${local.reference_data_runs_fqn} r WHERE r.FEED_ID = f.FEED_ID AND r.STATUS = 'succeeded') AS LAST_SUCCESS,
               (SELECT MAX(r.STARTED_AT) FROM ${local.reference_data_runs_fqn} r WHERE r.FEED_ID = f.FEED_ID) AS LAST_ATTEMPT
        FROM ${local.reference_feeds_fqn} f
        WHERE f.ENABLED
      ),
      stale AS (
        SELECT * FROM latest
        WHERE LAST_SUCCESS IS NULL OR LAST_SUCCESS < DATEADD('hour', -EXPECTED_EVERY_HOURS, SYSDATE())
      )
      SELECT UUID_STRING(),
             SYSDATE(),
             'reference_stale',
             s.STALE_SEVERITY,
             NULL,
             s.NAME || ' (' || s.SYSTEM || ') has not been replicated for over ' || s.EXPECTED_EVERY_HOURS || ' hours',
             'Reference feed ' || s.FEED_ID || ' from ' || s.SYSTEM || ' is expected to replicate into ${local.reference_schema}.' || s.TABLE_NAME
               || ' at least every ' || s.EXPECTED_EVERY_HOURS || ' hours. Last successful run: '
               || COALESCE(s.LAST_SUCCESS::TIMESTAMP_NTZ(0)::STRING || ' UTC', 'never') || '. Last attempt: '
               || COALESCE(s.LAST_ATTEMPT::TIMESTAMP_NTZ(0)::STRING || ' UTC', 'never')
               || '. Resolution against this feed uses the last replica until it is refreshed.',
             'reference:' || s.FEED_ID || ':' || CURRENT_DATE()::STRING,
             CURRENT_DATE()
      FROM stale s
      WHERE NOT EXISTS (
        SELECT 1 FROM ${local.alerts_fqn} a
        WHERE a.SOURCE_KEY = 'reference:' || s.FEED_ID || ':' || CURRENT_DATE()::STRING);
      raised := SQLROWCOUNT;
      RETURN raised;
    END;
  SQL
}

# --- File format and stage ------------------------------------------------

# Reference snapshots are ordinary CSV: a header row, comma-separated,
# quoted where needed, blank meaning NULL. The rendered replication
# procedures cast each column to the feed's declared type.
resource "snowflake_file_format_csv" "reference" {
  name     = "CSV"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.reference_schema].name
  comment  = "Reference-data snapshots: header row, comma-separated, quoted where needed, blank is NULL. Managed by Terraform."

  field_delimiter                = ","
  skip_header                    = 1
  skip_blank_lines               = "true"
  trim_space                     = "true"
  empty_field_as_null            = "true"
  error_on_column_count_mismatch = "true"
  replace_invalid_characters     = "false"
  escape                         = "NONE"
  escape_unenclosed_field        = "NONE"
  field_optionally_enclosed_by   = "\""
  null_if                        = [""]
  encoding                       = "UTF8"
  compression                    = "AUTO"

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_stage_external_s3" "reference" {
  name     = "LANDING"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.reference_schema].name
  comment  = "Reference-data snapshots ${local.reference_base_url}, one folder per feed. Managed by Terraform."

  url                 = local.reference_base_url
  storage_integration = snowflake_storage_integration_aws.landing.name

  file_format {
    format_name = local.reference_csv_format_fqn
  }

  depends_on = [snowflake_file_format_csv.reference, snowflake_grant_privileges_to_account_role.future_objects]
}

# --- Control tables -------------------------------------------------------

resource "snowflake_iceberg_table" "reference_feeds" {
  name     = "REFERENCE_FEEDS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Reference-data feeds and how often each is expected, synced from domains/<pack>/reference-data.yaml by astra-data. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/reference_feeds/"

  column {
    name     = "FEED_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "DOMAIN"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "SYSTEM"
    type     = "STRING"
    not_null = "true"
    comment  = "Source system of record, as the client names it"
  }
  column {
    name     = "TABLE_NAME"
    type     = "STRING"
    not_null = "true"
    comment  = "Replica table in the REFERENCE schema"
  }
  column {
    name     = "SCHEDULE_CRON"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "TIMEZONE"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "EXPECTED_EVERY_HOURS"
    type     = "NUMBER(5,0)"
    not_null = "true"
    comment  = "A successful replication is expected at least this often"
  }
  column {
    name     = "STALE_SEVERITY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "ENABLED"
    type     = "BOOLEAN"
    not_null = "true"
  }
  column {
    name    = "SOURCE"
    type    = "STRING"
    comment = "Feeds file this row was synced from"
  }
  column {
    name     = "UPDATED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "reference_data_runs" {
  name     = "REFERENCE_DATA_RUNS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every replication run of a reference-data feed with its row counts: source rows, conflicts, inserted, updated, deleted, unchanged and the replica total. Written by the rendered replication procedures. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/reference_data_runs/"

  column {
    name     = "RUN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "FEED_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "DOMAIN"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "STARTED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }
  column {
    name = "FINISHED_AT"
    type = "TIMESTAMP_NTZ(6)"
  }
  column {
    name     = "STATUS"
    type     = "STRING"
    not_null = "true"
    comment  = "succeeded, skipped (no new snapshot files) or failed"
  }
  column {
    name    = "SNAPSHOT_DATE"
    type    = "DATE"
    comment = "Business date the snapshot describes"
  }
  column {
    name    = "FILES_LOADED"
    type    = "NUMBER(18,0)"
    comment = "Snapshot files read in this run"
  }
  column {
    name    = "ROWS_SOURCE"
    type    = "NUMBER(18,0)"
    comment = "Rows in the snapshot"
  }
  column {
    name    = "ROWS_CONFLICT"
    type    = "NUMBER(18,0)"
    comment = "Snapshot rows left out because their key was blank or repeated"
  }
  column {
    name = "ROWS_INSERTED"
    type = "NUMBER(18,0)"
  }
  column {
    name = "ROWS_UPDATED"
    type = "NUMBER(18,0)"
  }
  column {
    name = "ROWS_DELETED"
    type = "NUMBER(18,0)"
  }
  column {
    name = "ROWS_UNCHANGED"
    type = "NUMBER(18,0)"
  }
  column {
    name    = "ROWS_TOTAL"
    type    = "NUMBER(18,0)"
    comment = "Rows in the replica after the run"
  }
  column {
    name    = "ERROR"
    type    = "STRING"
    comment = "The database's message when the run failed"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

# --- Detection ------------------------------------------------------------

resource "snowflake_procedure_sql" "detect_stale_reference_data" {
  name     = "DETECT_STALE_REFERENCE_DATA"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Raises one 'reference_stale' alert per feed and day when no replication has succeeded within the feed's expected interval. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.detect_stale_reference_data_definition

  depends_on = [snowflake_iceberg_table.alerts, snowflake_iceberg_table.reference_feeds, snowflake_iceberg_table.reference_data_runs]
}
