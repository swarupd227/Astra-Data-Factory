# Remote state for the prod environment. The bucket is created once per AWS
# account by infra/terraform/bootstrap; replace the placeholder with its
# output state_bucket_name. Nothing here is secret.
bucket       = "astra-data-factory-terraform-state-<aws account id>"
key          = "foundation/prod/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
