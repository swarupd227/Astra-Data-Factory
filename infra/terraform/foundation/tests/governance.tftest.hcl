# Unit tests for secrets, access history and masking (S1.2.5).
#
# Mocked providers. Live checks: `astra-verify pii check` compares a tagged
# column under a privileged and a restricted role and queries PII_ACCESS.

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
# Masking
# ---------------------------------------------------------------------------

run "pii_tag_carries_categories_and_the_three_masking_policies" {
  command = plan

  assert {
    condition     = snowflake_tag.pii.name == "PII" && snowflake_tag.pii.schema == "CONTROL"
    error_message = "The PII tag lives in CONTROL."
  }

  assert {
    condition     = contains(snowflake_tag.pii.ordered_allowed_values, "account_number") && contains(snowflake_tag.pii.ordered_allowed_values, "raw_record") && contains(snowflake_tag.pii.ordered_allowed_values, "email")
    error_message = "The tag value is the PII category."
  }

  assert {
    condition     = length(snowflake_tag.pii.masking_policies) == 3
    error_message = "String, number and date policies are bound to the tag."
  }

  assert {
    condition = (
      snowflake_masking_policy.pii_string.return_data_type == "STRING" &&
      snowflake_masking_policy.pii_number.return_data_type == "NUMBER" &&
      snowflake_masking_policy.pii_date.return_data_type == "DATE"
    )
    error_message = "One policy per data type."
  }
}

run "privileged_roles_see_values_and_everyone_else_sees_a_mask" {
  command = plan

  assert {
    condition = alltrue([
      for role in ["ASTRA_DEV_ADMIN", "ASTRA_DEV_ENGINEER", "ASTRA_DEV_PIPELINE", "ASTRA_DEV_STEWARD"] :
      strcontains(snowflake_masking_policy.pii_string.body, "IS_ROLE_IN_SESSION('${role}')")
    ])
    error_message = "The default unmasked roles are ADMIN, ENGINEER, PIPELINE and STEWARD."
  }

  assert {
    condition     = !strcontains(snowflake_masking_policy.pii_string.body, "AUDITOR") && !strcontains(snowflake_masking_policy.pii_string.body, "CONSUMER") && !strcontains(snowflake_masking_policy.pii_string.body, "SANDBOX")
    error_message = "AUDITOR, CONSUMER and SANDBOX are masked."
  }

  assert {
    condition = alltrue([
      for needle in [
        "SYSTEM$GET_TAG_ON_CURRENT_COLUMN('ASTRA_DEV.CONTROL.PII') = 'account_number'",
        "RIGHT(VAL, LEAST(LENGTH(VAL), 4))",
        "= 'email'",
        "'*****@' || SPLIT_PART(VAL, '@', 2)",
        "ELSE '*****'",
      ] : strcontains(snowflake_masking_policy.pii_string.body, needle)
    ])
    error_message = "The string mask keeps the last four of an account number and the domain of an email, and stars everything else."
  }

  assert {
    condition     = strcontains(snowflake_masking_policy.pii_number.body, "ELSE NULL") && strcontains(snowflake_masking_policy.pii_date.body, "DATE_TRUNC('year', VAL)")
    error_message = "Numbers mask to NULL and dates to the start of their year."
  }
}

run "unmasked_roles_are_a_variable" {
  command = plan

  variables {
    pii_unmasked_roles = ["ADMIN", "ENGINEER"]
  }

  assert {
    condition     = strcontains(snowflake_masking_policy.pii_string.body, "IS_ROLE_IN_SESSION('ASTRA_DEV_ENGINEER')") && !strcontains(snowflake_masking_policy.pii_string.body, "STEWARD")
    error_message = "Only the listed roles are unmasked."
  }
}

