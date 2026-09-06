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

output "iceberg_bucket_name" {
  description = "S3 bucket holding Iceberg data and metadata."
  value       = module.iceberg_bucket.name
}

output "landing_bucket_name" {
  description = "S3 bucket custodian files are delivered to."
  value       = module.landing_bucket.name
}

output "landing_base_url" {
  description = "Location Snowpipe watches. Files dropped beneath it appear in BRONZE.RAW_LINES."
  value       = local.landing_base_url
}

output "landing_stage" {
  description = "Fully qualified name of the external stage over the landing zone."
  value       = local.landing_stage_fqn
}

output "raw_lines_table" {
  description = "Fully qualified name of the Bronze raw-lines table."
  value       = local.raw_lines_table_fqn
}

output "raw_lines_pipe" {
  description = "Fully qualified name of the auto-ingest pipe."
  value       = snowflake_pipe.raw_lines.fully_qualified_name
}

output "file_load_log_table" {
  description = "Fully qualified name of the file load log (LOADED, FAILED, DUPLICATE, CONFLICT per arrival)."
  value       = local.file_load_log_fqn
}

output "snowpipe_notification_channel" {
  description = "SQS queue ARN Snowflake listens on for this account and region; the landing bucket notification targets it."
  value       = snowflake_pipe.raw_lines.notification_channel
}

output "iceberg_base_url" {
  description = "Base location of Iceberg tables; also the default-base-location of the Open Catalog catalog."
  value       = local.iceberg_base_url
}

output "external_volume_name" {
  description = "Snowflake external volume the database writes Iceberg tables to."
  value       = snowflake_external_volume.iceberg.name
}

output "snowflake_iceberg_role_arn" {
  description = "IAM role Snowflake assumes to reach the bucket."
  value       = aws_iam_role.snowflake_iceberg.arn
}

output "open_catalog_role_arn" {
  description = "IAM role Open Catalog assumes to read the bucket. Pass this as --role-arn to tools/opencatalog provision. The role itself exists only after open_catalog.iam_user_arn is set."
  value       = local.open_catalog_role_arn
}

output "open_catalog_role_created" {
  description = "Whether the Open Catalog IAM role exists yet."
  value       = local.open_catalog_role_wanted
}

output "secrets_prefix" {
  description = "Secrets Manager name prefix of this environment's secrets. Set as the SECRETS_PREFIX variable of the GitHub environment."
  value       = local.secrets_prefix
}

output "secret_arns" {
  description = "ARNs of the managed secrets, keyed by name. Values are set by an operator, never by Terraform."
  value       = { for k, s in aws_secretsmanager_secret.managed : k => s.arn }
}

output "read_secrets_policy_arn" {
  description = "IAM policy granting read on the managed secrets. Attach to the deploy role."
  value       = aws_iam_policy.read_secrets.arn
}

output "open_catalog_integration_name" {
  description = "Catalog integration syncing this database to Open Catalog, or null when Open Catalog is not configured."
  value       = one(snowflake_catalog_integration_open_catalog.sync[*].name)
}
