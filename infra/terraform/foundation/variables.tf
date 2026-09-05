# ---------------------------------------------------------------------------
# Environment identity
# ---------------------------------------------------------------------------

variable "environment" {
  description = "Environment this configuration manages. Becomes part of every object name. The standard set is dev, qa, uat and prod; further short names (for example perf, dr) are allowed."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "environment must be 2 to 8 lower-case letters or digits, starting with a letter (dev, qa, uat, prod, perf, ...)."
  }
}

variable "prefix" {
  description = "Prefix for every object name. Objects are named <PREFIX>_<ENV>_<OBJECT>."
  type        = string
  default     = "ASTRA"

  validation {
    condition     = can(regex("^[A-Z][A-Z0-9]{0,15}$", var.prefix))
    error_message = "prefix must be upper-case letters and digits, start with a letter, and be at most 16 characters."
  }
}

# ---------------------------------------------------------------------------
# Roles Terraform runs as and hangs its hierarchy from
# ---------------------------------------------------------------------------

variable "terraform_role" {
  description = "Account role Terraform assumes. Must be able to create databases, warehouses and roles: SYSADMIN with SECURITYADMIN granted to it, or ACCOUNTADMIN for the bootstrap run."
  type        = string
  default     = "SYSADMIN"
}

variable "parent_role" {
  description = "Existing account role that the environment ADMIN role is granted to, so the environment sits under the account role hierarchy."
  type        = string
  default     = "SYSADMIN"
}

# ---------------------------------------------------------------------------
# Database and schemas
# ---------------------------------------------------------------------------

variable "data_retention_days" {
  description = "Time Travel retention for the database and its schemas, in days."
  type        = number
  default     = 1

  validation {
    condition     = var.data_retention_days >= 0 && var.data_retention_days <= 90 && floor(var.data_retention_days) == var.data_retention_days
    error_message = "data_retention_days must be a whole number between 0 and 90."
  }
}

variable "schemas" {
  description = <<-EOT
    Schemas to create in the environment database and who may use them.
    Each schema names the functional roles that read it, write it and create objects in it.
    Creators are implicitly writers, and writers are implicitly readers.
    Functional roles: ADMIN, ENGINEER, PIPELINE, STEWARD, CONSUMER, AUDITOR.
  EOT
  type = map(object({
    comment        = string
    readers        = optional(set(string), [])
    writers        = optional(set(string), [])
    creators       = optional(set(string), ["ENGINEER"])
    managed_access = optional(bool, true)
  }))
  default = {
    BRONZE = {
      comment = "Raw lines as landed from custodians. Written by Snowpipe and the parse layer."
      writers = ["PIPELINE"]
      readers = ["AUDITOR"]
    }
    SILVER = {
      comment = "Canonical data model tables after merge and resolution."
      writers = ["PIPELINE"]
      readers = ["STEWARD", "AUDITOR"]
    }
    GOLD = {
      comment = "Consumer read models and semantic views."
      writers = ["PIPELINE"]
      readers = ["STEWARD", "CONSUMER", "AUDITOR"]
    }
    EXCEPTIONS = {
      comment = "Exception store: rejected rows with code, payload and workflow state."
      writers = ["PIPELINE", "STEWARD"]
      readers = ["AUDITOR"]
    }
    CONTROL = {
      comment = "Run status, file arrival tracking and published watermarks."
      writers = ["PIPELINE"]
      readers = ["STEWARD", "CONSUMER", "AUDITOR"]
    }
  }

  validation {
    condition     = alltrue([for name, _ in var.schemas : can(regex("^[A-Z][A-Z0-9_]{0,62}$", name))])
    error_message = "Schema names must be upper-case letters, digits and underscores, starting with a letter."
  }

  validation {
    condition     = contains(keys(var.schemas), "BRONZE") && contains(keys(var.schemas), "CONTROL")
    error_message = "schemas must include BRONZE (raw lines) and CONTROL (file load log); the landing pipeline creates objects in both."
  }

  validation {
    condition = alltrue(flatten([
      for _, s in var.schemas : [
        for r in setunion(s.readers, s.writers, s.creators) :
        contains(["ADMIN", "ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"], r)
      ]
    ]))
    error_message = "Schema readers, writers and creators must be functional roles: ADMIN, ENGINEER, PIPELINE, STEWARD, CONSUMER, AUDITOR."
  }
}

