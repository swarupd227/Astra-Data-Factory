# ---------------------------------------------------------------------------
# AWS: Iceberg storage and the roles that may reach it (S1.1.2)
# ---------------------------------------------------------------------------
#
# One dedicated bucket per environment holds every Iceberg table's data and
# metadata. Two IAM roles read it: one assumed by Snowflake (read and write,
# it is the only writer) and one assumed by Open Catalog (read only, for the
# credentials it vends to external engines).

data "aws_caller_identity" "current" {}

locals {
  iceberg_bucket_name = coalesce(
    var.iceberg_bucket_name,
    "${lower(var.prefix)}-${var.environment}-iceberg-${data.aws_caller_identity.current.account_id}",
  )
  iceberg_base_url = "s3://${local.iceberg_bucket_name}/"

  snowflake_iceberg_role_name = "${local.name_prefix}_SNOWFLAKE_ICEBERG"
  snowflake_iceberg_role_arn  = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.snowflake_iceberg_role_name}"

  open_catalog_role_name   = "${local.name_prefix}_OPEN_CATALOG"
  open_catalog_role_arn    = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.open_catalog_role_name}"
  open_catalog_role_wanted = try(var.open_catalog.iam_user_arn, null) != null

  # Reported by Snowflake once the external volume exists. The IAM trust
  # policy is built from these, which is why the volume is created before
  # the role and the role ARN above is composed rather than read back.
  volume_s3 = snowflake_external_volume.iceberg.describe_output[0].storage_locations[0].s3_storage_location[0]
}

# --- Bucket ---------------------------------------------------------------

resource "aws_s3_bucket" "iceberg" {
  bucket = local.iceberg_bucket_name

  tags = {
    Name = local.iceberg_bucket_name
  }
}

resource "aws_s3_bucket_ownership_controls" "iceberg" {
  bucket = aws_s3_bucket.iceberg.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "iceberg" {
  bucket = aws_s3_bucket.iceberg.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "iceberg" {
  bucket = aws_s3_bucket.iceberg.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "iceberg" {
  bucket = aws_s3_bucket.iceberg.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "iceberg_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.iceberg.arn,
      "${aws_s3_bucket.iceberg.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "iceberg" {
  bucket = aws_s3_bucket.iceberg.id
  policy = data.aws_iam_policy_document.iceberg_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.iceberg]
}

# --- Role assumed by Snowflake --------------------------------------------

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
    resources = ["${aws_s3_bucket.iceberg.arn}/*"]
  }

  statement {
    sid = "BucketList"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.iceberg.arn]
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
    resources = ["${aws_s3_bucket.iceberg.arn}/*"]
  }

  statement {
    sid = "BucketList"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.iceberg.arn]
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
