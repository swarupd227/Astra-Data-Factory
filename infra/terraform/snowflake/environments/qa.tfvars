# QA environment. Generated tests and dry-runs execute here; sizes match dev.

environment         = "qa"
data_retention_days = 1

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
