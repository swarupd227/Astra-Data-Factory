# ---------------------------------------------------------------------------
# AWS: buckets, the roles that may reach them, and event wiring
# ---------------------------------------------------------------------------
#
# Two buckets per environment, both hardened by modules/private-bucket:
#
#   iceberg   Iceberg data and metadata (S1.1.2). Snowflake writes, Open
#             Catalog reads on behalf of external engines.
#   landing   Files as delivered by custodians (S1.1.3). Snowpipe reads
#             on S3 object-created events.
#
# Three IAM roles, one per consumer, each with its own trust and least
# privilege. Role ARNs are composed from the account id and a fixed name so
# Snowflake objects that need the ARN can be created before the role whose
# trust policy needs what Snowflake reports back.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  iceberg_bucket_name = coalesce(var.iceberg_bucket_name, "${lower(var.prefix)}-${var.environment}-iceberg-${local.account_id}")
  landing_bucket_name = coalesce(var.landing_bucket_name, "${lower(var.prefix)}-${var.environment}-landing-${local.account_id}")

  iceberg_base_url = "s3://${local.iceberg_bucket_name}/"
  landing_base_url = "s3://${local.landing_bucket_name}/${var.landing_prefix}/"

  snowflake_iceberg_role_name = "${local.name_prefix}_SNOWFLAKE_ICEBERG"
  snowflake_iceberg_role_arn  = "arn:aws:iam::${local.account_id}:role/${local.snowflake_iceberg_role_name}"

  snowflake_landing_role_name = "${local.name_prefix}_SNOWFLAKE_LANDING"
  snowflake_landing_role_arn  = "arn:aws:iam::${local.account_id}:role/${local.snowflake_landing_role_name}"

  open_catalog_role_name   = "${local.name_prefix}_OPEN_CATALOG"
  open_catalog_role_arn    = "arn:aws:iam::${local.account_id}:role/${local.open_catalog_role_name}"
  open_catalog_role_wanted = try(var.open_catalog.iam_user_arn, null) != null

  # Reported by Snowflake once the external volume exists.
  volume_s3 = snowflake_external_volume.iceberg.describe_output[0].storage_locations[0].s3_storage_location[0]
}

# --- Buckets --------------------------------------------------------------

module "iceberg_bucket" {
  source = "./modules/private-bucket"

  name = local.iceberg_bucket_name
}

module "landing_bucket" {
  source = "./modules/private-bucket"

  name                               = local.landing_bucket_name
  noncurrent_version_expiration_days = var.landing_noncurrent_version_days
}

# --- Role assumed by Snowflake for Iceberg storage ------------------------

data "aws_iam_policy_document" "snowflake_iceberg_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [local.volume_s3.storage_aws_iam_user_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [local.volume_s3.storage_aws_external_id]
    }
  }
}

data "aws_iam_policy_document" "snowflake_iceberg_access" {
  statement {
    sid = "ObjectReadWrite"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = ["${module.iceberg_bucket.arn}/*"]
  }

  statement {
    sid = "BucketList"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [module.iceberg_bucket.arn]
  }
}

resource "aws_iam_role" "snowflake_iceberg" {
  name               = local.snowflake_iceberg_role_name
  description        = "Assumed by Snowflake to read and write Iceberg data for ${local.name_prefix}."
  assume_role_policy = data.aws_iam_policy_document.snowflake_iceberg_trust.json
}

resource "aws_iam_role_policy" "snowflake_iceberg" {
  name   = "iceberg-bucket-access"
  role   = aws_iam_role.snowflake_iceberg.id
  policy = data.aws_iam_policy_document.snowflake_iceberg_access.json
}

# --- Role assumed by Snowflake for the landing zone -----------------------
#
# Read only, limited to the landing prefix. Snowpipe never deletes; retention
# in the landing bucket is a lifecycle decision, not a pipeline side effect.

data "aws_iam_policy_document" "snowflake_landing_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [snowflake_storage_integration_aws.landing.describe_output[0].iam_user_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [snowflake_storage_integration_aws.landing.describe_output[0].external_id]
    }
  }
}

data "aws_iam_policy_document" "snowflake_landing_access" {
  statement {
    sid = "ObjectRead"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [
      "${module.landing_bucket.arn}/${var.landing_prefix}/*",
      "${module.landing_bucket.arn}/${var.sandbox_prefix}/*",
    ]
  }

  statement {
    sid = "BucketList"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [module.landing_bucket.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.landing_prefix}/*", "${var.sandbox_prefix}/*"]
    }
  }
}

resource "aws_iam_role" "snowflake_landing" {
  name               = local.snowflake_landing_role_name
  description        = "Assumed by Snowflake (Snowpipe) to read the ${local.name_prefix} landing zone."
  assume_role_policy = data.aws_iam_policy_document.snowflake_landing_trust.json
}

resource "aws_iam_role_policy" "snowflake_landing" {
  name   = "landing-bucket-read"
  role   = aws_iam_role.snowflake_landing.id
  policy = data.aws_iam_policy_document.snowflake_landing_access.json
}

# --- S3 events to Snowpipe ------------------------------------------------
#
# Snowflake owns one SQS queue per account and region for auto-ingest; every
# pipe and every auto-refreshing directory table reports the same ARN. One
# notification rule on the landing prefix therefore feeds the raw-lines pipe
# and the stage's directory table, and any per-custodian pipe the generation
# plane adds later under the same prefix.

resource "aws_s3_bucket_notification" "landing" {
  bucket = module.landing_bucket.id

  queue {
    id            = "snowpipe-${var.landing_prefix}"
    queue_arn     = snowflake_pipe.raw_lines.notification_channel
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "${var.landing_prefix}/"
  }
}

# --- Role assumed by Open Catalog -----------------------------------------
#
# Created once the catalog has been provisioned and has reported the IAM user
# and external id it will assume the role with (see tools/opencatalog).

data "aws_iam_policy_document" "open_catalog_trust" {
  count = local.open_catalog_role_wanted ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.open_catalog.iam_user_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.open_catalog.external_id]
    }
  }
}

data "aws_iam_policy_document" "open_catalog_access" {
  count = local.open_catalog_role_wanted ? 1 : 0

  statement {
    sid = "ObjectRead"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = ["${module.iceberg_bucket.arn}/*"]
  }

  statement {
    sid = "BucketList"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [module.iceberg_bucket.arn]
  }
}

resource "aws_iam_role" "open_catalog" {
  count = local.open_catalog_role_wanted ? 1 : 0

  name               = local.open_catalog_role_name
  description        = "Assumed by Snowflake Open Catalog to read Iceberg data for ${local.name_prefix} on behalf of external engines."
  assume_role_policy = data.aws_iam_policy_document.open_catalog_trust[0].json
}

resource "aws_iam_role_policy" "open_catalog" {
  count = local.open_catalog_role_wanted ? 1 : 0

  name   = "iceberg-bucket-read"
  role   = aws_iam_role.open_catalog[0].id
  policy = data.aws_iam_policy_document.open_catalog_access[0].json
}
