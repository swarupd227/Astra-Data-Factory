# Unit tests for the rejection taxonomy table (S2.3.2).
#
# Mocked providers. The content of the table comes from
# domains/<pack>/rejections.yaml through `astra-data rejections sync`.

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

run "rejection_codes_table_carries_level_severity_owner_and_resolution" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.rejection_codes.column : c.name] == ["CODE", "NAME", "DESCRIPTION", "LEVEL", "SEVERITY", "CATEGORY", "OWNER", "ENTITY", "RESOLUTION", "AUTO_RESOLVE", "LOADER_CODES", "DOMAIN", "ACTIVE", "SOURCE", "UPDATED_AT"]
    error_message = "REJECTION_CODES holds every attribute of a code the taxonomy file declares, plus the sync bookkeeping."
  }

  assert {
    condition     = snowflake_iceberg_table.rejection_codes.schema == "CONTROL" && snowflake_iceberg_table.rejection_codes.base_location == "control/rejection_codes/"
    error_message = "The taxonomy lives in CONTROL on the environment's Iceberg volume like the other control tables."
  }

  assert {
    condition     = alltrue([for c in snowflake_iceberg_table.rejection_codes.column : c.not_null == "true" if contains(["CODE", "LEVEL", "SEVERITY", "OWNER", "RESOLUTION", "ACTIVE", "UPDATED_AT"], c.name)])
    error_message = "A code always has a level, severity, owner, resolution and active flag."
  }
}
