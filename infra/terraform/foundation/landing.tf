# ---------------------------------------------------------------------------
# Snowflake: landing zone to Bronze raw lines on S3 events (S1.1.3)
# ---------------------------------------------------------------------------
#
# A file dropped under the landing prefix becomes rows in BRONZE.RAW_LINES
# through an auto-ingest pipe: one row per line, with the file name, the row
# number and the ingest timestamp. Per-custodian pipes rendered later by the
# generation plane reuse the same integration, stage and event wiring.
#
# Every object here depends on the future grants so that it is created after
# them and therefore covered by them.

locals {
  storage_integration_name = "${local.name_prefix}_LANDING"
  bronze_schema            = "BRONZE"
  control_schema           = "CONTROL"

  raw_lines_format_fqn = "\"${local.database_name}\".\"${local.bronze_schema}\".\"RAW_LINES\""
  landing_stage_fqn    = "\"${local.database_name}\".\"${local.bronze_schema}\".\"LANDING\""
  raw_lines_table_fqn  = "\"${local.database_name}\".\"${local.bronze_schema}\".\"RAW_LINES\""
  file_load_log_fqn    = "\"${local.database_name}\".\"${local.control_schema}\".\"FILE_LOAD_LOG\""

  # The catch-all pipe watches the whole landing prefix, or, once per-source
  # pipes are rendered and the catch-all is turned off, a folder nothing is
  # ever delivered to. The pipe object stays because its notification channel
  # is the SQS queue the bucket notification targets, shared by every pipe.
  raw_lines_copy_from = var.landing_catch_all_pipe ? "@${local.landing_stage_fqn}" : "@${local.landing_stage_fqn}/_none/"

  # Snowpipe COPY: one row per line with the file metadata the story requires.
  raw_lines_copy = <<-SQL
    COPY INTO ${local.raw_lines_table_fqn} (FILE_NAME, ROW_NUMBER, LINE, FILE_CONTENT_KEY, FILE_LAST_MODIFIED, INGESTED_AT)
    FROM (
      SELECT METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, $1, METADATA$FILE_CONTENT_KEY,
             METADATA$FILE_LAST_MODIFIED, METADATA$START_SCAN_TIME
      FROM ${local.raw_lines_copy_from}
    )
    FILE_FORMAT = (FORMAT_NAME = '${local.raw_lines_format_fqn}')
  SQL

  # Reconciles what landed with what loaded. Snowpipe silently ignores a file
  # name it has already seen within 14 days; this statement makes that visible:
  #   LOADED     Snowpipe loaded the file (from COPY_HISTORY)
  #   FAILED     Snowpipe reported a load failure
  #   DUPLICATE  the same name arrived again with the same content; not loaded
  #   CONFLICT   the same name arrived again with different content, or after
  #              a failed load; not loaded, needs a rename
  # Every raw-lines table Snowpipe loads: the foundation's RAW_LINES (the
  # catch-all, when enabled) and each rendered source's <SOURCE>_RAW_LINES
  # (S3.2.1). COPY_HISTORY is per table, so the procedure gathers it for each
  # before classifying every arrival.
  reconcile_file_loads_definition = <<-SQL
    DECLARE
      raw_tables CURSOR FOR
        SELECT TABLE_NAME
        FROM "${local.database_name}".INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '${local.bronze_schema}'
          AND (TABLE_NAME = 'RAW_LINES' OR ENDSWITH(TABLE_NAME, '_RAW_LINES'))
        ORDER BY TABLE_NAME;
      logged INTEGER DEFAULT 0;
    BEGIN
      CREATE OR REPLACE TEMPORARY TABLE RAW_LOADS (
        FILE_NAME STRING, STATUS STRING, ROW_COUNT NUMBER, FILE_SIZE NUMBER, FIRST_ERROR_MESSAGE STRING,
        LAST_LOAD_TIME TIMESTAMP_NTZ(6), PIPE_NAME STRING, TABLE_NAME STRING);
      FOR r IN raw_tables DO
        LET t STRING := r.TABLE_NAME;
        LET stmt STRING := 'INSERT INTO RAW_LOADS '
          || 'SELECT FILE_NAME, STATUS, ROW_COUNT, FILE_SIZE, FIRST_ERROR_MESSAGE, '
          || 'CONVERT_TIMEZONE(''UTC'', LAST_LOAD_TIME)::TIMESTAMP_NTZ(6), PIPE_NAME, ''' || t || ''' '
          || 'FROM TABLE("${local.database_name}".INFORMATION_SCHEMA.COPY_HISTORY('
          || 'TABLE_NAME => ''"${local.database_name}"."${local.bronze_schema}"."' || t || '"'', '
          || 'START_TIME => DATEADD(''hour'', -24, CURRENT_TIMESTAMP())))';
        EXECUTE IMMEDIATE :stmt;
      END FOR;

      INSERT INTO ${local.file_load_log_fqn} (FILE_NAME, FILE_LAST_MODIFIED, STATUS, FILE_HASH, FILE_SIZE, ROW_COUNT, DETAIL, OBSERVED_AT)
      WITH files AS (
        SELECT RELATIVE_PATH AS FILE_NAME,
               CONVERT_TIMEZONE('UTC', LAST_MODIFIED)::TIMESTAMP_NTZ(6) AS FILE_LAST_MODIFIED,
               MD5 AS FILE_HASH,
               SIZE AS FILE_SIZE
        FROM DIRECTORY(@${local.landing_stage_fqn})
      ),
      loads AS (
        SELECT FILE_NAME, STATUS, ROW_COUNT, FILE_SIZE, FIRST_ERROR_MESSAGE, LAST_LOAD_TIME, PIPE_NAME, TABLE_NAME
        FROM RAW_LOADS
      ),
    logged AS (
      SELECT FILE_NAME, FILE_LAST_MODIFIED, STATUS, FILE_HASH FROM ${local.file_load_log_fqn}
    ),
    new_loads AS (
      SELECT l.FILE_NAME,
             COALESCE(f.FILE_LAST_MODIFIED, l.LAST_LOAD_TIME) AS FILE_LAST_MODIFIED,
             IFF(l.STATUS = 'Loaded', 'LOADED', 'FAILED') AS STATUS,
             f.FILE_HASH,
             COALESCE(l.FILE_SIZE, f.FILE_SIZE) AS FILE_SIZE,
             l.ROW_COUNT,
             IFF(l.STATUS = 'Loaded',
                 'Loaded by pipe ' || COALESCE(l.PIPE_NAME, 'unknown') || ' into ${local.bronze_schema}.' || l.TABLE_NAME || '.',
                 'Pipe ' || COALESCE(l.PIPE_NAME, 'unknown') || ' reported ' || l.STATUS || ': ' || COALESCE(l.FIRST_ERROR_MESSAGE, 'no error message')) AS DETAIL
      FROM loads l
      LEFT JOIN files f ON f.FILE_NAME = l.FILE_NAME
      WHERE NOT EXISTS (
        SELECT 1 FROM logged g
        WHERE g.FILE_NAME = l.FILE_NAME
          AND g.STATUS IN ('LOADED', 'FAILED')
          AND g.FILE_LAST_MODIFIED = COALESCE(f.FILE_LAST_MODIFIED, l.LAST_LOAD_TIME))
    ),
    latest AS (
      SELECT FILE_NAME, MAX(FILE_LAST_MODIFIED) AS FILE_LAST_MODIFIED FROM logged GROUP BY FILE_NAME
    ),
    loaded AS (
      SELECT FILE_NAME, FILE_HASH, FILE_LAST_MODIFIED
      FROM logged
      WHERE STATUS = 'LOADED'
      QUALIFY ROW_NUMBER() OVER (PARTITION BY FILE_NAME ORDER BY FILE_LAST_MODIFIED DESC) = 1
    ),
    redrops AS (
      SELECT f.FILE_NAME,
             f.FILE_LAST_MODIFIED,
             CASE WHEN d.FILE_HASH IS NULL THEN 'CONFLICT'
                  WHEN d.FILE_HASH = f.FILE_HASH THEN 'DUPLICATE'
                  ELSE 'CONFLICT' END AS STATUS,
             f.FILE_HASH,
             f.FILE_SIZE,
             NULL AS ROW_COUNT,
             CASE WHEN d.FILE_HASH IS NULL THEN
                    'File name was seen before but never loaded. Snowpipe does not retry a file name within 14 days; rename the corrected file to load it.'
                  WHEN d.FILE_HASH = f.FILE_HASH THEN
                    'Same name and same content as the version modified at ' || d.FILE_LAST_MODIFIED::STRING || ' that was already loaded. Not loaded again.'
                  ELSE
                    'Same name but different content from the version modified at ' || d.FILE_LAST_MODIFIED::STRING || ' that was loaded. Snowpipe does not reload a modified file name within 14 days; rename the file to load it.'
             END AS DETAIL
      FROM files f
      JOIN latest t ON t.FILE_NAME = f.FILE_NAME AND f.FILE_LAST_MODIFIED > t.FILE_LAST_MODIFIED
      LEFT JOIN loaded d ON d.FILE_NAME = f.FILE_NAME
      WHERE NOT EXISTS (SELECT 1 FROM new_loads n WHERE n.FILE_NAME = f.FILE_NAME)
    )
    unclaimed AS (
      SELECT f.FILE_NAME,
             f.FILE_LAST_MODIFIED,
             'UNCLAIMED' AS STATUS,
             f.FILE_HASH,
             f.FILE_SIZE,
             NULL AS ROW_COUNT,
             'No pipe loaded this file within ${var.landing_unclaimed_minutes} minutes of arrival: no source config claims its path. Landed, not processed.' AS DETAIL
      FROM files f
      WHERE f.FILE_LAST_MODIFIED < DATEADD('minute', -${var.landing_unclaimed_minutes}, SYSDATE())
        AND NOT EXISTS (SELECT 1 FROM logged g WHERE g.FILE_NAME = f.FILE_NAME AND g.FILE_LAST_MODIFIED = f.FILE_LAST_MODIFIED)
        AND NOT EXISTS (SELECT 1 FROM new_loads n WHERE n.FILE_NAME = f.FILE_NAME)
        AND NOT EXISTS (SELECT 1 FROM redrops r WHERE r.FILE_NAME = f.FILE_NAME)
    )
    SELECT FILE_NAME, FILE_LAST_MODIFIED, STATUS, FILE_HASH, FILE_SIZE, ROW_COUNT, DETAIL, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ(6)
    FROM new_loads
    UNION ALL
    SELECT FILE_NAME, FILE_LAST_MODIFIED, STATUS, FILE_HASH, FILE_SIZE, ROW_COUNT, DETAIL, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ(6)
    FROM redrops
    UNION ALL
    SELECT FILE_NAME, FILE_LAST_MODIFIED, STATUS, FILE_HASH, FILE_SIZE, ROW_COUNT, DETAIL, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ(6)
    FROM unclaimed;
      logged := SQLROWCOUNT;
      RETURN logged;
    END;
  SQL
}

# --- Storage integration --------------------------------------------------

resource "snowflake_storage_integration_aws" "landing" {
  name    = local.storage_integration_name
  comment = "Read access to the ${local.name_prefix} landing zone for Snowpipe. Managed by Terraform."
  enabled = true

  storage_provider          = "S3"
  storage_aws_role_arn      = local.snowflake_landing_role_arn
  storage_allowed_locations = [local.landing_base_url, local.sandbox_base_url, local.reference_base_url]
}

resource "snowflake_grant_privileges_to_account_role" "storage_integration_usage" {
  for_each = toset(["ADMIN", "ENGINEER", "PIPELINE", "SANDBOX"])

  account_role_name = snowflake_account_role.this[each.key].name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_storage_integration_aws.landing.name
  }
}

# --- File format and stage ------------------------------------------------

# One column per line, nothing interpreted: no delimiter, no quoting, no
# escaping, no NULL substitution. Parsing is the next layer's job.
resource "snowflake_file_format_csv" "raw_lines" {
  name     = "RAW_LINES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.bronze_schema].name
  comment  = "One column per line, nothing interpreted. Managed by Terraform."

  field_delimiter                = "NONE"
  skip_header                    = 0
  skip_blank_lines               = "false"
  trim_space                     = "false"
  empty_field_as_null            = "false"
  error_on_column_count_mismatch = "false"
  replace_invalid_characters     = "false"
  escape                         = "NONE"
  escape_unenclosed_field        = "NONE"
  field_optionally_enclosed_by   = "NONE"
  null_if                        = []
  encoding                       = "UTF8"
  compression                    = "AUTO"

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_stage_external_s3" "landing" {
  name     = "LANDING"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.bronze_schema].name
  comment  = "Custodian landing zone ${local.landing_base_url}. Managed by Terraform."

  url                 = local.landing_base_url
  storage_integration = snowflake_storage_integration_aws.landing.name

  file_format {
    format_name = snowflake_file_format_csv.raw_lines.fully_qualified_name
  }

  # The directory table is what makes re-delivered files visible: Snowpipe
  # ignores them, the directory table shows the new version.
  directory {
    enable       = true
    auto_refresh = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

# --- Raw lines table and pipe ---------------------------------------------

resource "snowflake_iceberg_table" "raw_lines" {
  name     = "RAW_LINES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.bronze_schema].name
  comment  = "One row per line of every file landed. Written only by Snowpipe. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "bronze/raw_lines/"

  column {
    name     = "FILE_NAME"
    type     = "STRING"
    not_null = "true"
    comment  = "Path of the file relative to the landing stage"
  }
  column {
    name     = "ROW_NUMBER"
    type     = "NUMBER(18,0)"
    not_null = "true"
    comment  = "1-based line number within the file"
  }
  column {
    name    = "LINE"
    type    = "STRING"
    comment = "The line as delivered, untouched"
  }
  column {
    name    = "FILE_CONTENT_KEY"
    type    = "STRING"
    comment = "Checksum of the file as reported by the stage"
  }
  column {
    name    = "FILE_LAST_MODIFIED"
    type    = "TIMESTAMP_NTZ(6)"
    comment = "Last-modified time of the file in S3"
  }
  column {
    name     = "INGESTED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
    comment  = "When Snowpipe started scanning the file"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

# The catch-all pipe loads every file under the landing prefix into RAW_LINES.
# It is what a fresh environment uses before any source is rendered; once the
# generation plane renders per-source pipes (S3.2.1), an environment turns it
# off (landing_catch_all_pipe = false) so a file is loaded once, by the pipe
# of the source that claims it. The pipe object remains, pointed at a folder
# nothing is delivered to, because the bucket notification needs its queue.
resource "snowflake_pipe" "raw_lines" {
  name     = "RAW_LINES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.bronze_schema].name
  comment  = var.landing_catch_all_pipe ? "Loads every file under ${local.landing_base_url} into RAW_LINES on arrival. Catch-all; turn off once per-source pipes are rendered. Managed by Terraform." : "Catch-all pipe turned off (landing_catch_all_pipe = false): watches ${local.landing_base_url}_none/, which receives nothing. Kept for its notification channel. Managed by Terraform."

  auto_ingest    = true
  copy_statement = local.raw_lines_copy

  depends_on = [
    snowflake_stage_external_s3.landing,
    snowflake_iceberg_table.raw_lines,
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}

# --- File load log and reconciliation -------------------------------------

resource "snowflake_iceberg_table" "file_load_log" {
  name     = "FILE_LOAD_LOG"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every file arrival in the landing zone and what happened to it: LOADED, FAILED, DUPLICATE or CONFLICT. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/file_load_log/"

  column {
    name     = "FILE_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "FILE_LAST_MODIFIED"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
    comment  = "Last-modified time of the S3 object version this entry is about"
  }
  column {
    name     = "STATUS"
    type     = "STRING"
    not_null = "true"
    comment  = "LOADED, FAILED, DUPLICATE or CONFLICT"
  }
  column {
    name    = "FILE_HASH"
    type    = "STRING"
    comment = "MD5 reported by the stage directory"
  }
  column {
    name = "FILE_SIZE"
    type = "NUMBER(18,0)"
  }
  column {
    name    = "ROW_COUNT"
    type    = "NUMBER(18,0)"
    comment = "Rows loaded, for LOADED entries"
  }
  column {
    name     = "DETAIL"
    type     = "STRING"
    not_null = "true"
    comment  = "What happened and, for CONFLICT, what to do"
  }
  column {
    name     = "OBSERVED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_procedure_sql" "reconcile_file_loads" {
  name     = "RECONCILE_FILE_LOADS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Compares the landing directory with the copy history of every raw-lines table and logs each arrival as LOADED, FAILED, DUPLICATE, CONFLICT or UNCLAIMED. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.reconcile_file_loads_definition

  depends_on = [
    snowflake_procedure_sql.reconcile_file_loads,
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}

resource "snowflake_task" "reconcile_file_loads" {
  name     = "RECONCILE_FILE_LOADS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Logs every landing-zone arrival as LOADED, FAILED, DUPLICATE, CONFLICT or UNCLAIMED. Serverless. Managed by Terraform."

  started                                  = true
  user_task_managed_initial_warehouse_size = "XSMALL"
  suspend_task_after_num_failures          = 10
  sql_statement                            = "CALL ${local.control_fqn}.\"RECONCILE_FILE_LOADS\"()"

  schedule {
    minutes = var.landing_reconcile_interval_minutes
  }

  depends_on = [
    snowflake_stage_external_s3.landing,
    snowflake_iceberg_table.raw_lines,
    snowflake_iceberg_table.file_load_log,
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}
