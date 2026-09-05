# Production environment.
#
# monthly_credit_quota is intentionally unset here: the client sets its own
# credit target (spec Section 16, cost per source within client target) and the
# monitor must be created by ACCOUNTADMIN. Set it in this file once agreed.

environment         = "prod"
data_retention_days = 7
aws_region          = "us-east-1"

# open_catalog: see dev.tfvars for the shape; set after provisioning.

warehouse_tiers = {
  simple = {
    size                 = "XSMALL"
    auto_suspend_seconds = 120
    users                = ["ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"]
  }
  medium = {
    size                 = "SMALL"
    auto_suspend_seconds = 120
  }
  complex = {
    size                 = "MEDIUM"
    auto_suspend_seconds = 120
    max_cluster_count    = 2
  }
}
