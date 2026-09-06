# Unit tests for the signed implied-decimal functions (S2.2.3).
#
# Mocked providers. The Python reference implementation carries the
# behavioural tests (knowledge/tests/test_numerics.py); these prove the SQL
# encodes the same rules and is wired as one function per concern.

mock_provider "snowflake" {}

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

variables {
  environment = "dev"
}

run "three_functions_in_control_with_fixed_result_types" {
  command = plan

  assert {
    condition = (
      snowflake_function_sql.implied_decimal.name == "IMPLIED_DECIMAL" &&
      snowflake_function_sql.signed_implied_decimal.name == "SIGNED_IMPLIED_DECIMAL" &&
      snowflake_function_sql.signed_implied_decimal_problem.name == "SIGNED_IMPLIED_DECIMAL_PROBLEM"
    )
    error_message = "The three functions exist under their documented names."
  }

  assert {
    condition = alltrue([
      for f in [snowflake_function_sql.implied_decimal, snowflake_function_sql.signed_implied_decimal, snowflake_function_sql.signed_implied_decimal_problem] :
      f.schema == "CONTROL" && f.return_results_behavior == "IMMUTABLE"
    ])
    error_message = "All three live in CONTROL and are immutable."
  }

  assert {
    condition     = snowflake_function_sql.signed_implied_decimal.return_type == "NUMBER(38, 12)" && snowflake_function_sql.signed_implied_decimal_problem.return_type == "VARCHAR"
    error_message = "The value function returns a 12-decimal number; the problem function returns text."
  }

  assert {
    condition     = [for a in snowflake_function_sql.signed_implied_decimal.arguments : a.arg_name] == ["DIGITS", "SIGN", "SCALE", "POSITIVE", "NEGATIVE", "UNKNOWN"]
    error_message = "The sign convention is passed as three arrays: positive, negative and unknown codes."
  }
}

run "the_sql_encodes_the_same_rules_as_the_reference_implementation" {
  command = plan

  assert {
    condition = alltrue([
      for needle in [
        "WHEN DIGITS IS NULL OR TRIM(DIGITS) = '' THEN NULL",
        "WHEN NOT REGEXP_LIKE(TRIM(DIGITS), '[0-9]+') THEN NULL",
        "= 'negative' THEN -1 * \"ASTRA_DEV\".\"CONTROL\".\"IMPLIED_DECIMAL\"(DIGITS, SCALE)",
        "= 'positive' THEN \"ASTRA_DEV\".\"CONTROL\".\"IMPLIED_DECIMAL\"(DIGITS, SCALE)",
        "= 'unknown' AND \"ASTRA_DEV\".\"CONTROL\".\"IMPLIED_DECIMAL\"(DIGITS, SCALE) = 0 THEN 0",
        "ELSE NULL",
      ] : strcontains(snowflake_function_sql.signed_implied_decimal.function_definition, needle)
    ])
    error_message = "Blank digits are NULL, invalid digits are NULL, negative and positive apply the sign, an unknown sign is NULL unless the magnitude is zero, anything else is NULL."
  }

  assert {
    condition     = strcontains(snowflake_function_sql.signed_implied_decimal_problem.function_definition, "'sign is blank or unknown; the value is unknown, not zero'") && strcontains(snowflake_function_sql.signed_implied_decimal_problem.function_definition, "is not an accepted sign")
    error_message = "The problem function explains an unknown or invalid sign."
  }

  assert {
    condition     = strcontains(snowflake_function_sql.implied_decimal.function_definition, "LPAD(TRIM(DIGITS), SCALE + 1, '0')") && strcontains(snowflake_function_sql.implied_decimal.function_definition, "38, 12)")
    error_message = "The decimal point is placed by string position, exactly, not by floating-point division."
  }
}
