# Unit tests for per-custodian orchestration (S3.2.6).
#
# Mocked providers. The DAG itself is rendered per source by astra-data
# (generation/tests/test_render_tasks.py); this checks the control objects
# the rendered gate task calls and writes.

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

run "custodian_runs_records_every_start_with_its_reason" {
  command = plan

  assert {
    condition     = [for c in snowflake_iceberg_table.custodian_runs.column : c.name] == ["RUN_ID", "CUSTODIAN_ID", "BUSINESS_DATE", "STARTED_AT", "REASON", "FILES", "LATEST_ARRIVAL_AT", "AFTER_CUTOFF", "LATE_FILES"]
    error_message = "CUSTODIAN_RUNS records which business date started, why, what it saw and whether it was after the cutoff."
  }

  assert {
    condition     = snowflake_iceberg_table.custodian_runs.base_location == "control/custodian_runs/"
    error_message = "CUSTODIAN_RUNS lives under the control prefix of the Iceberg volume."
  }
}

run "gate_runs_only_a_complete_file_set_with_a_new_arrival" {
  command = plan

  assert {
    condition     = snowflake_procedure_sql.custodian_gate.return_type == "STRING" && snowflake_procedure_sql.custodian_gate.execute_as == "OWNER" && [for a in snowflake_procedure_sql.custodian_gate.arguments : a.arg_name] == ["CUSTODIAN_ID"]
    error_message = "CUSTODIAN_GATE takes the custodian id, runs as owner and returns 'run' or 'wait'."
  }

  assert {
    condition = alltrue([
      for needle in [
        # the expected file set comes from CUSTODIAN_FILES, arrivals from FILE_LOAD_LOG, both for one custodian
        "WHERE CUSTODIAN_ID = :CUSTODIAN_ID AND ENABLED",
        "SELECT COUNT(*) AS PATTERNS FROM \"ASTRA_DEV\".\"CONTROL\".\"CUSTODIAN_FILES\" WHERE CUSTODIAN_ID = :CUSTODIAN_ID",
        "JOIN \"ASTRA_DEV\".\"CONTROL\".\"FILE_LOAD_LOG\" l ON l.FILE_NAME LIKE f.FILE_PATTERN AND l.STATUS = 'LOADED'",
        # a file's business date is the same one the late detector uses
        "CONVERT_TIMEZONE('UTC', c.TIMEZONE, l.FILE_LAST_MODIFIED)::DATE AS BUSINESS_DATE",
        # complete means every pattern has a file for the date
        "WHERE e.PATTERNS > 0 AND d.PATTERNS = e.PATTERNS",
        # a run starts on the first completion and again on any later arrival for the date
        "WHERE LAST_RUN_SAW IS NULL OR LATEST_ARRIVAL_AT > LAST_RUN_SAW",
        "WHEN LATE_FILES IS NOT NULL THEN 'late_arrival'",
        "'redelivery'",
        # the child tasks read the answer
        "CALL SYSTEM$SET_RETURN_VALUE(IFF(:started > 0, 'run', 'wait'));",
      ] : strcontains(snowflake_procedure_sql.custodian_gate.procedure_definition, needle)
    ])
    error_message = "The gate must start a run for a business date whose expected file set is complete and whose newest arrival the last run did not see, and tell the DAG so."
  }

  assert {
    condition = alltrue([
      for needle in [
        "'custodian_late_arrival'",
        "'info'",
        "'late_arrival:' || r.CUSTODIAN_ID || ':' || r.BUSINESS_DATE::STRING || ':' || r.RUN_ID",
        "AND r.LATE_FILES IS NOT NULL",
      ] : strcontains(snowflake_procedure_sql.custodian_gate.procedure_definition, needle)
    ])
    error_message = "A file set completed after the cutoff raises one info alert naming the late files and the run."
  }
}
