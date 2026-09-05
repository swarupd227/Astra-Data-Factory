# ---------------------------------------------------------------------------
# Bootstrap: the one thing that must exist before the foundation can run
# ---------------------------------------------------------------------------
#
# The foundation keeps its state in S3. That bucket cannot be created by the
# configuration whose state it holds, so it is created here, once per AWS
# account, with local state. The bucket is hardened by the same module the
# foundation uses for its own buckets.
#
#   terraform init && terraform apply
#
# Local state for this root is deliberate. It records one bucket whose name
# is derived from the account id; if the state file is lost, import it:
#   terraform import module.state_bucket.aws_s3_bucket.this <bucket name>

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Platform  = "astra-data-factory"
      ManagedBy = "terraform"
      Purpose   = "terraform-state"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  state_bucket_name = coalesce(var.state_bucket_name, "${var.prefix}-terraform-state-${data.aws_caller_identity.current.account_id}")
}

module "state_bucket" {
  source = "../foundation/modules/private-bucket"

  name                               = local.state_bucket_name
  noncurrent_version_expiration_days = var.state_history_days
}
