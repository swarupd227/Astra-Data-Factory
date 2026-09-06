# ---------------------------------------------------------------------------
# Alerts to Slack, Jira and email (S1.2.4)
# ---------------------------------------------------------------------------
#
# Detection and dispatch both run inside Snowflake, serverless, every minute,
# so nobody has to watch a dashboard and no runner has to be alive:
#
#   DETECT_TASK_FAILURES    every failed task in the environment database
#                           (INFORMATION_SCHEMA.TASK_HISTORY) becomes an alert
#   DETECT_LATE_CUSTODIANS  a custodian whose cutoff has passed on a business
#                           day without all expected files becomes a 'late'
#                           alert listing the missing files
#   DISPATCH_ALERTS         sends every undelivered alert to the channels its
#                           severity routes to, through Snowflake notification
#                           integrations; records each attempt
#
# Severity per custodian, cutoff, timezone, business days and expected files
# come from the source configs (astra-data custodians sync) into
# CONTROL.CUSTODIANS and CONTROL.CUSTODIAN_FILES. Which severity goes to
# which channel is data in CONTROL.ALERT_ROUTES, seeded here from the
# channels configured for the environment.

locals {
  control_fqn = "\"${local.database_name}\".\"${local.control_schema}\""

  alerts_fqn           = "${local.control_fqn}.\"ALERTS\""
  alert_deliveries_fqn = "${local.control_fqn}.\"ALERT_DELIVERIES\""
  alert_routes_fqn     = "${local.control_fqn}.\"ALERT_ROUTES\""
  custodians_fqn       = "${local.control_fqn}.\"CUSTODIANS\""
  custodian_files_fqn  = "${local.control_fqn}.\"CUSTODIAN_FILES\""
  merge_log_fqn        = "${local.control_fqn}.\"MERGE_LOG\""

  # A custodian that has merged files but whose last full refresh of a scope
  # is older than its expectation gets one 'refresh_stale' alert per day.
  detect_stale_refreshes_definition = <<-SQL
    DECLARE
      raised INTEGER DEFAULT 0;
    BEGIN
      INSERT INTO ${local.alerts_fqn} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE)
      WITH scopes AS (
        SELECT m.CUSTODIAN_ID, m.SOURCE_ID, m.SCOPE,
               MAX(IFF(m.MODE = 'refresh', m.LOADED_AT, NULL)) AS LAST_REFRESH,
               MAX(m.LOADED_AT) AS LAST_LOAD
        FROM ${local.merge_log_fqn} m
        GROUP BY m.CUSTODIAN_ID, m.SOURCE_ID, m.SCOPE
      ),
      stale AS (
        SELECT s.CUSTODIAN_ID, s.SOURCE_ID, s.SCOPE, s.LAST_REFRESH, s.LAST_LOAD, c.NAME, c.LATE_SEVERITY, c.REFRESH_EXPECTED_DAYS
        FROM scopes s
        JOIN ${local.custodians_fqn} c ON c.CUSTODIAN_ID = s.CUSTODIAN_ID AND c.ENABLED AND c.REFRESH_EXPECTED_DAYS IS NOT NULL
        WHERE s.LAST_REFRESH IS NULL OR s.LAST_REFRESH < DATEADD('day', -c.REFRESH_EXPECTED_DAYS, SYSDATE())
      )
      SELECT UUID_STRING(),
             SYSDATE(),
             'refresh_stale',
             t.LATE_SEVERITY,
             t.CUSTODIAN_ID,
             t.NAME || ': no full refresh of ' || t.SOURCE_ID || ' (' || t.SCOPE || ') for over ' || t.REFRESH_EXPECTED_DAYS || ' days',
             t.NAME || ' (' || t.CUSTODIAN_ID || ') is expected to send a full refresh of ' || t.SOURCE_ID || ' for ' || t.SCOPE
               || ' at least every ' || t.REFRESH_EXPECTED_DAYS || ' days. Last refresh: '
               || COALESCE(t.LAST_REFRESH::TIMESTAMP_NTZ(0)::STRING || ' UTC', 'never') || '. Last file of any kind: '
               || t.LAST_LOAD::TIMESTAMP_NTZ(0)::STRING || ' UTC. Silver for this scope may be missing retired positions.',
             'refresh:' || t.CUSTODIAN_ID || ':' || t.SOURCE_ID || ':' || t.SCOPE || ':' || CURRENT_DATE()::STRING,
             CURRENT_DATE()
      FROM stale t
      WHERE NOT EXISTS (
        SELECT 1 FROM ${local.alerts_fqn} a
        WHERE a.SOURCE_KEY = 'refresh:' || t.CUSTODIAN_ID || ':' || t.SOURCE_ID || ':' || t.SCOPE || ':' || CURRENT_DATE()::STRING);
      raised := SQLROWCOUNT;
      RETURN raised;
    END;
  SQL

  email_integration_name = "${local.name_prefix}_ALERT_EMAIL"
  slack_integration_name = "${local.name_prefix}_ALERT_SLACK"
  jira_integration_name  = "${local.name_prefix}_ALERT_JIRA"

  email_enabled = length(var.alert_email_recipients) > 0
  # Whether the secret is set is not itself secret; count and for_each need a plain value.
  slack_enabled = nonsensitive(var.slack_webhook_secret != null)
  jira_enabled  = var.jira != null

  # Which severities reach which channel. Only configured channels are seeded.
  severity_channels = {
    critical = ["slack", "jira", "email"]
    error    = ["slack", "jira", "email"]
    warning  = ["slack", "email"]
    info     = ["slack"]
  }
  channel_enabled = {
    slack = local.slack_enabled
    jira  = local.jira_enabled
    email = local.email_enabled
  }
  channel_integration = {
    slack = local.slack_integration_name
    jira  = local.jira_integration_name
    email = local.email_integration_name
  }
  alert_routes = merge([
    for severity, channels in local.severity_channels : {
      for channel in channels : "${severity}.${channel}" => {
        severity    = severity
        channel     = channel
        integration = local.channel_integration[channel]
        recipients  = channel == "email" ? join(",", var.alert_email_recipients) : ""
      } if local.channel_enabled[channel]
    }
  ]...)

  jira_project_key = try(var.jira.project_key, "")
  jira_issue_type  = try(var.jira.issue_type, "Task")

  detect_task_failures_definition = <<-SQL
    DECLARE
      raised INTEGER DEFAULT 0;
    BEGIN
      INSERT INTO ${local.alerts_fqn} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE)
      SELECT UUID_STRING(),
             SYSDATE(),
             'task_failed',
             COALESCE(c.FAILURE_SEVERITY, 'error'),
             c.CUSTODIAN_ID,
             'Task ' || h.SCHEMA_NAME || '.' || h.NAME || ' failed',
             'Task "${local.database_name}"."' || h.SCHEMA_NAME || '"."' || h.NAME || '" failed at '
               || CONVERT_TIMEZONE('UTC', h.COMPLETED_TIME)::TIMESTAMP_NTZ(0)::STRING || ' UTC. Error '
               || COALESCE(h.ERROR_CODE::STRING, 'unknown') || ': ' || COALESCE(h.ERROR_MESSAGE, 'no message')
               || '. Query id ' || h.QUERY_ID || '.',
             'task:' || h.QUERY_ID,
             CURRENT_DATE()
      FROM TABLE("${local.database_name}".INFORMATION_SCHEMA.TASK_HISTORY(
             SCHEDULED_TIME_RANGE_START => DATEADD('hour', -${var.alert_lookback_hours}, CURRENT_TIMESTAMP()),
             RESULT_LIMIT => 1000)) h
      LEFT JOIN ${local.custodians_fqn} c
        ON c.ENABLED AND STARTSWITH(h.NAME, UPPER(c.CUSTODIAN_ID) || '_')
      WHERE h.STATE = 'FAILED'
        AND NOT EXISTS (SELECT 1 FROM ${local.alerts_fqn} a WHERE a.SOURCE_KEY = 'task:' || h.QUERY_ID);
      raised := SQLROWCOUNT;
      RETURN raised;
    END;
  SQL

  detect_late_custodians_definition = <<-SQL
    DECLARE
      raised INTEGER DEFAULT 0;
    BEGIN
      INSERT INTO ${local.alerts_fqn} (ALERT_ID, RAISED_AT, KIND, SEVERITY, CUSTODIAN_ID, TITLE, BODY, SOURCE_KEY, BUSINESS_DATE)
      WITH due AS (
        SELECT c.CUSTODIAN_ID, c.NAME, c.LATE_SEVERITY, c.TIMEZONE, c.BUSINESS_DAYS,
               CONVERT_TIMEZONE(c.TIMEZONE, CURRENT_TIMESTAMP())::DATE AS BUSINESS_DATE,
               UPPER(DAYNAME(CONVERT_TIMEZONE(c.TIMEZONE, CURRENT_TIMESTAMP()))) AS DAY_NAME,
               CONVERT_TIMEZONE(c.TIMEZONE, 'UTC',
                 (CONVERT_TIMEZONE(c.TIMEZONE, CURRENT_TIMESTAMP())::DATE::STRING || ' ' || c.CUTOFF_TIME)::TIMESTAMP_NTZ) AS CUTOFF_UTC
        FROM ${local.custodians_fqn} c
        WHERE c.ENABLED
      ),
      past_cutoff AS (
        SELECT * FROM due
        WHERE SYSDATE() >= CUTOFF_UTC AND POSITION(DAY_NAME IN BUSINESS_DAYS) > 0
      ),
      missing AS (
        SELECT p.CUSTODIAN_ID, p.NAME, p.LATE_SEVERITY, p.TIMEZONE, p.BUSINESS_DATE, p.CUTOFF_UTC,
               LISTAGG(f.FILE_PATTERN, ', ') WITHIN GROUP (ORDER BY f.FILE_PATTERN) AS MISSING_FILES,
               COUNT(*) AS MISSING_COUNT
        FROM past_cutoff p
        JOIN ${local.custodian_files_fqn} f ON f.CUSTODIAN_ID = p.CUSTODIAN_ID
        WHERE NOT EXISTS (
          SELECT 1 FROM ${local.file_load_log_fqn} l
          WHERE l.FILE_NAME LIKE f.FILE_PATTERN
            AND CONVERT_TIMEZONE('UTC', p.TIMEZONE, l.FILE_LAST_MODIFIED)::DATE = p.BUSINESS_DATE)
        GROUP BY p.CUSTODIAN_ID, p.NAME, p.LATE_SEVERITY, p.TIMEZONE, p.BUSINESS_DATE, p.CUTOFF_UTC
      )
      SELECT UUID_STRING(),
             SYSDATE(),
             'custodian_late',
             m.LATE_SEVERITY,
             m.CUSTODIAN_ID,
             m.NAME || ' is late: ' || m.MISSING_COUNT || ' expected file(s) missing after cutoff',
             m.NAME || ' (' || m.CUSTODIAN_ID || ') has not delivered ' || m.MISSING_COUNT || ' expected file(s) for '
               || m.BUSINESS_DATE::STRING || ' by the cutoff ' || m.CUTOFF_UTC::TIMESTAMP_NTZ(0)::STRING || ' UTC. Missing: '
               || m.MISSING_FILES || '.',
             'late:' || m.CUSTODIAN_ID || ':' || m.BUSINESS_DATE::STRING,
             m.BUSINESS_DATE
      FROM missing m
      WHERE NOT EXISTS (
        SELECT 1 FROM ${local.alerts_fqn} a
        WHERE a.SOURCE_KEY = 'late:' || m.CUSTODIAN_ID || ':' || m.BUSINESS_DATE::STRING);
      raised := SQLROWCOUNT;
      RETURN raised;
    END;
  SQL

  dispatch_alerts_definition = <<-SQL
    DECLARE
      sent INTEGER DEFAULT 0;
    BEGIN
      LET pending RESULTSET := (
        SELECT a.ALERT_ID, a.SEVERITY, a.KIND, a.CUSTODIAN_ID, a.TITLE, a.BODY,
               r.CHANNEL, r.INTEGRATION_NAME, r.RECIPIENTS
        FROM ${local.alerts_fqn} a
        JOIN ${local.alert_routes_fqn} r ON r.SEVERITY = a.SEVERITY AND r.ENABLED
        WHERE a.RAISED_AT >= DATEADD('day', -1, SYSDATE())
          AND NOT EXISTS (
            SELECT 1 FROM ${local.alert_deliveries_fqn} d
            WHERE d.ALERT_ID = a.ALERT_ID AND d.CHANNEL = r.CHANNEL AND d.STATUS = 'sent')
          AND (SELECT COUNT(*) FROM ${local.alert_deliveries_fqn} d
               WHERE d.ALERT_ID = a.ALERT_ID AND d.CHANNEL = r.CHANNEL) < ${var.alert_delivery_attempts}
        ORDER BY a.RAISED_AT
      );
      LET c CURSOR FOR pending;
      FOR rec IN c DO
        LET alert_id STRING := rec.ALERT_ID;
        LET channel STRING := rec.CHANNEL;
        LET integration STRING := rec.INTEGRATION_NAME;
        LET recipients STRING := rec.RECIPIENTS;
        LET subject STRING := '[Astra ${var.environment}] ' || UPPER(rec.SEVERITY) || ': ' || rec.TITLE;
        LET text STRING := subject || '\n\n' || rec.BODY || '\n\nAlert ' || rec.ALERT_ID || ' from Astra Data Factory ${var.environment}.';
        LET issue STRING := OBJECT_CONSTRUCT(
          'fields', OBJECT_CONSTRUCT(
            'project', OBJECT_CONSTRUCT('key', '${local.jira_project_key}'),
            'issuetype', OBJECT_CONSTRUCT('name', '${local.jira_issue_type}'),
            'summary', subject,
            'description', OBJECT_CONSTRUCT('type', 'doc', 'version', 1, 'content', ARRAY_CONSTRUCT(
              OBJECT_CONSTRUCT('type', 'paragraph', 'content', ARRAY_CONSTRUCT(
                OBJECT_CONSTRUCT('type', 'text', 'text', rec.BODY))))),
            'labels', ARRAY_CONSTRUCT('astra-data-factory', rec.KIND, COALESCE(rec.CUSTODIAN_ID, 'platform'), rec.SEVERITY)))::STRING;
        LET status STRING := 'sent';
        LET detail STRING := '';
        BEGIN
          IF (channel = 'email') THEN
            CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(
              SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(:text),
              SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG(:integration, :subject, SPLIT(REPLACE(:recipients, ' ', ''), ',')));
          ELSEIF (channel = 'slack') THEN
            CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(
              SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(:text)),
              SNOWFLAKE.NOTIFICATION.INTEGRATION(:integration));
          ELSEIF (channel = 'jira') THEN
            CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(
              SNOWFLAKE.NOTIFICATION.APPLICATION_JSON(:issue),
              SNOWFLAKE.NOTIFICATION.INTEGRATION(:integration));
          ELSE
            status := 'failed';
            detail := 'unknown channel ' || channel;
          END IF;
        EXCEPTION
          WHEN OTHER THEN
            status := 'failed';
            detail := SQLERRM;
        END;
        INSERT INTO ${local.alert_deliveries_fqn} (ALERT_ID, CHANNEL, INTEGRATION_NAME, STATUS, DETAIL, ATTEMPTED_AT)
          VALUES (:alert_id, :channel, :integration, :status, :detail, SYSDATE());
        IF (status = 'sent') THEN
          sent := sent + 1;
        END IF;
      END FOR;
      RETURN sent;
    END;
  SQL

  run_alerting_definition = <<-SQL
    BEGIN
      CALL ${local.control_fqn}."DETECT_TASK_FAILURES"();
      CALL ${local.control_fqn}."DETECT_LATE_CUSTODIANS"();
      CALL ${local.control_fqn}."DETECT_STALE_REFRESHES"();
      CALL ${local.control_fqn}."DETECT_STALE_REFERENCE_DATA"();
      CALL ${local.control_fqn}."DISPATCH_ALERTS"();
      RETURN 'ok';
    END;
  SQL
}

