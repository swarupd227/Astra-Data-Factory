"""Terraform for one source: the infrastructure the bundle needs, as a root module whose plan is clean when it is there.

The bundle's Snowflake objects are deployed as SQL (ADR 0018); what the
bundle takes from the foundation is infrastructure it does not create:
the tier warehouse, the schemas it writes, the PII tag, the landing
bucket and the custodian's prefix in it. This module reads each with a
data source and fails the plan when one is missing or wrong, so a deploy
never starts against an environment that cannot hold the bundle, and an
environment change that removes something a bundle needs shows up as a
failed plan, not as a failed deploy half way through (ADR 0027).

The outputs are what an operator or a downstream tool needs to hand the
custodian: the landing URL its files go to, the pipe that loads them, the
warehouse that processes them.
"""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import WAREHOUSE_BY_TIER, custodian_folder, gate_task_name, pipe_name, task_name

SCHEMAS = ("BRONZE", "SILVER", "EXCEPTIONS", "CONTROL")
REQUIRED_VERSION = ">= 1.10.0"
SNOWFLAKE_PROVIDER = "~> 2.20"
AWS_PROVIDER = "~> 6.0"


def _tier_suffix(compiled: CompiledConfig) -> str:
    """WAREHOUSE_MEDIUM -> WH_MEDIUM, the foundation's warehouse name suffix for the tier."""
    placeholder = WAREHOUSE_BY_TIER[compiled.source["tier"]]
    return "WH_" + placeholder.strip("{} ").split("_", 1)[1]


def render_versions(compiled: CompiledConfig) -> str:
    return f"""# Prerequisites of the {compiled.id} bundle. Rendered by astra-data render; do not edit.
terraform {{
  required_version = "{REQUIRED_VERSION}"

  required_providers {{
    snowflake = {{
      source  = "snowflakedb/snowflake"
      version = "{SNOWFLAKE_PROVIDER}"
    }}
    aws = {{
      source  = "hashicorp/aws"
      version = "{AWS_PROVIDER}"
    }}
  }}
}}

# Credentials come from the environment, as for the foundation: SNOWFLAKE_* and AWS_* variables.
provider "snowflake" {{
  role = var.snowflake_role
}}

provider "aws" {{}}
"""


def render_variables(compiled: CompiledConfig) -> str:
    return f"""# Rendered by astra-data render; do not edit.
variable "environment" {{
  description = "Environment the bundle is checked against: dev, qa, uat or prod."
  type        = string

  validation {{
    condition     = can(regex("^[a-z][a-z0-9]{{1,7}}$", var.environment))
    error_message = "environment must be 2 to 8 lower-case letters or digits, starting with a letter."
  }}
}}

variable "prefix" {{
  description = "Naming prefix of the foundation; the database is <PREFIX>_<ENVIRONMENT>."
  type        = string
  default     = "ASTRA"
}}

variable "snowflake_role" {{
  description = "Role the check runs as; it needs USAGE on the database and the schemas."
  type        = string
  default     = null
}}

variable "landing_bucket_name" {{
  description = "Landing bucket; defaults to the foundation's name, <prefix>-<environment>-landing-<account id>."
  type        = string
  default     = null
}}

variable "landing_prefix" {{
  description = "Prefix under which custodians deliver, as in the foundation."
  type        = string
  default     = "landing"
}}
"""


