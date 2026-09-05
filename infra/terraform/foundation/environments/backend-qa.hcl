# Remote state for the qa environment. The bucket belongs to the client
# account that hosts the environment; replace the placeholder name when the
# account is provisioned. Nothing here is secret.
bucket       = "astra-data-factory-terraform-state"
key          = "snowflake/qa/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
