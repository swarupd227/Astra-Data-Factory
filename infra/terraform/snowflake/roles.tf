# ---------------------------------------------------------------------------
# Functional roles
# ---------------------------------------------------------------------------
#
# One account role per persona group (spec Section 3), scoped to this
# environment. People and service users are granted these roles; nothing is
# ever granted to a user directly.
#
#   ADMIN     Owns the environment. Inherits every other role. Granted to the
#             account's parent role so the account hierarchy stays intact.
#   ENGINEER  Deploys generated code: creates and reads or writes every object.
#   PIPELINE  Service role for Snowpipe, Dynamic Tables and Tasks at run time.
#   STEWARD   Reads Silver, Gold and Control; works the exception store.
#   CONSUMER  Reads Gold and the watermark only (for example UMP via pg_lake).
#   AUDITOR   Reads everything and changes nothing.

locals {
  role_comments = {
    ADMIN    = "Owns the ${var.environment} environment and inherits every functional role."
    ENGINEER = "Deploys generated code and can create, read and write every object in ${var.environment}."
    PIPELINE = "Service role for Snowpipe, Dynamic Tables and Tasks in ${var.environment}."
    STEWARD  = "Reads Silver, Gold and Control and works the exception store in ${var.environment}."
    CONSUMER = "Reads Gold and the published watermark in ${var.environment}."
    AUDITOR  = "Reads everything in ${var.environment} and changes nothing."
  }
}

resource "snowflake_account_role" "this" {
  for_each = local.role_names

  name    = each.value
  comment = "${local.role_comments[each.key]} Managed by Terraform."
}

# Every functional role rolls up to ADMIN; ADMIN rolls up to the parent role.
resource "snowflake_grant_account_role" "to_admin" {
  for_each = { for r in local.functional_roles : r => r if r != "ADMIN" }

  role_name        = snowflake_account_role.this[each.key].name
  parent_role_name = snowflake_account_role.this["ADMIN"].name
}

resource "snowflake_grant_account_role" "admin_to_parent" {
  role_name        = snowflake_account_role.this["ADMIN"].name
  parent_role_name = var.parent_role
}