def render_main(compiled: CompiledConfig) -> str:
    source = compiled.id
    custodian = compiled.source["custodian"]
    tier = compiled.source["tier"]
    folder = (custodian_folder(compiled) or custodian).strip("/")
    schema_checks = "\n".join(
        f"""
data "snowflake_schemas" "{schema.lower()}" {{
  like            = "{schema}"
  with_describe   = false
  with_parameters = false

  in {{
    database = local.database
  }}

  lifecycle {{
    postcondition {{
      condition     = length(self.schemas) == 1
      error_message = "Schema ${{local.database}}.{schema} is missing; the {source} bundle {'writes' if schema != 'CONTROL' else 'reads and writes'} it. Apply the foundation first."
    }}
  }}
}}"""
        for schema in SCHEMAS
    )
    return f"""# What the {source} bundle needs from the foundation (ADR 0027). A clean plan means the environment can hold the
# bundle; a failed postcondition names what is missing. Rendered by astra-data render; do not edit.

data "aws_caller_identity" "current" {{}}

locals {{
  database       = "${{upper(var.prefix)}}_${{upper(var.environment)}}"
  warehouse      = "${{local.database}}_{_tier_suffix(compiled)}"
  landing_bucket = coalesce(var.landing_bucket_name, "${{lower(var.prefix)}}-${{var.environment}}-landing-${{data.aws_caller_identity.current.account_id}}")
  landing_folder = "${{var.landing_prefix}}/{folder}/"
}}

# The {tier} tier warehouse the process task runs on.
data "snowflake_warehouses" "tier" {{
  like            = local.warehouse
  with_describe   = false
  with_parameters = false

  lifecycle {{
    postcondition {{
      condition     = length(self.warehouses) == 1
      error_message = "Warehouse ${{local.warehouse}} ({tier} tier) is missing; the {source} process task runs on it. Apply the foundation first."
    }}
  }}
}}
{schema_checks}

# The PII tag the bundle binds to raw lines, mapped PII columns and exception payloads.
data "snowflake_tags" "pii" {{
  like = "PII"

  in {{
    schema = "${{local.database}}.CONTROL"
  }}

  lifecycle {{
    postcondition {{
      condition     = length(self.tags) == 1
      error_message = "Tag ${{local.database}}.CONTROL.PII is missing; the {source} bundle tags PII columns with it. Apply the foundation first."
    }}
  }}
}}

# The landing bucket the pipe reads from. A missing bucket fails the read itself.
data "aws_s3_bucket" "landing" {{
  bucket = local.landing_bucket
}}

output "database" {{
  value = local.database
}}

output "warehouse" {{
  description = "Warehouse of the {tier} tier that processes {source}."
  value       = local.warehouse
}}

output "landing_url" {{
  description = "Where custodian {custodian} delivers the files of {source}."
  value       = "s3://${{data.aws_s3_bucket.landing.bucket}}/${{local.landing_folder}}"
}}

output "pipe" {{
  description = "Pipe that loads the files, in BRONZE."
  value       = "${{local.database}}.BRONZE.{pipe_name(compiled)}"
}}

output "tasks" {{
  description = "The custodian's gate and this source's process task, in BRONZE."
  value       = ["${{local.database}}.BRONZE.{gate_task_name(compiled)}", "${{local.database}}.BRONZE.{task_name(compiled)}"]
}}
"""


def render_test(compiled: CompiledConfig) -> str:
    source = compiled.id
    tier = compiled.source["tier"]
    schema_overrides = "\n".join(
        f"""
  override_data {{
    target = data.snowflake_schemas.{schema.lower()}
    values = {{
      schemas = [{{ show_output = [{{ name = "{schema}" }}] }}]
    }}
  }}"""
        for schema in SCHEMAS
    )
    schema_asserts = "\n".join(
        f"""
  assert {{
    condition     = length(data.snowflake_schemas.{schema.lower()}.schemas) == 1
    error_message = "The bundle checks that {schema} exists."
  }}"""
        for schema in SCHEMAS
    )
    return f"""# Unit test of the {source} prerequisites module, with mocked providers: the plan is clean when the
# foundation provides the warehouse, schemas, tag and bucket, and fails naming the missing piece otherwise.
# Rendered by astra-data render; do not edit.

mock_provider "snowflake" {{}}

mock_provider "aws" {{
  mock_data "aws_caller_identity" {{
    defaults = {{
      account_id = "123456789012"
    }}
  }}
}}

variables {{
  environment = "dev"
}}

run "plan_is_clean_when_the_foundation_provides_everything" {{
  command = plan

  override_data {{
    target = data.snowflake_warehouses.tier
    values = {{
      warehouses = [{{ show_output = [{{ name = "ASTRA_DEV_{_tier_suffix(compiled)}" }}] }}]
    }}
  }}
{schema_overrides}

  override_data {{
    target = data.snowflake_tags.pii
    values = {{
      tags = [{{ show_output = [{{ name = "PII" }}] }}]
    }}
  }}

  override_data {{
    target = data.aws_s3_bucket.landing
    values = {{
      bucket = "astra-dev-landing-123456789012"
    }}
  }}

  assert {{
    condition     = output.database == "ASTRA_DEV" && output.warehouse == "ASTRA_DEV_{_tier_suffix(compiled)}"
    error_message = "The database and the {tier} tier warehouse follow the foundation's naming."
  }}
{schema_asserts}

  assert {{
    condition     = output.landing_url == "s3://astra-dev-landing-123456789012/landing/{(custodian_folder(compiled) or compiled.source['custodian']).strip('/')}/"
    error_message = "The landing URL is the custodian's folder under the landing prefix."
  }}
}}

run "a_missing_warehouse_fails_the_plan" {{
  command = plan

  override_data {{
    target = data.snowflake_warehouses.tier
    values = {{
      warehouses = []
    }}
  }}
{schema_overrides}

  override_data {{
    target = data.snowflake_tags.pii
    values = {{
      tags = [{{ show_output = [{{ name = "PII" }}] }}]
    }}
  }}

  override_data {{
    target = data.aws_s3_bucket.landing
    values = {{
      bucket = "astra-dev-landing-123456789012"
    }}
  }}

  expect_failures = [data.snowflake_warehouses.tier]
}}
"""


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {
        "terraform/versions.tf": render_versions(compiled),
        "terraform/variables.tf": render_variables(compiled),
        "terraform/main.tf": render_main(compiled),
        "terraform/tests/prerequisites.tftest.hcl": render_test(compiled),
    }
