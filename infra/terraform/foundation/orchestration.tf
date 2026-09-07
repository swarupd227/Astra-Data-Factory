# Per-custodian orchestration (S3.2.6, ADR 0024).
#
# Every custodian with a delivery block gets one Tasks DAG, rendered by
# astra-data for each of its sources: a root task <CUSTODIAN>_GATE that runs
# every minute and calls CUSTODIAN_GATE, and one child task per source that
# runs the source's process procedure when the gate says 'run'. The gate
# says 'run' for a business date when every expected file pattern of the
# custodian has a loaded file for that date and a file arrived since the
# last run for that date; so the DAG starts within a minute of the last
# expected file, and a late or re-delivered file starts it again. Every
# start is a row of CUSTODIAN_RUNS. A custodian's DAG shares nothing with
# another's but these two control objects, which it only inserts into.

locals {
  custodian_runs_fqn = "${local.control_fqn}.\"CUSTODIAN_RUNS\""

  # How far back the gate looks for business dates worth running. A file for
  # an older date is processed by the source's merge in arrival order but
  # does not start the DAG on its own.
  gate_lookback_days = 7

  custodian_gate_definition = <<-SQL
    DECLARE
      started INTEGER DEFAULT 0;
    BEGIN
      -- 1. Business dates of the last ${local.gate_lookback_days} days whose expected file set is complete and whose newest arrival
      --    is later than the newest arrival the last run for that date saw. A file's business date is its
      --    last-modified date in the custodian's timezone, as the late detector counts it.
      INSERT INTO ${local.custodian_runs_fqn} (RUN_ID, CUSTODIAN_ID, BUSINESS_DATE, STARTED_AT, REASON, FILES, LATEST_ARRIVAL_AT, AFTER_CUTOFF, LATE_FILES)
      WITH c AS (
        SELECT CUSTODIAN_ID, TIMEZONE, CUTOFF_TIME
        FROM ${local.custodians_fqn}
        WHERE CUSTODIAN_ID = :CUSTODIAN_ID AND ENABLED
      ),
      expected AS (
        SELECT COUNT(*) AS PATTERNS FROM ${local.custodian_files_fqn} WHERE CUSTODIAN_ID = :CUSTODIAN_ID
      ),
      arrivals AS (
        SELECT f.FILE_PATTERN, l.FILE_NAME, l.FILE_LAST_MODIFIED, l.OBSERVED_AT,
               CONVERT_TIMEZONE('UTC', c.TIMEZONE, l.FILE_LAST_MODIFIED)::DATE AS BUSINESS_DATE
        FROM c
        JOIN ${local.custodian_files_fqn} f ON f.CUSTODIAN_ID = c.CUSTODIAN_ID
        JOIN ${local.file_load_log_fqn} l ON l.FILE_NAME LIKE f.FILE_PATTERN AND l.STATUS = 'LOADED'
        WHERE l.FILE_LAST_MODIFIED >= DATEADD('day', -${local.gate_lookback_days}, SYSDATE())
      ),
      by_date AS (
        SELECT a.BUSINESS_DATE,
               COUNT(DISTINCT a.FILE_PATTERN) AS PATTERNS,
               COUNT(DISTINCT a.FILE_NAME) AS FILES,
               MAX(a.OBSERVED_AT) AS LATEST_ARRIVAL_AT,
               CONVERT_TIMEZONE(c.TIMEZONE, 'UTC', (a.BUSINESS_DATE::STRING || ' ' || c.CUTOFF_TIME)::TIMESTAMP_NTZ) AS CUTOFF_UTC
        FROM arrivals a CROSS JOIN c
        GROUP BY a.BUSINESS_DATE, c.TIMEZONE, c.CUTOFF_TIME
      ),
      complete AS (
        SELECT d.*,
               (SELECT MAX(r.LATEST_ARRIVAL_AT) FROM ${local.custodian_runs_fqn} r
                 WHERE r.CUSTODIAN_ID = :CUSTODIAN_ID AND r.BUSINESS_DATE = d.BUSINESS_DATE) AS LAST_RUN_SAW,
               (SELECT LISTAGG(a.FILE_NAME, ', ') WITHIN GROUP (ORDER BY a.FILE_NAME) FROM arrivals a
                 WHERE a.BUSINESS_DATE = d.BUSINESS_DATE AND a.FILE_LAST_MODIFIED >= d.CUTOFF_UTC) AS LATE_FILES
        FROM by_date d CROSS JOIN expected e
        WHERE e.PATTERNS > 0 AND d.PATTERNS = e.PATTERNS
      )
      SELECT UUID_STRING(),
             :CUSTODIAN_ID,
             BUSINESS_DATE,
             SYSDATE(),
             CASE WHEN LAST_RUN_SAW IS NOT NULL THEN 'redelivery'
                  WHEN LATE_FILES IS NOT NULL THEN 'late_arrival'
                  ELSE 'complete' END,
             FILES,
             LATEST_ARRIVAL_AT,
             SYSDATE() >= CUTOFF_UTC,
             LATE_FILES
      FROM complete
      WHERE LAST_RUN_SAW IS NULL OR LATEST_ARRIVAL_AT > LAST_RUN_SAW;
      started := SQLROWCOUNT;

      -- 2. A file set completed by a file that came after the cutoff is worth telling operations about once: the late
      --    alert said what was missing, this says it arrived and the run started.
      INSERT INTO ${local.alerts_fqn} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE)
      SELECT UUID_STRING(),
             SYSDATE(),
             'custodian_late_arrival',
             'info',
             r.CUSTODIAN_ID,
             c.NAME || ': late file(s) for ' || r.BUSINESS_DATE::STRING || ' arrived; processing started',
             c.NAME || ' (' || r.CUSTODIAN_ID || ') completed its expected file set for ' || r.BUSINESS_DATE::STRING
               || ' after the cutoff. Late: ' || r.LATE_FILES || '. Run ' || r.RUN_ID || ' started at '
               || r.STARTED_AT::TIMESTAMP_NTZ(0)::STRING || ' UTC.',
             'late_arrival:' || r.CUSTODIAN_ID || ':' || r.BUSINESS_DATE::STRING || ':' || r.RUN_ID,
             r.BUSINESS_DATE
      FROM ${local.custodian_runs_fqn} r
      JOIN ${local.custodians_fqn} c ON c.CUSTODIAN_ID = r.CUSTODIAN_ID
      WHERE r.CUSTODIAN_ID = :CUSTODIAN_ID
        AND r.REASON IN ('late_arrival', 'redelivery')
        AND r.LATE_FILES IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM ${local.alerts_fqn} a
          WHERE a.SOURCE_KEY = 'late_arrival:' || r.CUSTODIAN_ID || ':' || r.BUSINESS_DATE::STRING || ':' || r.RUN_ID);

      -- 3. The child tasks run only when the gate says so.
      CALL SYSTEM$SET_RETURN_VALUE(IFF(:started > 0, 'run', 'wait'));
      RETURN IFF(started > 0, 'run', 'wait');
    END;
  SQL
}

