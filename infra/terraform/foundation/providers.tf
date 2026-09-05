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