# ---------------------------------------------------------------------------
# Warehouses, one per processing tier
# ---------------------------------------------------------------------------

variable "warehouse_tiers" {
  description = <<-EOT
    Warehouses by processing tier. Tiers simple, medium and complex are required; further tiers
    may be added. Size is the only value that must be set. `users` lists the functional roles
    granted USAGE. ADMIN always gets full control and AUDITOR always gets MONITOR.
  EOT
  type = map(object({
    size                      = string
    auto_suspend_seconds      = optional(number, 60)
    max_cluster_count         = optional(number, 1)
    statement_timeout_seconds = optional(number, 3600)
    users                     = optional(set(string), ["ENGINEER", "PIPELINE"])
    comment                   = optional(string)
  }))
  default = {
    simple = {
      size  = "XSMALL"
      users = ["ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"]
    }
    medium = {
      size = "SMALL"
    }
    complex = {
      size = "MEDIUM"
    }
  }

  validation {
    condition     = alltrue([for t in ["simple", "medium", "complex"] : contains(keys(var.warehouse_tiers), t)])
    error_message = "warehouse_tiers must define the simple, medium and complex tiers."
  }

  validation {
    condition     = alltrue([for name, _ in var.warehouse_tiers : can(regex("^[a-z][a-z0-9]{0,15}$", name))])
    error_message = "Tier names must be lower-case letters and digits, starting with a letter, at most 16 characters."
  }

  validation {
    condition = alltrue([
      for _, t in var.warehouse_tiers :
      contains(["XSMALL", "SMALL", "MEDIUM", "LARGE", "XLARGE", "XXLARGE", "XXXLARGE", "X4LARGE", "X5LARGE", "X6LARGE"], t.size)
    ])
    error_message = "Warehouse size must be one of XSMALL, SMALL, MEDIUM, LARGE, XLARGE, XXLARGE, XXXLARGE, X4LARGE, X5LARGE, X6LARGE."
  }

  validation {
    condition = alltrue([
      for _, t in var.warehouse_tiers :
      t.auto_suspend_seconds >= 60 && t.max_cluster_count >= 1 && t.max_cluster_count <= 10 && t.statement_timeout_seconds > 0
    ])
    error_message = "auto_suspend_seconds must be at least 60, max_cluster_count between 1 and 10, statement_timeout_seconds positive."
  }

  validation {
    condition = alltrue(flatten([
      for _, t in var.warehouse_tiers : [
        for r in t.users : contains(["ADMIN", "ENGINEER", "PIPELINE", "STEWARD", "CONSUMER", "AUDITOR"], r)
      ]
    ]))
    error_message = "Warehouse users must be functional roles: ADMIN, ENGINEER, PIPELINE, STEWARD, CONSUMER, AUDITOR."
  }
}

# ---------------------------------------------------------------------------
# Iceberg storage (S1.1.2)
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region of the Iceberg bucket. Should match the Snowflake account's region to avoid cross-region egress."
  type        = string
  default     = "us-east-1"
}

variable "iceberg_bucket_name" {
  description = "Name of the S3 bucket that holds Iceberg data and metadata. Leave null to derive <prefix>-<env>-iceberg-<aws account id>."
  type        = string
  default     = null

  validation {
    condition     = var.iceberg_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.iceberg_bucket_name))
    error_message = "iceberg_bucket_name must be a valid S3 bucket name: lower-case letters, digits, dots and hyphens, 3 to 63 characters."
  }
}

# ---------------------------------------------------------------------------
# Landing zone and Snowpipe (S1.1.3)
# ---------------------------------------------------------------------------

variable "landing_bucket_name" {
  description = "Name of the S3 bucket custodian files are delivered to. Leave null to derive <prefix>-<env>-landing-<aws account id>."
  type        = string
  default     = null

  validation {
    condition     = var.landing_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.landing_bucket_name))
    error_message = "landing_bucket_name must be a valid S3 bucket name: lower-case letters, digits, dots and hyphens, 3 to 63 characters."
  }
}

