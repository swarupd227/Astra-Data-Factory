terraform {
  required_version = ">= 1.10.0"

  required_providers {
    snowflake = {
      source = "snowflakedb/snowflake"
      # Preview resources (see providers.tf) may change between minor
      # versions; the lock file pins the exact version that was tested.
      version = "~> 2.20"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