# --- Tables ---------------------------------------------------------------

resource "snowflake_iceberg_table" "alerts" {
  name     = "ALERTS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every alert raised: failed tasks and late custodians. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/alerts/"

  column {
    name     = "ALERT_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "RAISED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }
  column {
    name     = "KIND"
    type     = "STRING"
    not_null = "true"
    comment  = "task_failed or custodian_late"
  }
  column {
    name     = "SEVERITY"
    type     = "STRING"
    not_null = "true"
    comment  = "info, warning, error or critical"
  }
  column {
    name    = "CUSTODIAN_ID"
    type    = "STRING"
    comment = "Null for platform tasks"
  }
  column {
    name     = "TITLE"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "BODY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "SOURCE_KEY"
    type     = "STRING"
    not_null = "true"
    comment  = "Deduplication key: task:<query id> or late:<custodian>:<business date>"
  }
  column {
    name = "BUSINESS_DATE"
    type = "DATE"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "alert_deliveries" {
  name     = "ALERT_DELIVERIES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every delivery attempt of an alert to a channel. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/alert_deliveries/"

  column {
    name     = "ALERT_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "CHANNEL"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "INTEGRATION_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "STATUS"
    type     = "STRING"
    not_null = "true"
    comment  = "sent or failed"
  }
  column {
    name = "DETAIL"
    type = "STRING"
  }
  column {
    name     = "ATTEMPTED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "alert_routes" {
  name     = "ALERT_ROUTES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Which severity goes to which channel. Seeded by Terraform from the configured channels; editable by operations. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/alert_routes/"

  column {
    name     = "SEVERITY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "CHANNEL"
    type     = "STRING"
    not_null = "true"
    comment  = "slack, jira or email"
  }
  column {
    name     = "INTEGRATION_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name    = "RECIPIENTS"
    type    = "STRING"
    comment = "Comma-separated email addresses; email channel only"
  }
  column {
    name     = "ENABLED"
    type     = "BOOLEAN"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "custodians" {
  name     = "CUSTODIANS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Delivery expectations and alert severities per custodian, synced from the source configs by astra-data. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/custodians/"

  column {
    name     = "CUSTODIAN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "CUTOFF_TIME"
    type     = "STRING"
    not_null = "true"
    comment  = "HH:MM in the custodian's timezone"
  }
  column {
    name     = "TIMEZONE"
    type     = "STRING"
    not_null = "true"
    comment  = "IANA name, for example America/New_York"
  }
  column {
    name     = "BUSINESS_DAYS"
    type     = "STRING"
    not_null = "true"
    comment  = "Comma-separated upper-case day names, for example MON,TUE,WED,THU,FRI"
  }
  column {
    name     = "LATE_SEVERITY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "FAILURE_SEVERITY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name    = "REFRESH_EXPECTED_DAYS"
    type    = "NUMBER(5,0)"
    comment = "A full refresh is expected at least this often; null means no expectation"
  }
  column {
    name     = "ENABLED"
    type     = "BOOLEAN"
    not_null = "true"
  }
  column {
    name    = "SOURCE"
    type    = "STRING"
    comment = "Config files this row was synced from"
  }
  column {
    name     = "UPDATED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "custodian_files" {
  name     = "CUSTODIAN_FILES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Files expected from each custodian every business day, as LIKE patterns relative to the landing prefix. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/custodian_files/"

  column {
    name     = "CUSTODIAN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "FILE_PATTERN"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name = "DESCRIPTION"
    type = "STRING"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "merge_log" {
  name     = "MERGE_LOG"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every file merged into Silver: which custodian, source and scope, the business date, whether it was a full refresh or an update, and the row counts. Written by rendered pipelines; read by the stale-refresh detector. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/merge_log/"

  column {
    name     = "CUSTODIAN_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "SOURCE_ID"
    type     = "STRING"
    not_null = "true"
    comment  = "Source id of the config the pipeline was rendered from"
  }
  column {
    name     = "SCOPE"
    type     = "STRING"
    not_null = "true"
    comment  = "Partition a refresh replaces, for example remote_id=RMT0000001, or 'all'"
  }
  column {
    name = "BUSINESS_DATE"
    type = "DATE"
  }
  column {
    name     = "MODE"
    type     = "STRING"
    not_null = "true"
    comment  = "refresh or update"
  }
  column {
    name     = "FILE_NAME"
    type     = "STRING"
    not_null = "true"
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
    name = "ROWS_CARRIED"
    type = "NUMBER(18,0)"
  }
  column {
    name = "ROWS_RETIRED"
    type = "NUMBER(18,0)"
  }
  column {
    name     = "LOADED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_procedure_sql" "detect_stale_refreshes" {
  name     = "DETECT_STALE_REFRESHES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Raises one 'refresh_stale' alert per day for each custodian, source and scope whose last full refresh is older than the custodian expects. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.detect_stale_refreshes_definition

  depends_on = [snowflake_iceberg_table.alerts, snowflake_iceberg_table.custodians, snowflake_iceberg_table.merge_log]
}

# --- Channels -------------------------------------------------------------

resource "snowflake_email_notification_integration" "alerts" {
  count = local.email_enabled ? 1 : 0

  name               = local.email_integration_name
  enabled            = true
  allowed_recipients = var.alert_email_recipients
  comment            = "Alert email for ${local.name_prefix}. Managed by Terraform."
}

resource "snowflake_secret_with_generic_string" "slack_webhook" {
  count = local.slack_enabled ? 1 : 0

  name          = "SLACK_WEBHOOK"
  database      = snowflake_database.this.name
  schema        = snowflake_schema.this[local.control_schema].name
  secret_string = var.slack_webhook_secret
  comment       = "Secret part of the Slack incoming webhook URL. Managed by Terraform."

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_execute" "slack_integration" {
  count = local.slack_enabled ? 1 : 0

  execute = join(" ", [
    "CREATE OR REPLACE NOTIFICATION INTEGRATION ${local.slack_integration_name}",
    "TYPE = WEBHOOK ENABLED = TRUE",
    "WEBHOOK_URL = 'https://hooks.slack.com/services/SNOWFLAKE_WEBHOOK_SECRET'",
    "WEBHOOK_SECRET = ${local.control_fqn}.\"SLACK_WEBHOOK\"",
    "WEBHOOK_BODY_TEMPLATE = '{\"text\": \"SNOWFLAKE_WEBHOOK_MESSAGE\"}'",
    "WEBHOOK_HEADERS = ('Content-Type' = 'application/json')",
    "COMMENT = 'Alert Slack channel for ${local.name_prefix}. Managed by Terraform.'",
  ])
  revert = "DROP NOTIFICATION INTEGRATION IF EXISTS ${local.slack_integration_name}"
  query  = "SHOW NOTIFICATION INTEGRATIONS LIKE '${local.slack_integration_name}'"

  depends_on = [snowflake_secret_with_generic_string.slack_webhook]
}

resource "snowflake_secret_with_generic_string" "jira_auth" {
  count = local.jira_enabled ? 1 : 0

  name          = "JIRA_AUTH"
  database      = snowflake_database.this.name
  schema        = snowflake_schema.this[local.control_schema].name
  secret_string = base64encode("${var.jira.user_email}:${var.jira_api_token}")
  comment       = "Basic authentication for the Jira REST API. Managed by Terraform."

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_execute" "jira_integration" {
  count = local.jira_enabled ? 1 : 0

  execute = join(" ", [
    "CREATE OR REPLACE NOTIFICATION INTEGRATION ${local.jira_integration_name}",
    "TYPE = WEBHOOK ENABLED = TRUE",
    "WEBHOOK_URL = '${trimsuffix(var.jira.site_url, "/")}/rest/api/3/issue'",
    "WEBHOOK_SECRET = ${local.control_fqn}.\"JIRA_AUTH\"",
    "WEBHOOK_BODY_TEMPLATE = 'SNOWFLAKE_WEBHOOK_MESSAGE'",
    "WEBHOOK_HEADERS = ('Content-Type' = 'application/json', 'Authorization' = 'Basic SNOWFLAKE_WEBHOOK_SECRET')",
    "COMMENT = 'Alert Jira issues for ${local.name_prefix}. Managed by Terraform.'",
  ])
  revert = "DROP NOTIFICATION INTEGRATION IF EXISTS ${local.jira_integration_name}"
  query  = "SHOW NOTIFICATION INTEGRATIONS LIKE '${local.jira_integration_name}'"

  depends_on = [snowflake_secret_with_generic_string.jira_auth]
}

# Routes are seeded once per severity and channel; operations may change
# ENABLED and RECIPIENTS afterwards without Terraform reverting them.
resource "snowflake_execute" "alert_route" {
  for_each = local.alert_routes

  execute = join(" ", [
    "MERGE INTO ${local.alert_routes_fqn} r",
    "USING (SELECT '${each.value.severity}' AS SEVERITY, '${each.value.channel}' AS CHANNEL) s",
    "ON r.SEVERITY = s.SEVERITY AND r.CHANNEL = s.CHANNEL",
    "WHEN NOT MATCHED THEN INSERT (SEVERITY, CHANNEL, INTEGRATION_NAME, RECIPIENTS, ENABLED)",
    "VALUES ('${each.value.severity}', '${each.value.channel}', '${each.value.integration}', '${each.value.recipients}', TRUE)",
  ])
  revert = "DELETE FROM ${local.alert_routes_fqn} WHERE SEVERITY = '${each.value.severity}' AND CHANNEL = '${each.value.channel}'"

  depends_on = [snowflake_iceberg_table.alert_routes]
}

# --- Detection and dispatch -----------------------------------------------

resource "snowflake_procedure_sql" "detect_task_failures" {
  name     = "DETECT_TASK_FAILURES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Raises an alert for every failed task in the last ${var.alert_lookback_hours} hours that has none yet. Severity from the custodian the task belongs to. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.detect_task_failures_definition

  depends_on = [snowflake_iceberg_table.alerts, snowflake_iceberg_table.custodians]
}

resource "snowflake_procedure_sql" "detect_late_custodians" {
  name     = "DETECT_LATE_CUSTODIANS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Raises one 'late' alert per custodian and business day when expected files are missing after the cutoff, listing them. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.detect_late_custodians_definition

  depends_on = [snowflake_iceberg_table.alerts, snowflake_iceberg_table.custodians, snowflake_iceberg_table.custodian_files, snowflake_iceberg_table.file_load_log]
}

resource "snowflake_procedure_sql" "dispatch_alerts" {
  name     = "DISPATCH_ALERTS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Sends undelivered alerts to the channels their severity routes to and records every attempt. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.dispatch_alerts_definition

  depends_on = [snowflake_iceberg_table.alerts, snowflake_iceberg_table.alert_routes, snowflake_iceberg_table.alert_deliveries]
}

resource "snowflake_procedure_sql" "run_alerting" {
  name     = "RUN_ALERTING"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Detects failed tasks, late custodians, stale refreshes and stale reference data, then dispatches. Managed by Terraform."

  return_type          = "STRING"
  execute_as           = "OWNER"
  procedure_definition = local.run_alerting_definition

  depends_on = [
    snowflake_procedure_sql.detect_task_failures,
    snowflake_procedure_sql.detect_late_custodians,
    snowflake_procedure_sql.detect_stale_refreshes,
    snowflake_procedure_sql.detect_stale_reference_data,
    snowflake_procedure_sql.dispatch_alerts,
  ]
}

resource "snowflake_task" "raise_alerts" {
  name     = "RAISE_ALERTS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Runs alert detection and dispatch every ${var.alert_interval_minutes} minute(s). Serverless. Managed by Terraform."

  started                                  = true
  user_task_managed_initial_warehouse_size = "XSMALL"
  suspend_task_after_num_failures          = 30
  sql_statement                            = "CALL ${local.control_fqn}.\"RUN_ALERTING\"()"

  schedule {
    minutes = var.alert_interval_minutes
  }

  depends_on = [snowflake_procedure_sql.run_alerting]
}
