# ---------------------------------------------------------------------------
# Secrets, access history and masking baseline (S1.2.5)
# ---------------------------------------------------------------------------
#
# Three things a security reviewer asks for on day one:
#
#   Secrets      Values live in the client's secret manager (AWS Secrets
#                Manager, created here with no value) and in Snowflake secret
#                objects; Git, config and logs carry references only. CI reads
#                the manager through the deploy role and masks what it reads.
#
#   Masking      A PII tag in CONTROL with a category as its value. Masking
#                policies for strings, numbers and dates are bound to the tag,
#                so tagging a column is all a renderer has to do. Privileged
#                roles see the value; everyone else sees a mask shaped by the
#                category. Bronze raw lines are tagged from the start.
#
#   Access       A view over ACCESS_HISTORY joined to the PII tag answers
#   history      "who read which PII column and when". A serverless task copies
#                it hourly into an Iceberg table in the client's bucket so the
#                record outlives Snowflake's own retention.

locals {
  pii_tag_fqn         = "\"${local.database_name}\".\"${local.control_schema}\".\"PII\""
  pii_access_log_fqn  = "${local.control_fqn}.\"PII_ACCESS_LOG\""
  pii_access_view_fqn = "${local.control_fqn}.\"PII_ACCESS\""

  pii_categories = ["raw_record", "name", "account_number", "tax_id", "email", "phone", "address", "date_of_birth", "financial"]

  # Roles that see PII in clear, expressed as functional role keys.
  pii_unmasked_condition = join(" OR ", [for r in var.pii_unmasked_roles : "IS_ROLE_IN_SESSION('${local.role_names[r]}')"])

  masking_string_body = <<-SQL
    CASE
      WHEN ${local.pii_unmasked_condition} THEN VAL
      WHEN VAL IS NULL THEN NULL
      WHEN SYSTEM$GET_TAG_ON_CURRENT_COLUMN('${local.database_name}.${local.control_schema}.PII') = 'account_number'
        THEN REPEAT('*', GREATEST(LENGTH(VAL) - 4, 0)) || RIGHT(VAL, LEAST(LENGTH(VAL), 4))
      WHEN SYSTEM$GET_TAG_ON_CURRENT_COLUMN('${local.database_name}.${local.control_schema}.PII') = 'email'
        THEN '*****@' || SPLIT_PART(VAL, '@', 2)
      ELSE '*****'
    END
  SQL

  masking_number_body = <<-SQL
    CASE
      WHEN ${local.pii_unmasked_condition} THEN VAL
      ELSE NULL
    END
  SQL

  masking_date_body = <<-SQL
    CASE
      WHEN ${local.pii_unmasked_condition} THEN VAL
      WHEN VAL IS NULL THEN NULL
      ELSE DATE_TRUNC('year', VAL)
    END
  SQL

  # Who read which PII column and when. Objects are matched to the current
  # PII tag references so the answer follows the tags as they are applied.
  pii_access_statement = <<-SQL
    SELECT
      ah.QUERY_ID,
      ah.QUERY_START_TIME,
      ah.USER_NAME,
      qh.ROLE_NAME,
      obj.value:objectName::STRING AS OBJECT_NAME,
      col.value:columnName::STRING AS COLUMN_NAME,
      tr.TAG_VALUE AS PII_CATEGORY,
      qh.QUERY_TYPE,
      qh.WAREHOUSE_NAME
    FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
         LATERAL FLATTEN(INPUT => ah.BASE_OBJECTS_ACCESSED) obj,
         LATERAL FLATTEN(INPUT => obj.value:columns) col
    JOIN SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES tr
      ON tr.TAG_DATABASE = '${local.database_name}'
     AND tr.TAG_SCHEMA = '${local.control_schema}'
     AND tr.TAG_NAME = 'PII'
     AND tr.DOMAIN = 'COLUMN'
     AND tr.OBJECT_DATABASE || '.' || tr.OBJECT_SCHEMA || '.' || tr.OBJECT_NAME = obj.value:objectName::STRING
     AND tr.COLUMN_NAME = col.value:columnName::STRING
    LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
      ON qh.QUERY_ID = ah.QUERY_ID
  SQL

  retain_pii_access_statement = <<-SQL
    INSERT INTO ${local.pii_access_log_fqn} (QUERY_ID, QUERY_START_TIME, USER_NAME, ROLE_NAME, OBJECT_NAME, COLUMN_NAME, PII_CATEGORY, QUERY_TYPE, WAREHOUSE_NAME, RETAINED_AT)
    SELECT v.QUERY_ID, CONVERT_TIMEZONE('UTC', v.QUERY_START_TIME)::TIMESTAMP_NTZ(6), v.USER_NAME, v.ROLE_NAME, v.OBJECT_NAME, v.COLUMN_NAME, v.PII_CATEGORY, v.QUERY_TYPE, v.WAREHOUSE_NAME, SYSDATE()
    FROM ${local.pii_access_view_fqn} v
    WHERE v.QUERY_START_TIME >= DATEADD('day', -${var.pii_access_catchup_days}, CURRENT_TIMESTAMP())
      AND NOT EXISTS (
        SELECT 1 FROM ${local.pii_access_log_fqn} l
        WHERE l.QUERY_ID = v.QUERY_ID AND l.OBJECT_NAME = v.OBJECT_NAME AND l.COLUMN_NAME = v.COLUMN_NAME)
  SQL
}

# --- Masking --------------------------------------------------------------

