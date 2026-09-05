# ---------------------------------------------------------------------------
# Ephemeral sandboxes (S1.2.3)
# ---------------------------------------------------------------------------
#
# A sandbox is a database named <PREFIX>_<ENV>_SBX_<TASK> with the standard
# schemas and its own warehouse, created by the sandbox runner
# (verification/astra_verification/sandbox.py) for one task and dropped when
# the task ends. Because a sandbox is a whole database, a release bundle is
# deployed into it unchanged: only {{ DATABASE }} and the warehouse differ.
#
# This file provides what the runner needs and what must outlive it:
#
#   - the SANDBOX role with just enough privilege to create and drop its own
#     databases and warehouses
#   - tags for cost attribution, applied to every sandbox database and
#     warehouse: TASK_ID and PURPOSE
#   - a log of sandbox events in CONTROL
#   - a reaper: a stored procedure run by a serverless task that drops any
#     sandbox past the expiry written in its comment, or older than the hard
#     maximum, whether or not the runner is still alive

locals {
  sandbox_database_pattern = "${local.name_prefix}_SBX_%"
  sandbox_log_fqn          = "\"${local.database_name}\".\"${local.control_schema}\".\"SANDBOX_LOG\""
  sandbox_base_url         = "s3://${local.landing_bucket_name}/${var.sandbox_prefix}/"

  reap_sandboxes_definition = <<-SQL
    DECLARE
      dropped INTEGER DEFAULT 0;
    BEGIN
      SHOW DATABASES LIKE '${local.sandbox_database_pattern}';
      LET found RESULTSET := (
        SELECT "name" AS NAME, "comment" AS COMMENT,
               CONVERT_TIMEZONE('UTC', "created_on")::TIMESTAMP_NTZ AS CREATED_AT
        FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
      );
      LET c CURSOR FOR found;
      FOR rec IN c DO
        LET meta VARIANT := TRY_PARSE_JSON(rec.COMMENT);
        LET task_id STRING := COALESCE(meta:task::STRING, 'unknown');
        LET expires_at TIMESTAMP_NTZ := TRY_TO_TIMESTAMP_NTZ(meta:expires_at::STRING);
        LET reason STRING := NULL;
        IF (expires_at IS NOT NULL AND expires_at <= SYSDATE()) THEN
          reason := 'expired';
        ELSEIF (rec.CREATED_AT <= DATEADD('hour', -${var.sandbox_max_age_hours}, SYSDATE())) THEN
          reason := 'max_age';
        END IF;
        IF (reason IS NOT NULL) THEN
          EXECUTE IMMEDIATE 'DROP WAREHOUSE IF EXISTS "' || rec.NAME || '_WH"';
          EXECUTE IMMEDIATE 'DROP DATABASE IF EXISTS "' || rec.NAME || '"';
          INSERT INTO ${local.sandbox_log_fqn} (TASK_ID, SANDBOX, EVENT, REASON, DETAIL, OCCURRED_AT)
            VALUES (:task_id, rec.NAME, 'destroyed', :reason,
                    'Dropped by the reaper. expires_at=' || COALESCE(:expires_at::STRING, 'none') || ', created_at=' || rec.CREATED_AT::STRING,
                    SYSDATE());
          dropped := dropped + 1;
        END IF;
      END FOR;
      RETURN dropped;
    END;
  SQL
}

# --- Cost attribution tags ------------------------------------------------

resource "snowflake_tag" "task_id" {
  name     = "TASK_ID"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Task a sandbox or query belongs to. Set on sandbox databases and warehouses; joins to WAREHOUSE_METERING_HISTORY through TAG_REFERENCES for cost per task."

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_tag" "purpose" {
  name                   = "PURPOSE"
  database               = snowflake_database.this.name
  schema                 = snowflake_schema.this[local.control_schema].name
  comment                = "What an object is for. Sandbox objects carry 'sandbox'."
  ordered_allowed_values = ["sandbox", "pipeline", "workbench", "agent"]

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

# --- Sandbox log ----------------------------------------------------------

resource "snowflake_iceberg_table" "sandbox_log" {
  name     = "SANDBOX_LOG"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Every sandbox created and destroyed, by whom and why. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/sandbox_log/"

  column {
    name     = "TASK_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "SANDBOX"
    type     = "STRING"
    not_null = "true"
    comment  = "Sandbox database name"
  }
  column {
    name     = "EVENT"
    type     = "STRING"
    not_null = "true"
    comment  = "created or destroyed"
  }
  column {
    name    = "REASON"
    type    = "STRING"
    comment = "For destroyed: task_done, expired, max_age or manual"
  }
  column {
    name = "DETAIL"
    type = "STRING"
  }
  column {
    name     = "OCCURRED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

# --- Reaper ---------------------------------------------------------------

resource "snowflake_procedure_sql" "reap_sandboxes" {
  name     = "REAP_SANDBOXES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Drops sandboxes past their expiry or older than ${var.sandbox_max_age_hours} hours and logs each drop. Returns the number dropped. Managed by Terraform."

  return_type          = "INTEGER"
  execute_as           = "OWNER"
  procedure_definition = local.reap_sandboxes_definition

  depends_on = [
    snowflake_iceberg_table.sandbox_log,
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}

resource "snowflake_task" "reap_sandboxes" {
  name     = "REAP_SANDBOXES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Runs the sandbox reaper every ${var.sandbox_reap_interval_minutes} minutes. Serverless. Managed by Terraform."

  started                                  = true
  user_task_managed_initial_warehouse_size = "XSMALL"
  suspend_task_after_num_failures          = 10
  sql_statement                            = "CALL \"${local.database_name}\".\"${local.control_schema}\".\"REAP_SANDBOXES\"()"

  schedule {
    minutes = var.sandbox_reap_interval_minutes
  }

  depends_on = [snowflake_procedure_sql.reap_sandboxes]
}

# --- What the SANDBOX role may do -----------------------------------------

resource "snowflake_grant_privileges_to_account_role" "sandbox_account" {
  account_role_name = snowflake_account_role.this["SANDBOX"].name
  privileges        = ["CREATE DATABASE", "CREATE WAREHOUSE", "EXECUTE TASK", "EXECUTE MANAGED TASK"]
  on_account        = true
}

resource "snowflake_grant_privileges_to_account_role" "sandbox_apply_tags" {
  for_each = {
    task_id = snowflake_tag.task_id.fully_qualified_name
    purpose = snowflake_tag.purpose.fully_qualified_name
  }

  account_role_name = snowflake_account_role.this["SANDBOX"].name
  privileges        = ["APPLY"]

  on_schema_object {
    object_type = "TAG"
    object_name = each.value
  }
}

resource "snowflake_grant_privileges_to_account_role" "sandbox_log_insert" {
  account_role_name = snowflake_account_role.this["SANDBOX"].name
  privileges        = ["INSERT"]

  on_schema_object {
    object_type = "ICEBERG TABLE"
    object_name = snowflake_iceberg_table.sandbox_log.fully_qualified_name
  }
}
