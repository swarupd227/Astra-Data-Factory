output "database_name" {
  description = "Name of the environment database."
  value       = snowflake_database.this.name
}

output "schema_names" {
  description = "Fully qualified names of the schemas, keyed by schema."
  value       = { for k, s in snowflake_schema.this : k => s.fully_qualified_name }
}

output "warehouse_names" {
  description = "Warehouse names keyed by tier."
  value       = { for k, w in snowflake_warehouse.tier : k => w.name }
}

output "warehouse_sizes" {
  description = "Warehouse sizes keyed by tier, as applied."
  value       = { for k, w in snowflake_warehouse.tier : k => w.warehouse_size }
}

output "role_names" {
  description = "Functional role names keyed by role."
  value       = { for k, r in snowflake_account_role.this : k => r.name }
}

output "resource_monitor_name" {
  description = "Name of the resource monitor, or null when no credit quota is set."
  value       = one(snowflake_resource_monitor.this[*].name)
}
