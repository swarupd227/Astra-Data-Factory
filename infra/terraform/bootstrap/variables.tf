variable "aws_region" {
  description = "Region of the state bucket. Use the region the environments run in."
  type        = string
  default     = "us-east-1"
}

variable "prefix" {
  description = "Prefix of the state bucket name: <prefix>-terraform-state-<aws account id>."
  type        = string
  default     = "astra-data-factory"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", var.prefix))
    error_message = "prefix must be lower-case letters, digits and hyphens."
  }
}

variable "state_bucket_name" {
  description = "Explicit bucket name. Leave null to derive it from the prefix and the account id."
  type        = string
  default     = null

  validation {
    condition     = var.state_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid S3 bucket name."
  }
}

variable "state_history_days" {
  description = "How long superseded state versions are kept. Every apply writes a new version."
  type        = number
  default     = 90
}
