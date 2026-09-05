# Development environment. Only values that differ between environments live
# in this file; object definitions are shared (S1.2.1).

environment         = "dev"
data_retention_days = 1
aws_region          = "us-east-1"

# Open Catalog is enabled once the account exists and the catalog has been
# provisioned with `tools/opencatalog provision`. The client secret is never
# put in this file; export TF_VAR_open_catalog_client_secret from the secret
# manager. Example:
#
# open_catalog = {
#   account_url  = "https://artizent-astra_oc.snowflakecomputing.com"
#   catalog_name = "astra_dev"
#   iam_user_arn = "arn:aws:iam::123456789012:user/polaris-..."   # reported by provision
#   external_id  = "..."                                            # reported by provision
# }
# open_catalog_client_id = "..."   # sync service connection, from provision

warehouse_tiers = {
  simple = {
    size  = "XSMALL"
    users = ["ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"]
  }
  medium = {
    size = "XSMALL"
  }
  complex = {
    size = "SMALL"
  }
}
