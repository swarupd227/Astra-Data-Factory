# Unit tests for alerts to Slack, Jira and email (S1.2.4).
#
# Mocked providers. Live delivery is checked by raising a test alert:
#   CALL "ASTRA_DEV"."CONTROL"."DISPATCH_ALERTS"() after inserting a row into ALERTS.

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

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

run "failed_tasks_become_alerts_with_custodian_severity" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "INFORMATION_SCHEMA.TASK_HISTORY",
        "DATEADD('hour', -2, CURRENT_TIMESTAMP())",
        "h.STATE = 'FAILED'",
        "COALESCE(c.FAILURE_SEVERITY, 'error')",
        "STARTSWITH(h.NAME, UPPER(c.CUSTODIAN_ID) || '_')",
        "'task:' || h.QUERY_ID",
        "NOT EXISTS (SELECT 1 FROM \"ASTRA_DEV\".\"CONTROL\".\"ALERTS\" a WHERE a.SOURCE_KEY = 'task:' || h.QUERY_ID)",
      ] : strcontains(snowflake_procedure_sql.detect_task_failures.procedure_definition, needle)
    ])
    error_message = "Task failure detection must scan task history, take severity from the custodian by task-name prefix, and deduplicate by query id."
  }
}

run "late_custodians_become_alerts_listing_missing_files" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "FROM \"ASTRA_DEV\".\"CONTROL\".\"CUSTODIANS\" c",
        "SYSDATE() >= CUTOFF_UTC",
        "POSITION(DAY_NAME IN BUSINESS_DAYS) > 0",
        "JOIN \"ASTRA_DEV\".\"CONTROL\".\"CUSTODIAN_FILES\" f",
        "FROM \"ASTRA_DEV\".\"CONTROL\".\"FILE_LOAD_LOG\" l",
        "l.FILE_NAME LIKE f.FILE_PATTERN",
        "LISTAGG(f.FILE_PATTERN, ', ')",
        "m.LATE_SEVERITY",
        "'late:' || m.CUSTODIAN_ID || ':' || m.BUSINESS_DATE::STRING",
        "' UTC. Missing: '",
        "|| m.MISSING_FILES ||",
      ] : strcontains(snowflake_procedure_sql.detect_late_custodians.procedure_definition, needle)
    ])
    error_message = "Late detection must apply cutoff and business days per custodian, compare expected files with arrivals, list the missing files and alert once per business date."
  }
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

run "dispatch_sends_per_route_and_records_every_attempt" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "JOIN \"ASTRA_DEV\".\"CONTROL\".\"ALERT_ROUTES\" r ON r.SEVERITY = a.SEVERITY AND r.ENABLED",
        "d.STATUS = 'sent'",
        "< 5",
        "SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG(:integration, :subject",
        "SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(:text)",
        "SNOWFLAKE.NOTIFICATION.APPLICATION_JSON(:issue)",
        "WHEN OTHER THEN",
        "INSERT INTO \"ASTRA_DEV\".\"CONTROL\".\"ALERT_DELIVERIES\"",
        "'[Astra dev] '",
      ] : strcontains(snowflake_procedure_sql.dispatch_alerts.procedure_definition, needle)
    ])
    error_message = "Dispatch must follow the routes, send through the three integration kinds, cap attempts, catch delivery errors and record each attempt."
  }

  assert {
    condition     = snowflake_task.raise_alerts.started && snowflake_task.raise_alerts.schedule[0].minutes == 1 && snowflake_task.raise_alerts.warehouse == null
    error_message = "Alerting runs every minute, serverless, so a failure is alerted well within five minutes."
  }

  assert {
    condition     = strcontains(snowflake_procedure_sql.run_alerting.procedure_definition, "DETECT_TASK_FAILURES") && strcontains(snowflake_procedure_sql.run_alerting.procedure_definition, "DETECT_LATE_CUSTODIANS") && strcontains(snowflake_procedure_sql.run_alerting.procedure_definition, "DISPATCH_ALERTS")
    error_message = "One run detects both kinds and dispatches."
  }
}

# ---------------------------------------------------------------------------
# Channels and routes
# ---------------------------------------------------------------------------

run "no_channels_means_no_integrations_and_no_routes" {
  command = plan

  assert {
    condition = (
      length(snowflake_email_notification_integration.alerts) == 0 &&
      length(snowflake_execute.slack_integration) == 0 &&
      length(snowflake_execute.jira_integration) == 0 &&
      length(snowflake_execute.alert_route) == 0
    )
    error_message = "Without channel settings nothing is created; alerts are still recorded in ALERTS."
  }
}

