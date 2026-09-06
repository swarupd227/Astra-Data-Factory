# ---------------------------------------------------------------------------
# Signed implied-decimal numerics (S2.2.3)
# ---------------------------------------------------------------------------
#
# The same function the reference implementation applies in Python
# (knowledge/src/astra_knowledge/patterns/numerics.py), shipped for the
# target platform so rendered parse code uses one definition:
#
#   CONTROL.IMPLIED_DECIMAL(digits, scale)
#     unsigned digits with `scale` implied decimals as a number
#   CONTROL.SIGNED_IMPLIED_DECIMAL(digits, sign, scale, positive, negative, unknown)
#     the number with its sign applied by the custodian's convention:
#     positive and negative are the sign codes that mean each; unknown are
#     the codes (blank included) that mean the sign is not known, which
#     makes the value NULL rather than zero. Anything else is NULL too.
#   CONTROL.SIGNED_IMPLIED_DECIMAL_PROBLEM(...)
#     why the value is NULL, or NULL when it is not; the renderer records it
#     as a record-level DQ finding
#
# The result scale is fixed at 12 decimals; the renderer casts to the
# column's own scale from the picture.

locals {
  implied_decimal_fqn        = "${local.control_fqn}.\"IMPLIED_DECIMAL\""
  signed_implied_decimal_fqn = "${local.control_fqn}.\"SIGNED_IMPLIED_DECIMAL\""

  # Places the decimal point `SCALE` digits from the right, after padding so
  # that fewer digits than the scale still work. Exact, no floating point.
  implied_decimal_definition = <<-SQL
    TO_NUMBER(
      LEFT(LPAD(TRIM(DIGITS), SCALE + 1, '0'), LENGTH(LPAD(TRIM(DIGITS), SCALE + 1, '0')) - SCALE)
        || IFF(SCALE > 0, '.' || RIGHT(LPAD(TRIM(DIGITS), SCALE + 1, '0'), SCALE), ''),
      38, 12)
  SQL

  sign_classification = <<-SQL
    CASE
      WHEN ARRAY_CONTAINS(SIGN::VARIANT, NEGATIVE) OR ARRAY_CONTAINS(TRIM(SIGN)::VARIANT, NEGATIVE) THEN 'negative'
      WHEN ARRAY_CONTAINS(SIGN::VARIANT, POSITIVE) OR ARRAY_CONTAINS(TRIM(SIGN)::VARIANT, POSITIVE) THEN 'positive'
      WHEN SIGN IS NULL OR ARRAY_CONTAINS(SIGN::VARIANT, UNKNOWN) OR ARRAY_CONTAINS(TRIM(SIGN)::VARIANT, UNKNOWN) THEN 'unknown'
      ELSE 'invalid'
    END
  SQL

  signed_implied_decimal_definition = <<-SQL
    CASE
      WHEN DIGITS IS NULL OR TRIM(DIGITS) = '' THEN NULL
      WHEN NOT REGEXP_LIKE(TRIM(DIGITS), '[0-9]+') THEN NULL
      WHEN (${local.sign_classification}) = 'negative' THEN -1 * ${local.implied_decimal_fqn}(DIGITS, SCALE)
      WHEN (${local.sign_classification}) = 'positive' THEN ${local.implied_decimal_fqn}(DIGITS, SCALE)
      WHEN (${local.sign_classification}) = 'unknown' AND ${local.implied_decimal_fqn}(DIGITS, SCALE) = 0 THEN 0
      ELSE NULL
    END
  SQL

  signed_implied_decimal_problem_definition = <<-SQL
    CASE
      WHEN DIGITS IS NULL OR TRIM(DIGITS) = '' THEN NULL
      WHEN NOT REGEXP_LIKE(TRIM(DIGITS), '[0-9]+') THEN '''' || TRIM(DIGITS) || ''' is not all digits'
      WHEN (${local.sign_classification}) IN ('negative', 'positive') THEN NULL
      WHEN (${local.sign_classification}) = 'unknown' THEN
        IFF(${local.implied_decimal_fqn}(DIGITS, SCALE) = 0, NULL, 'sign is blank or unknown; the value is unknown, not zero')
      ELSE 'sign ''' || SIGN || ''' is not an accepted sign'
    END
  SQL
}

resource "snowflake_function_sql" "implied_decimal" {
  name     = "IMPLIED_DECIMAL"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Unsigned digits with SCALE implied decimals as a number. Exact. Managed by Terraform."

  arguments {
    arg_name      = "DIGITS"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SCALE"
    arg_data_type = "NUMBER"
  }
  return_type             = "NUMBER(38, 12)"
  return_results_behavior = "IMMUTABLE"
  function_definition     = local.implied_decimal_definition

  depends_on = [snowflake_grant_privileges_to_account_role.future_objects]
}

resource "snowflake_function_sql" "signed_implied_decimal" {
  name     = "SIGNED_IMPLIED_DECIMAL"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Unsigned digits, a separate sign and implied decimals as a signed number, by the custodian's sign convention. Unknown sign is NULL, not zero. Managed by Terraform."

  arguments {
    arg_name      = "DIGITS"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SIGN"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SCALE"
    arg_data_type = "NUMBER"
  }
  arguments {
    arg_name      = "POSITIVE"
    arg_data_type = "ARRAY"
  }
  arguments {
    arg_name      = "NEGATIVE"
    arg_data_type = "ARRAY"
  }
  arguments {
    arg_name      = "UNKNOWN"
    arg_data_type = "ARRAY"
  }
  return_type             = "NUMBER(38, 12)"
  return_results_behavior = "IMMUTABLE"
  function_definition     = local.signed_implied_decimal_definition

  depends_on = [snowflake_function_sql.implied_decimal]
}

resource "snowflake_function_sql" "signed_implied_decimal_problem" {
  name     = "SIGNED_IMPLIED_DECIMAL_PROBLEM"
  database = snowflake_database.this.name
  schema   = snowflake_schema.this[local.control_schema].name
  comment  = "Why SIGNED_IMPLIED_DECIMAL returned NULL for the same arguments, or NULL when it did not. Managed by Terraform."

  arguments {
    arg_name      = "DIGITS"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SIGN"
    arg_data_type = "VARCHAR"
  }
  arguments {
    arg_name      = "SCALE"
    arg_data_type = "NUMBER"
  }
  arguments {
    arg_name      = "POSITIVE"
    arg_data_type = "ARRAY"
  }
  arguments {
    arg_name      = "NEGATIVE"
    arg_data_type = "ARRAY"
  }
  arguments {
    arg_name      = "UNKNOWN"
    arg_data_type = "ARRAY"
  }
  return_type             = "VARCHAR"
  return_results_behavior = "IMMUTABLE"
  function_definition     = local.signed_implied_decimal_problem_definition

  depends_on = [snowflake_function_sql.implied_decimal]
}
