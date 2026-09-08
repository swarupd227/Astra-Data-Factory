variable "name" {
  description = "Bucket name. Must be globally unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.name))
    error_message = "name must be a valid S3 bucket name: lower-case letters, digits, dots and hyphens, 3 to 63 characters."
  }
}

variable "noncurrent_version_expiration_days" {
  description = "Days after which non-current object versions are expired. Null keeps every version."
  type        = number
  default     = null

  validation {
    condition     = var.noncurrent_version_expiration_days == null || try(var.noncurrent_version_expiration_days >= 1, false)
    error_message = "noncurrent_version_expiration_days must be at least 1 or null."
  }
}

variable "object_lock_retention_days" {
  description = "When set, the bucket is created with S3 Object Lock and every new object version is retained, unmodifiable and undeletable, for this many days. Null means no object lock. Cannot be turned on after creation."
  type        = number
  default     = null

  validation {
    condition     = var.object_lock_retention_days == null || try(var.object_lock_retention_days >= 1, false)
    error_message = "object_lock_retention_days must be at least 1 or null."
  }
}

variable "object_lock_mode" {
  description = "GOVERNANCE (a principal with s3:BypassGovernanceRetention may still delete) or COMPLIANCE (nobody can, until the retention ends)."
  type        = string
  default     = "GOVERNANCE"

  validation {
    condition     = contains(["GOVERNANCE", "COMPLIANCE"], var.object_lock_mode)
    error_message = "object_lock_mode must be GOVERNANCE or COMPLIANCE."
  }
}

variable "tags" {
  description = "Tags added to the bucket in addition to the provider's default tags."
  type        = map(string)
  default     = {}
}
