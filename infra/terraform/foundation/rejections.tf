# ---------------------------------------------------------------------------
# Rejection taxonomy (S2.3.2)
#
# CONTROL.REJECTION_CODES holds the domain pack's rejection codes: every
# reason a file, record or value is rejected, with its level, severity,
# owner and resolution. `astra-data rejections sync` brings it in line with
# domains/<pack>/rejections.yaml on every deploy; codes that leave the
# taxonomy are retired (ACTIVE = FALSE), never deleted, so exception rows
# keep their referent. Every SILVER.EXCEPTION row references a code here;
# the rendered CDM test exception_rejection_code_lookup.sql checks it.
# ---------------------------------------------------------------------------

resource "snowflake_iceberg_table" "rejection_codes" {
  name     = "REJECTION_CODES"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Rejection taxonomy of the domain pack, synced from domains/<pack>/rejections.yaml by astra-data. Managed by Terraform."

  external_volume = snowflake_external_volume.iceberg.name
  base_location   = "control/rejection_codes/"

  column {
    name     = "CODE"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "NAME"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "DESCRIPTION"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "LEVEL"
    type     = "STRING"
    not_null = "true"
    comment  = "file, record or field"
  }
  column {
    name     = "SEVERITY"
    type     = "STRING"
    not_null = "true"
    comment  = "critical (file not loaded), error (record not loaded) or warning (loaded and flagged)"
  }
  column {
    name     = "CATEGORY"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "OWNER"
    type     = "STRING"
    not_null = "true"
    comment  = "custodian, data_engineer, steward or platform: who resolves exceptions with this code"
  }
  column {
    name    = "ENTITY"
    type    = "STRING"
    comment = "Canonical entity the rejected record was meant for, when there is one"
  }
  column {
    name     = "RESOLUTION"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "AUTO_RESOLVE"
    type     = "BOOLEAN"
    not_null = "true"
    comment  = "Exception Triage may apply the resolution without a person, with audit"
  }
  column {
    name    = "LOADER_CODES"
    type    = "STRING"
    comment = "Comma-separated codes of the legacy Loader Rejections reference this code reproduces"
  }
  column {
    name     = "DOMAIN"
    type     = "STRING"
    not_null = "true"
  }
  column {
    name     = "ACTIVE"
    type     = "BOOLEAN"
    not_null = "true"
    comment  = "FALSE once the code leaves the taxonomy; rows are never deleted"
  }
  column {
    name    = "SOURCE"
    type    = "STRING"
    comment = "Taxonomy file this row was synced from"
  }
  column {
    name     = "UPDATED_AT"
    type     = "TIMESTAMP_NTZ(6)"
    not_null = "true"
  }

  depends_on = [
    snowflake_grant_privileges_to_account_role.future_objects,
  ]
}
