locals {
  name_prefix   = "${var.prefix}_${upper(var.environment)}"
  database_name = local.name_prefix

  # Functional roles created for every environment. The names are stable so
  # that grants, CI and the workbench can refer to them without lookup.
  functional_roles = ["ADMIN", "ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR", "SANDBOX"]
  role_names       = { for r in local.functional_roles : r => "${local.name_prefix}_${r}" }

  warehouse_names = { for tier, _ in var.warehouse_tiers : tier => "${local.name_prefix}_WH_${upper(tier)}" }

  # Privileges granted on future objects in a schema, by access level.
  # Future grants (rather than grants on existing objects) keep the foundation
  # free of drift on an empty account and cover every object the generation
  # plane creates later.
  read_object_privileges = {
    "TABLES"             = ["SELECT"]
    "ICEBERG TABLES"     = ["SELECT"]
    "VIEWS"              = ["SELECT"]
    "DYNAMIC TABLES"     = ["SELECT"]
    "MATERIALIZED VIEWS" = ["SELECT"]
    "STREAMS"            = ["SELECT"]
  }
  write_object_privileges = {
    "TABLES"                = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]
    "ICEBERG TABLES"        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]
    "VIEWS"                 = ["SELECT"]
    "DYNAMIC TABLES"        = ["SELECT", "MONITOR", "OPERATE"]
    "MATERIALIZED VIEWS"    = ["SELECT"]
    "STREAMS"               = ["SELECT"]
    "STAGES"                = ["USAGE"]
    "FILE FORMATS"          = ["USAGE"]
    "FUNCTIONS"             = ["USAGE"]
    "PROCEDURES"            = ["USAGE"]
    "SEQUENCES"             = ["USAGE"]
    "TASKS"                 = ["MONITOR", "OPERATE"]
    "PIPES"                 = ["MONITOR", "OPERATE"]
    "DATA METRIC FUNCTIONS" = ["USAGE"]
  }

  # Everything the generation plane renders (spec Appendix A) must be creatable
  # by the role CI deploys as.
  schema_create_privileges = [
    "CREATE TABLE",
    "CREATE ICEBERG TABLE",
    "CREATE VIEW",
    "CREATE MATERIALIZED VIEW",
    "CREATE DYNAMIC TABLE",
    "CREATE STAGE",
    "CREATE FILE FORMAT",
    "CREATE FUNCTION",
    "CREATE PROCEDURE",
    "CREATE SEQUENCE",
    "CREATE STREAM",
    "CREATE TASK",
    "CREATE PIPE",
    "CREATE DATA METRIC FUNCTION",
  ]

  # One entry per (schema, role) pair that has any access to the schema.
  schema_role_access = merge([
    for schema, cfg in var.schemas : {
      for role in setunion(cfg.readers, cfg.writers, cfg.creators) :
      "${schema}.${role}" => {
        schema  = schema
        role    = role
        writer  = contains(cfg.writers, role) || contains(cfg.creators, role)
        creator = contains(cfg.creators, role)
      }
    }
  ]...)

  # One entry per (schema, role, future object type).
  future_object_grants = merge([
    for key, access in local.schema_role_access : {
      for object_type, privileges in(access.writer ? local.write_object_privileges : local.read_object_privileges) :
      "${key}.${object_type}" => {
        schema      = access.schema
        role        = access.role
        object_type = object_type
        privileges  = privileges
      }
    }
  ]...)

  # Warehouse privileges per role. ADMIN controls every warehouse, AUDITOR can
  # monitor every warehouse, PIPELINE can operate the ones it uses, and every
  # listed user gets USAGE.
  warehouse_role_grants = merge([
    for tier, cfg in var.warehouse_tiers : {
      for role in setunion(cfg.users, ["ADMIN", "AUDITOR"]) :
      "${tier}.${role}" => {
        tier = tier
        role = role
        privileges = (
          role == "ADMIN" ? ["MODIFY", "MONITOR", "OPERATE", "USAGE"] :
          role == "AUDITOR" ? (contains(cfg.users, role) ? ["MONITOR", "USAGE"] : ["MONITOR"]) :
          role == "PIPELINE" ? ["OPERATE", "USAGE"] :
          ["USAGE"]
        )
      }
    }
  ]...)

  # Roles that run or deploy Tasks need account-level task execution privileges.
  task_execution_roles = ["ENGINEER", "PIPELINE"]
}
