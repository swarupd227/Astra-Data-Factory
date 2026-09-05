# Remote state lives in the client's own account (spec Section 12: the factory
# holds references only). This is a partial configuration; the bucket, key and
# region for each environment are supplied at init time from
# environments/backend-<env>.hcl so no environment detail is hard-coded here.
#
#   terraform init -backend-config=environments/backend-dev.hcl
#
# Local validation and `terraform test` run with `terraform init -backend=false`.
terraform {
  backend "s3" {}
}
