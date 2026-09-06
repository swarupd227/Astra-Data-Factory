# Connection details and credentials are read from the environment. Nothing
# secret is ever written to a file in this repository (S1.2.5).
#
#   SNOWFLAKE_ORGANIZATION_NAME   e.g. ARTIZENT
#   SNOWFLAKE_ACCOUNT_NAME        e.g. ASTRA_DEV
#   SNOWFLAKE_USER                the Terraform service user
#   SNOWFLAKE_AUTHENTICATOR       SNOWFLAKE_JWT (key-pair authentication)
#   SNOWFLAKE_PRIVATE_KEY         PEM body of the service user's private key
#
# The role Terraform acts as is the only provider setting that is a variable,
# because it differs between the bootstrap run and steady-state runs.
provider "snowflake" {
  role = var.terraform_role

  # Resources the provider still marks as preview. They are pinned by the
  # provider version constraint and the lock file; review this list when the
  # provider is upgraded.
  preview_features_enabled = [
    "snowflake_storage_integration_aws_resource",
    "snowflake_file_format_csv_resource",
    "snowflake_stage_external_s3_resource",
    "snowflake_pipe_resource",
    "snowflake_iceberg_table_resource",
    "snowflake_procedure_sql_resource",
    "snowflake_email_notification_integration_resource",
    "snowflake_function_sql_resource",
  ]
}

# AWS credentials come from the environment or an assumed role (OIDC in CI).
# The bucket and IAM roles are created in the client's own AWS account.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Platform    = "astra-data-factory"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