resource "snowflake_masking_policy" "pii_string" {
  name     = "PII_STRING"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Masks string columns tagged PII for roles not in the unmasked list. Account numbers keep their last four characters, emails their domain. Managed by Terraform."

  argument {
    name = "VAL"
    type = "STRING"
  }
  return_data_type = "STRING"
  body             = local.masking_string_body

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_masking_policy" "pii_number" {
  name     = "PII_NUMBER"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Masks numeric columns tagged PII to NULL for roles not in the unmasked list. Managed by Terraform."

  argument {
    name = "VAL"
    type = "NUMBER"
  }
  return_data_type = "NUMBER"
  body             = local.masking_number_body

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_masking_policy" "pii_date" {
  name     = "PII_DATE"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Masks date columns tagged PII to the first day of their year for roles not in the unmasked list. Managed by Terraform."

  argument {
    name = "VAL"
    type = "DATE"
  }
  return_data_type = "DATE"
  body             = local.masking_date_body

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_tag" "pii" {
  name                   = "PII"
  database               = snowflake_database.this.name
  schema                 = snowflake_schema.this[local.control_schema].name
  comment                = "Marks a column as personal or sensitive data; the value is the category. Masking policies are bound to this tag, so tagging a column masks it for non-privileged roles and records its reads."
  ordered_allowed_values = local.pii_categories

  masking_policies = [
    "${local.control_fqn}.\"PII_STRING\"",
    "${local.control_fqn}.\"PII_NUMBER\"",
    "${local.control_fqn}.\"PII_DATE\"",
  ]

  depends_on = [
    snowflake_masking_policy.pii_string,
    snowflake_masking_policy.pii_number,
    snowflake_masking_policy.pii_date,
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}

# Raw custodian records carry account numbers and names. Masked from day one.
resource "snowflake_tag_association" "raw_lines_pii" {
  object_type        = "COLUMN"
  object_identifiers = ["\"${local.database_name}\".\"${local.bronze_schema}\".\"RAW_LINES\".\"LINE\""]
  tag_id             = snowflake_tag.pii.fully_qualified_name
  tag_value          = "raw_record"

  depends_on = [snowflake_iceberg_table.raw_lines]
}

# Renderers may apply the PII tag to the columns they create.
resource "snowflake_grant_privileges_to_account_role" "pii_tag_apply" {
  for_each = toset(["ADMIN", "ENGINEER"])

  account_role_name = snowflake_account_role.this[each.key].name
  privileges        = ["APPLY"]

  on_schema_object {
    object_type = "TAG"
    object_name = snowflake_tag.pii.fully_qualified_name
  }
}

# --- Access history -------------------------------------------------------

resource "snowflake_view" "pii_access" {
  name      = "PII_ACCESS"
  database  = snowflake_database.this.name
  schema    = snowflake_schema.this[local.control_schema].name
  comment   = "Who read which PII-tagged column and when, from ACCESS_HISTORY joined to the PII tag. Up to three hours behind. Managed by Terraform."
  statement = local.pii_access_statement

  depends_on = [snowflake_tag.pii, snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_iceberg_table" "pii_access_log" {
  name     = "PII_ACCESS_LOG"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Retained copy of PII column reads, appended hourly from PII_ACCESS. Kept in the client's bucket beyond Snowflake's own retention. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/pii_access_log/"

  column {
    name     = "QUERY_ID"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "QUERY_START_TIME"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }
  column {
    name     = "USER_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name = "ROLE_NAME"
    type = "STRING"
  }
  column {
    name     = "OBJECT_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "COLUMN_NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name = "PII_CATEGORY"
    type = "STRING"
  }
  column {
    name = "QUERY_TYPE"
    type = "STRING"
  }
  column {
    name = "WAREHOUSE_NAME"
    type = "STRING"
  }
  column {
    name     = "RETAINED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_task" "retain_pii_access" {
  name     = "RETAIN_PII_ACCESS"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Appends new PII column reads to PII_ACCESS_LOG every ${var.pii_access_retention_interval_minutes} minutes. Serverless. Managed by Terraform."

  started                                  = true
  user_task_managed_initial_warehouse_size = "XSMALL"
  suspend_task_after_num_failures          = 10
  sql_statement                            = local.retain_pii_access_statement

  schedule {
    minutes = var.pii_access_retention_interval_minutes
  }

  depends_on = [snowflake_view.pii_access, snowflake_iceberg_table.pii_access_log]
}

# --- Secrets in the client's secret manager -------------------------------
#
# Created without values. Values are put in by an operator or a rotation
# process, never by Terraform, so no secret passes through state or Git.

locals {
  managed_secrets = {
    "snowflake/private-key"      = "PEM private key of the Snowflake service user the pipeline runs as"
    "open-catalog/client-secret" = "OAuth client secret of the Open Catalog sync service connection"
    "alerts/slack-webhook"       = "Secret path of the Slack incoming webhook used for alerts"
    "alerts/jira-token"          = "API token of the Jira user alerts are raised as"
  }
  secrets_prefix = "${lower(var.prefix)}/${var.environment}"
}

resource "aws_secretsmanager_secret" "managed" {
  for_each = local.managed_secrets

  name                    = "${local.secrets_prefix}/${each.key}"
  description             = "${each.value}. Astra Data Factory ${var.environment}."
  recovery_window_in_days = 7
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    sid       = "ReadAstraSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [for s in aws_secretsmanager_secret.managed : s.arn]
  }
}

resource "aws_iam_policy" "read_secrets" {
  name        = "${local.name_prefix}_READ_SECRETS"
  description = "Read the ${local.name_prefix} secrets in Secrets Manager. Attach to the deploy role."
  policy      = data.aws_iam_policy_document.read_secrets.json
}
