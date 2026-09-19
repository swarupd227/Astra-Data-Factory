-- Reconciliation of the custodial domain pack's canonical tables: what CONTROL.RECONCILE found for each custodian and
-- business date, replaced whole by each run, and a log of every run with its row counts. Rendered by astra-data reconciliation render.

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS" (
  "RUN_ID"             STRING NOT NULL COMMENT 'The run that found it; see CONTROL.RECONCILIATION_RUNS',
  "CHECK_NAME"         STRING NOT NULL COMMENT 'The identity that failed: position_quantity, cash_balance',
  "CATEGORY"           STRING NOT NULL COMMENT 'What kind of break: mismatch, appeared, disappeared, unverifiable',
  "CUSTODIAN_ID"       STRING NOT NULL,
  "ACCOUNT_NUMBER"     STRING NOT NULL COMMENT 'Account the position or balance is held in',
  "SECURITY_ID"        STRING COMMENT 'The security, for a position break',
  "CURRENCY"           STRING COMMENT 'The currency, for a cash break',
  "BALANCE_TYPE"       STRING COMMENT 'The balance type, for a cash break',
  "AS_OF_DATE"         DATE NOT NULL COMMENT 'The business date that was checked',
  "PRIOR_DATE"         DATE NOT NULL COMMENT 'The earlier snapshot it was checked against',
  "PRIOR_VALUE"        NUMBER(38,8) COMMENT 'Quantity or amount in that snapshot; null if it was not held',
  "MOVEMENT"           NUMBER(38,8) NOT NULL COMMENT 'What the transactions between the two dates moved it by',
  "EXPECTED"           NUMBER(38,8) NOT NULL COMMENT 'Prior value plus movement',
  "ACTUAL"             NUMBER(38,8) COMMENT 'What the business date reports; null if it is not there',
  "DIFFERENCE"         NUMBER(38,8) NOT NULL COMMENT 'Actual (zero if absent) minus expected',
  "UNVERIFIABLE_COUNT" NUMBER(18,0) NOT NULL COMMENT 'Transactions that made the identity impossible to evaluate; above zero exactly when the category is unverifiable',
  "REJECTION_CODE"     STRING NOT NULL COMMENT 'The taxonomy code of the check; see CONTROL.REJECTION_CODES',
  "DETECTED_AT"        TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the run found it'
)
BASE_LOCATION = 'control/reconciliation_breaks/'
COMMENT = 'The current reconciliation findings of each custodian and business date. Replaced whole by each CONTROL.RECONCILE run. Rendered by astra-data reconciliation render.';
ALTER ICEBERG TABLE {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'account_number';

-- Every run: for which custodian and business date, how many positions and balances were in scope, how many breaks it found.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."RECONCILIATION_RUNS" (
  "RUN_ID"                STRING NOT NULL,
  "CUSTODIAN_ID"          STRING NOT NULL,
  "AS_OF_DATE"            DATE NOT NULL,
  "STARTED_AT"            TIMESTAMP_NTZ(6) NOT NULL,
  "FINISHED_AT"           TIMESTAMP_NTZ(6) NOT NULL,
  "POSITIONS_IN_SCOPE"   NUMBER(18,0) NOT NULL COMMENT 'Position rows of the business date',
  "BALANCES_IN_SCOPE"    NUMBER(18,0) NOT NULL COMMENT 'Cash balance rows of the business date',
  "BREAKS_FOUND"         NUMBER(18,0) NOT NULL COMMENT 'Rows written to CONTROL.RECONCILIATION_BREAKS, unverifiable ones included'
)
BASE_LOCATION = 'control/reconciliation_runs/'
COMMENT = 'Every CONTROL.RECONCILE run with its row counts. Rendered by astra-data reconciliation render.';
