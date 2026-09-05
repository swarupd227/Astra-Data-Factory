# UAT environment. Sized like production so the 3x volume test (S4.3.1) is
# representative.

environment         = "uat"
data_retention_days = 7

warehouse_tiers = {
  simple = {
    size  = "XSMALL"
    users = ["ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"]
  }
  medium = {
    size = "SMALL"
  }
  complex = {
    size              = "MEDIUM"
    max_cluster_count = 2
  }
}
