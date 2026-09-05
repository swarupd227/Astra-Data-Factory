# QA environment. Generated tests and dry-runs execute here; sizes match dev.

environment         = "qa"
data_retention_days = 1
aws_region          = "us-east-1"

# open_catalog: see dev.tfvars for the shape; set after provisioning.

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
