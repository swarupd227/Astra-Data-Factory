mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

variables {
  name = "astra-dev-landing-123456789012"
}

run "hardens_the_bucket" {
  command = plan

  assert {
    condition     = aws_s3_bucket.this.bucket == "astra-dev-landing-123456789012"
    error_message = "Bucket must use the given name."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.this.block_public_acls &&
      aws_s3_bucket_public_access_block.this.block_public_policy &&
      aws_s3_bucket_public_access_block.this.ignore_public_acls &&
      aws_s3_bucket_public_access_block.this.restrict_public_buckets
    )
    error_message = "All public access must be blocked."
  }

  assert {
    condition     = anytrue([for r in aws_s3_bucket_ownership_controls.this.rule : r.object_ownership == "BucketOwnerEnforced"])
    error_message = "ACLs must be disabled."
  }

  assert {
    condition     = anytrue([for v in aws_s3_bucket_versioning.this.versioning_configuration : v.status == "Enabled"])
    error_message = "Versioning must be enabled."
  }

  assert {
    condition = anytrue([
      for r in aws_s3_bucket_server_side_encryption_configuration.this.rule :
      anytrue([for d in r.apply_server_side_encryption_by_default : d.sse_algorithm == "AES256"])
    ])
    error_message = "Objects must be encrypted with SSE-S3."
  }

  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.this) == 0
    error_message = "No lifecycle rule unless an expiry is requested."
  }
}

run "expires_noncurrent_versions_when_asked" {
  command = plan

  variables {
    noncurrent_version_expiration_days = 30
  }

  assert {
    condition = anytrue([
      for r in aws_s3_bucket_lifecycle_configuration.this[0].rule :
      r.status == "Enabled" && anytrue([for e in r.noncurrent_version_expiration : e.noncurrent_days == 30])
    ])
    error_message = "Lifecycle rule must expire non-current versions after the requested days."
  }
}

run "rejects_invalid_name" {
  command = plan

  variables {
    name = "Bad_Name"
  }

  expect_failures = [var.name]
}