resource "snowflake_iceberg_table" "custodian_runs" {
  name     = "CUSTODIAN_RUNS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every start of a custodian's Tasks DAG: the business date whose expected file set was complete, why it started (complete, late_arrival, redelivery) and the arrival it saw. Written by CUSTODIAN_GATE. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/custodian_runs/"

  column {
    name     = "RUN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "CUSTODIAN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "BUSINESS_DATE"
    type     = "DATE"
    not_null = "true"
    comment  = "Business date whose file set was complete: the files' last-modified date in the custodian's timezone"
  }
  column {
    name     = "STARTED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }
  column {
    name     = "REASON"
    type     = "STRING"
    not_null = "true"
    comment  = "complete = first run, before the cutoff; late_arrival = first run, completed by a file after the cutoff; redelivery = a further file for a date already run"
  }
  column {
    name     = "FILES"
    type     = "NUMBER(18,0)"
    not_null = "true"
    comment  = "Loaded files of the business date that match an expected pattern"
  }
  column {
    name     = "LATEST_ARRIVAL_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
    comment  = "Newest file-load-log observation this run saw; a later one starts the DAG again"
  }
  column {
    name     = "AFTER_CUTOFF"
    type     = "BOOLEAN"
    not_null = "true"
  }
  column {
    name    = "LATE_FILES"
    type    = "STRING"
    comment = "Files of the business date last modified at or after the cutoff, when any"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_procedure_sql" "custodian_gate" {
  name     = "CUSTODIAN_GATE"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Root of a custodian's Tasks DAG: returns 'run' and records a CUSTODIAN_RUNS row when a business date's expected file set is complete and a file arrived since its last run; 'wait' otherwise. Managed by Terraform."

  return_type = "STRING"
  execute_as  = "OWNER"

  arguments {
    arg_name      = "CUSTODIAN_ID"
    arg_data_type = "STRING"
  }

  procedure_definition = local.custodian_gate_definition

  depends_on = [
    snowflake_iceberg_table.custodian_runs,
    snowflake_iceberg_table.custodians,
    snowflake_iceberg_table.custodian_files,
    snowflake_iceberg_table.file_load_log,
    snowflake_iceberg_table.alerts,
  ]
}