variable "landing_prefix" {
  description = "Key prefix inside the landing bucket that Snowpipe watches. Custodian folders live beneath it. No leading or trailing slash."
  type        = string
  default     = "landing"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_/-]*[a-z0-9]$", var.landing_prefix)) && !strcontains(var.landing_prefix, "//")
    error_message = "landing_prefix must be lower-case letters, digits, underscores, hyphens and single slashes, with no leading or trailing slash."
  }
}

variable "landing_noncurrent_version_days" {
  description = "Days to keep superseded versions of landed files (a re-delivered file overwrites the previous object; the old version stays this long). Null keeps every version."
  type        = number
  default     = 30
}

variable "landing_reconcile_interval_minutes" {
  description = "How often the file load log is reconciled against the landing zone and Snowpipe history. Duplicates are reported within this interval of arriving."
  type        = number
  default     = 1

  validation {
    condition     = var.landing_reconcile_interval_minutes >= 1 && var.landing_reconcile_interval_minutes <= 60 && floor(var.landing_reconcile_interval_minutes) == var.landing_reconcile_interval_minutes
    error_message = "landing_reconcile_interval_minutes must be a whole number between 1 and 60."
  }
}

# ---------------------------------------------------------------------------
# Snowflake Open Catalog (S1.1.2)
# ---------------------------------------------------------------------------

variable "open_catalog" {
  description = <<-EOT
    Snowflake Open Catalog that Iceberg table metadata is synced to, so that external engines
    (pg_lake, Spark, DuckDB) read the same files through the Iceberg REST API. Leave null until
    the Open Catalog account exists and the catalog has been provisioned with
    tools/opencatalog. iam_user_arn and external_id are reported by that provisioning step;
    once set, the IAM role Open Catalog assumes to read the bucket is created.
  EOT
  type = object({
    account_url  = string
    catalog_name = string
    iam_user_arn = optional(string)
    external_id  = optional(string)
  })
  default = null

  validation {
    condition     = var.open_catalog == null || can(regex("^https://[a-z0-9._-]+\\.snowflakecomputing\\.com$", var.open_catalog.account_url))
    error_message = "open_catalog.account_url must be https://<org>-<account>.snowflakecomputing.com with no trailing path."
  }

  validation {
    condition     = var.open_catalog == null || can(regex("^[a-z][a-z0-9_]{0,62}$", var.open_catalog.catalog_name))
    error_message = "open_catalog.catalog_name must be lower-case letters, digits and underscores, starting with a letter."
  }

  validation {
    condition     = var.open_catalog == null || (var.open_catalog_client_id != null && var.open_catalog_client_secret != null)
    error_message = "open_catalog requires open_catalog_client_id and open_catalog_client_secret (supply the secret via TF_VAR_open_catalog_client_secret, never in a file)."
  }

  validation {
    condition     = var.open_catalog == null || ((var.open_catalog.iam_user_arn == null) == (var.open_catalog.external_id == null))
    error_message = "open_catalog.iam_user_arn and open_catalog.external_id must be set together."
  }
}

variable "open_catalog_client_id" {
  description = "OAuth client id of the Open Catalog service connection that Snowflake uses to sync table metadata."
  type        = string
  default     = null
}

variable "open_catalog_client_secret" {
  description = "OAuth client secret of that service connection. Supply from the secret manager via TF_VAR_open_catalog_client_secret."
  type        = string
  default     = null
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Cost control
# ---------------------------------------------------------------------------

variable "monthly_credit_quota" {
  description = "Monthly credit quota for a resource monitor attached to every warehouse in this environment. Leave null to create no monitor. Creating a monitor requires the ACCOUNTADMIN role."
  type        = number
  default     = null

  validation {
    condition     = var.monthly_credit_quota == null || try(var.monthly_credit_quota > 0, false)
    error_message = "monthly_credit_quota must be a positive number of credits or null."
  }
}
