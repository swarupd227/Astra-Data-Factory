# Remote state for the qa environment. The bucket is created once per AWS
# account by infra/terraform/bootstrap; replace the placeholder with its
# output state_bucket_name. Nothing here is secret.
bucket       = "astra-data-factory-terraform-state-<aws account id>"
key          = "foundation/qa/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