run "raw_lines_are_tagged_pii_from_day_one" {
  command = plan

  assert {
    condition     = snowflake_tag_association.raw_lines_pii.object_type == "COLUMN" && snowflake_tag_association.raw_lines_pii.tag_value == "raw_record"
    error_message = "BRONZE.RAW_LINES.LINE carries the PII tag as raw_record."
  }

  assert {
    condition     = contains(snowflake_tag_association.raw_lines_pii.object_identifiers, "\"ASTRA_DEV\".\"BRONZE\".\"RAW_LINES\".\"LINE\"")
    error_message = "The association names the LINE column of the raw-lines table."
  }

  assert {
    condition     = toset(keys(snowflake_grant_privileges_to_account_role.pii_tag_apply)) == toset(["ADMIN", "ENGINEER"])
    error_message = "ADMIN and ENGINEER may apply the PII tag to columns they create."
  }
}

# ---------------------------------------------------------------------------
# Access history
# ---------------------------------------------------------------------------

run "pii_access_view_answers_who_read_which_column_when" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah",
        "LATERAL FLATTEN(INPUT => ah.BASE_OBJECTS_ACCESSED) obj",
        "LATERAL FLATTEN(INPUT => obj.value:columns) col",
        "SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES tr",
        "tr.TAG_NAME = 'PII'",
        "tr.DOMAIN = 'COLUMN'",
        "ah.USER_NAME",
        "qh.ROLE_NAME",
        "ah.QUERY_START_TIME",
        "col.value:columnName::STRING AS COLUMN_NAME",
      ] : strcontains(snowflake_view.pii_access.statement, needle)
    ])
    error_message = "The view must join column-level access history to the current PII tag references and expose user, role, time, object and column."
  }

  assert {
    condition     = snowflake_view.pii_access.name == "PII_ACCESS" && snowflake_view.pii_access.schema == "CONTROL"
    error_message = "The view lives in CONTROL where AUDITOR reads."
  }
}

run "pii_access_is_retained_hourly_in_the_client_bucket" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.pii_access_log.column : c.name] == ["QUERY_ID", "QUERY_START_TIME", "USER_NAME", "ROLE_NAME", "OBJECT_NAME", "COLUMN_NAME", "PII_CATEGORY", "QUERY_TYPE", "WAREHOUSE_NAME", "RETAINED_AT"]
    error_message = "The retained log keeps the same answer as the view plus when it was retained."
  }

  assert {
    condition     = snowflake_task.retain_pii_access.started && snowflake_task.retain_pii_access.schedule[0].minutes == 60 && snowflake_task.retain_pii_access.warehouse == null
    error_message = "Retention runs hourly, serverless."
  }

  assert {
    condition     = strcontains(snowflake_task.retain_pii_access.sql_statement, "DATEADD('day', -7, CURRENT_TIMESTAMP())") && strcontains(snowflake_task.retain_pii_access.sql_statement, "NOT EXISTS")
    error_message = "Retention looks back seven days and never duplicates a read."
  }
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

run "secrets_exist_in_the_client_secret_manager_without_values" {
  command = plan

  assert {
    condition = toset(keys(aws_secretsmanager_secret.managed)) == toset([
      "snowflake/private-key", "open-catalog/client-secret", "alerts/slack-webhook", "alerts/jira-token",
    ])
    error_message = "The four secrets the platform needs are created as references."
  }

  assert {
    condition     = aws_secretsmanager_secret.managed["snowflake/private-key"].name == "astra/dev/snowflake/private-key"
    error_message = "Secrets are named <prefix>/<env>/<purpose>."
  }

  assert {
    condition     = aws_iam_policy.read_secrets.name == "ASTRA_DEV_READ_SECRETS"
    error_message = "A read policy exists to attach to the deploy role."
  }
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

run "rejects_unmasking_the_auditor" {
  command = plan

  variables {
    pii_unmasked_roles = ["ADMIN", "AUDITOR"]
  }

  expect_failures = [var.pii_unmasked_roles]
}

run "rejects_retention_interval_out_of_range" {
  command = plan

  variables {
    pii_access_retention_interval_minutes = 5
  }

  expect_failures = [var.pii_access_retention_interval_minutes]
}
