# Development environment. Only values that differ between environments live
# in this file; object definitions are shared (S1.2.1).

environment         = "dev"
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