run "all_three_channels_are_wired_when_configured" {
  command = plan

  variables {
    alert_email_recipients = ["ops@example.com", "steward@example.com"]
    slack_webhook_secret   = "T000/B000/secret"
    jira = {
      site_url    = "https://artizent.atlassian.net"
      project_key = "ASTRA"
      user_email  = "alerts@example.com"
      issue_type  = "Bug"
    }
    jira_api_token = "token"
  }

  assert {
    condition     = snowflake_email_notification_integration.alerts[0].name == "ASTRA_DEV_ALERT_EMAIL" && toset(snowflake_email_notification_integration.alerts[0].allowed_recipients) == toset(["ops@example.com", "steward@example.com"])
    error_message = "Email integration must allow exactly the configured recipients."
  }

  assert {
    condition     = strcontains(snowflake_execute.slack_integration[0].execute, "WEBHOOK_URL = 'https://hooks.slack.com/services/SNOWFLAKE_WEBHOOK_SECRET'") && strcontains(snowflake_execute.slack_integration[0].execute, "WEBHOOK_BODY_TEMPLATE = '{\"text\": \"SNOWFLAKE_WEBHOOK_MESSAGE\"}'")
    error_message = "Slack integration must keep the webhook secret in a Snowflake secret and post a text payload."
  }

  assert {
    condition     = strcontains(snowflake_execute.jira_integration[0].execute, "WEBHOOK_URL = 'https://artizent.atlassian.net/rest/api/3/issue'") && strcontains(snowflake_execute.jira_integration[0].execute, "'Authorization' = 'Basic SNOWFLAKE_WEBHOOK_SECRET'")
    error_message = "Jira integration must post to the issue API with basic authentication from a Snowflake secret."
  }

  assert {
    condition     = strcontains(snowflake_procedure_sql.dispatch_alerts.procedure_definition, "OBJECT_CONSTRUCT('key', 'ASTRA')") && strcontains(snowflake_procedure_sql.dispatch_alerts.procedure_definition, "OBJECT_CONSTRUCT('name', 'Bug')")
    error_message = "Jira issues carry the configured project and issue type."
  }

  assert {
    condition = toset(keys(snowflake_execute.alert_route)) == toset([
      "critical.slack", "critical.jira", "critical.email",
      "error.slack", "error.jira", "error.email",
      "warning.slack", "warning.email",
      "info.slack",
    ])
    error_message = "Routes: critical and error to all three, warning to Slack and email, info to Slack."
  }

  assert {
    condition     = strcontains(snowflake_execute.alert_route["error.email"].execute, "'ops@example.com,steward@example.com'") && strcontains(snowflake_execute.alert_route["error.email"].execute, "WHEN NOT MATCHED THEN INSERT")
    error_message = "Email routes carry the recipients and are seeded without overwriting operator changes."
  }
}

run "only_configured_channels_get_routes" {
  command = plan

  variables {
    alert_email_recipients = ["ops@example.com"]
  }

  assert {
    condition     = toset(keys(snowflake_execute.alert_route)) == toset(["critical.email", "error.email", "warning.email"])
    error_message = "With email only, routes exist for the severities that use email."
  }
}

# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

run "custodian_tables_carry_cutoff_business_days_files_and_severities" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.custodians.column : c.name] == ["CUSTODIAN_ID", "NAME", "CUTOFF_TIME", "TIMEZONE", "BUSINESS_DAYS", "LATE_SEVERITY", "FAILURE_SEVERITY", "REFRESH_EXPECTED_DAYS", "ENABLED", "SOURCE", "UPDATED_AT"]
    error_message = "CUSTODIANS holds the schedule, both severities and the refresh expectation per custodian."
  }

  assert {
    condition     = [for c in snowflake_iceberg_table.custodian_files.column : c.name] == ["CUSTODIAN_ID", "FILE_PATTERN", "DESCRIPTION"]
    error_message = "CUSTODIAN_FILES holds the expected files per custodian."
  }

  assert {
    condition     = [for c in snowflake_iceberg_table.alerts.column : c.name] == ["ALERT_ID", "RAISED_AT", "KIND", "SEVERITY", "CUSTODIAN_ID", "TITLE", "BODY", "SOURCE_KEY", "BUSINESS_DATE"]
    error_message = "ALERTS identifies each alert, its kind, severity, custodian and deduplication key."
  }
}

run "stale_refreshes_are_alerted_from_the_merge_log" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.merge_log.column : c.name] == ["CUSTODIAN_ID", "SOURCE_ID", "SCOPE", "BUSINESS_DATE", "MODE", "FILE_NAME", "ROWS_INSERTED", "ROWS_UPDATED", "ROWS_CARRIED", "ROWS_RETIRED", "LOADED_AT"]
    error_message = "MERGE_LOG records who, what scope, which mode and the counts of every merge."
  }

  assert {
    condition = alltrue([
      for needle in [
        "MAX(IFF(m.MODE = 'refresh', m.LOADED_AT, NULL)) AS LAST_REFRESH",
        "c.REFRESH_EXPECTED_DAYS IS NOT NULL",
        "s.LAST_REFRESH IS NULL OR s.LAST_REFRESH < DATEADD('day', -c.REFRESH_EXPECTED_DAYS, SYSDATE())",
        "'refresh_stale'",
        "t.LATE_SEVERITY",
        "'refresh:' || t.CUSTODIAN_ID || ':' || t.SOURCE_ID || ':' || t.SCOPE || ':' || CURRENT_DATE()::STRING",
      ] : strcontains(snowflake_procedure_sql.detect_stale_refreshes.procedure_definition, needle)
    ])
    error_message = "Stale detection must compare the last refresh per custodian, source and scope with the custodian's expectation and alert once per day at the late severity."
  }

  assert {
    condition     = strcontains(snowflake_procedure_sql.run_alerting.procedure_definition, "DETECT_STALE_REFRESHES")
    error_message = "The alerting run includes stale refresh detection."
  }
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

run "rejects_jira_without_token" {
  command = plan

  variables {
    jira = {
      site_url    = "https://artizent.atlassian.net"
      project_key = "ASTRA"
      user_email  = "alerts@example.com"
    }
  }

  expect_failures = [var.jira]
}

run "rejects_alert_interval_over_five_minutes" {
  command = plan

  variables {
    alert_interval_minutes = 10
  }

  expect_failures = [var.alert_interval_minutes]
}

run "rejects_malformed_recipient" {
  command = plan

  variables {
    alert_email_recipients = ["ops at example.com"]
  }

  expect_failures = [var.alert_email_recipients]
}
