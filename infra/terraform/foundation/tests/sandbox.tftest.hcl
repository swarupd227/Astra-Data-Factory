# Unit tests for ephemeral sandboxes (S1.2.3).
#
# Mocked providers. The live check (a sandbox with a bundle deployed in under
# two minutes, tagged, and gone after destroy) is `astra-verify sandbox check`.

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

run "sandbox_role_can_create_and_drop_its_own_databases_and_warehouses" {
  command = plan

  assert {
    condition     = snowflake_account_role.this["SANDBOX"].name == "ASTRA_DEV_SANDBOX"
    error_message = "The SANDBOX role must exist with the environment prefix."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.sandbox_account.privileges == toset(["CREATE DATABASE", "CREATE WAREHOUSE", "EXECUTE TASK", "EXECUTE MANAGED TASK"])
    error_message = "SANDBOX holds exactly the account privileges needed to create sandboxes and run tasks in them."
  }

  assert {
    condition     = contains(keys(snowflake_grant_privileges_to_account_role.external_volume_usage), "SANDBOX") && contains(keys(snowflake_grant_privileges_to_account_role.storage_integration_usage), "SANDBOX")
    error_message = "SANDBOX may use the external volume and the storage integration so sandboxes are Iceberg and can stage sample files."
  }

  assert {
    condition     = toset([for k, _ in snowflake_grant_privileges_to_account_role.future_objects : split(".", k)[0] if split(".", k)[1] == "SANDBOX"]) == toset(["CONTROL"])
    error_message = "SANDBOX reads CONTROL only; it never touches Bronze, Silver or Gold of the shared environment."
  }

  assert {
    condition     = snowflake_grant_privileges_to_account_role.sandbox_log_insert.privileges == toset(["INSERT"])
    error_message = "SANDBOX may insert into the sandbox log and nothing else in CONTROL."
  }
}

run "cost_tags_exist_and_sandbox_may_apply_them" {
  command = plan

  assert {
    condition     = snowflake_tag.task_id.name == "TASK_ID" && snowflake_tag.task_id.schema == "CONTROL" && snowflake_tag.purpose.name == "PURPOSE"
    error_message = "TASK_ID and PURPOSE tags live in CONTROL."
  }

  assert {
    condition     = contains(snowflake_tag.purpose.ordered_allowed_values, "sandbox")
    error_message = "PURPOSE must allow the value sandbox."
  }

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.sandbox_apply_tags)) == toset(["task_id", "purpose"]) && alltrue([for g in snowflake_grant_privileges_to_account_role.sandbox_apply_tags : g.privileges == toset(["APPLY"])])
    error_message = "SANDBOX may apply both tags."
  }
}

run "reaper_drops_expired_sandboxes_on_a_schedule" {
  command = plan

  assert {
    condition     = snowflake_procedure_sql.reap_sandboxes.name == "REAP_SANDBOXES" && snowflake_procedure_sql.reap_sandboxes.execute_as == "OWNER" && snowflake_procedure_sql.reap_sandboxes.return_type == "INTEGER"
    error_message = "The reaper is an owner's-rights SQL procedure returning the number dropped."
  }

  assert {
    condition = alltrue([
      for needle in [
        "SHOW DATABASES LIKE 'ASTRA_DEV_SBX_%'",
        "meta:expires_at",
        "reason := 'expired'",
        "DATEADD('hour', -24, SYSDATE())",
        "reason := 'max_age'",
        "DROP WAREHOUSE IF EXISTS",
        "DROP DATABASE IF EXISTS",
        "INSERT INTO \"ASTRA_DEV\".\"CONTROL\".\"SANDBOX_LOG\"",
      ] : strcontains(snowflake_procedure_sql.reap_sandboxes.procedure_definition, needle)
    ])
    error_message = "The reaper must find sandboxes by name, apply the expiry and the hard maximum, drop warehouse then database, and log it."
  }

  assert {
    condition     = snowflake_task.reap_sandboxes.started && snowflake_task.reap_sandboxes.schedule[0].minutes == 10 && snowflake_task.reap_sandboxes.warehouse == null
    error_message = "The reaper task runs every 10 minutes, serverless, and starts enabled."
  }

  assert {
    condition     = snowflake_task.reap_sandboxes.sql_statement == "CALL \"ASTRA_DEV\".\"CONTROL\".\"REAP_SANDBOXES\"()"
    error_message = "The task calls the reaper procedure."
  }
}

run "reaper_settings_are_variables" {
  command = plan

  variables {
    sandbox_reap_interval_minutes = 5
    sandbox_max_age_hours         = 6
  }

  assert {
    condition     = snowflake_task.reap_sandboxes.schedule[0].minutes == 5 && strcontains(snowflake_procedure_sql.reap_sandboxes.procedure_definition, "DATEADD('hour', -6, SYSDATE())")
    error_message = "Reap interval and maximum age must follow the variables."
  }
}

run "sandbox_log_records_task_event_and_reason" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.sandbox_log.column : c.name] == ["TASK_ID", "SANDBOX", "EVENT", "REASON", "DETAIL", "OCCURRED_AT"]
    error_message = "Sandbox log columns must identify the task, the sandbox, the event and the reason."
  }
}

run "sandbox_files_are_staged_outside_the_snowpipe_prefix" {
  command = plan

  assert {
    condition     = contains(snowflake_storage_integration_aws.landing.storage_allowed_locations, "s3://astra-dev-landing-123456789012/sandbox/")
    error_message = "The storage integration must allow the sandbox prefix."
  }

  assert {
    condition     = alltrue([for q in aws_s3_bucket_notification.landing.queue : q.filter_prefix == "landing/"])
    error_message = "Snowpipe must not watch the sandbox prefix."
  }
}

run "rejects_sandbox_prefix_inside_landing_prefix" {
  command = plan

  variables {
    sandbox_prefix = "landing/sandbox"
  }

  expect_failures = [var.sandbox_prefix]
}

run "rejects_reaper_settings_out_of_range" {
  command = plan

  variables {
    sandbox_max_age_hours = 0
  }

  expect_failures = [var.sandbox_max_age_hours]
}
