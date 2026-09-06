-- Reference-data replicas of the custodial pack: one replica per feed with its staging, change and conflict tables.
-- Rendered by astra-data reference render. Do not edit; change the feeds file and re-render.

-- Security master (SOS): key (SECURITY_ID); replicated from @{{ DATABASE }}."REFERENCE"."LANDING"/security_master/
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."SECURITY_MASTER" (
  "SECURITY_ID"       STRING NOT NULL COMMENT 'Security master identifier.',
  "CUSIP"             STRING COMMENT 'CUSIP, nine characters.',
  "ISIN"              STRING COMMENT 'ISIN, twelve characters.',
  "SEDOL"             STRING COMMENT 'SEDOL, seven characters.',
  "TICKER"            STRING COMMENT 'Exchange ticker.',
  "DESCRIPTION"       STRING NOT NULL COMMENT 'Name of the security.',
  "ASSET_CLASS"       STRING NOT NULL COMMENT 'Broad class of the security, in the security master''s vocabulary.',
  "SECURITY_TYPE"     STRING COMMENT 'Finer type within the asset class.',
  "ISSUER"            STRING COMMENT 'Issuer of the security.',
  "CURRENCY"          STRING NOT NULL COMMENT 'ISO 4217 currency the security trades in.',
  "PRICE_FACTOR"      NUMBER(18,8) COMMENT 'Multiplier from quoted price to value per unit.',
  "MATURITY_DATE"     DATE COMMENT 'Maturity date of a fixed-income security.',
  "STATUS"            STRING NOT NULL COMMENT 'ACTIVE or INACTIVE in the security master.',
  "SOURCE_UPDATED_AT" TIMESTAMP_NTZ(6) COMMENT 'When the security master last changed the row.',
  "REPLICATED_AT"     TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was last written by a replication run.',
  "RUN_ID"            STRING NOT NULL COMMENT 'Replication run that last wrote the row; see CONTROL.REFERENCE_DATA_RUNS.'
)
BASE_LOCATION = 'reference/security_master/'
COMMENT = 'The platform''s security master: one row per security with the identifiers custodian records carry (CUSIP, ISIN, SEDOL, ticker), so that a custodian''s security resolves to the platform''s SECURITY_ID by join. Replica of SOS; every row references its replication run.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_STAGING" (
  "SECURITY_ID"       STRING,
  "CUSIP"             STRING,
  "ISIN"              STRING,
  "SEDOL"             STRING,
  "TICKER"            STRING,
  "DESCRIPTION"       STRING,
  "ASSET_CLASS"       STRING,
  "SECURITY_TYPE"     STRING,
  "ISSUER"            STRING,
  "CURRENCY"          STRING,
  "PRICE_FACTOR"      NUMBER(18,8),
  "MATURITY_DATE"     DATE,
  "STATUS"            STRING,
  "SOURCE_UPDATED_AT" TIMESTAMP_NTZ(6),
  "SOURCE_FILE"       STRING NOT NULL,
  "SOURCE_ROW"        NUMBER(18,0) NOT NULL
)
BASE_LOCATION = 'reference/security_master_staging/'
COMMENT = 'The newest Security master snapshot as loaded, before the delta is computed. Truncated by every run.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_CHANGES" (
  "RUN_ID"            STRING NOT NULL,
  "CHANGED_AT"        TIMESTAMP_NTZ(6) NOT NULL,
  "CHANGE"            STRING NOT NULL COMMENT 'inserted, updated or deleted',
  "SECURITY_ID"       STRING NOT NULL,
  "BEFORE"            STRING COMMENT 'The row before the change, as JSON; null when inserted',
  "AFTER"             STRING COMMENT 'The row after the change, as JSON; null when deleted'
)
BASE_LOCATION = 'reference/security_master_changes/'
COMMENT = 'Every change a replication run made to SECURITY_MASTER: the delta since any run is the rows with a later RUN_ID.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_CONFLICTS" (
  "RUN_ID"            STRING NOT NULL,
  "SECURITY_ID"       STRING,
  "ROW_COUNT"         NUMBER(18,0) NOT NULL,
  "REJECTION_CODE"    STRING NOT NULL
)
BASE_LOCATION = 'reference/security_master_conflicts/'
COMMENT = 'Snapshot keys a run left out because they were blank or repeated (REFERENCE_DATA_CONFLICT); the replica keeps what it had for them.';

-- Account cross-reference (CAS): key (CUSTODIAN_ID, CUSTODIAN_ACCOUNT_NUMBER); replicated from @{{ DATABASE }}."REFERENCE"."LANDING"/account_xref/
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."ACCOUNT_XREF" (
  "CUSTODIAN_ID"             STRING NOT NULL COMMENT 'Custodian that holds the account, by the platform''s custodian id.',
  "CUSTODIAN_ACCOUNT_NUMBER" STRING NOT NULL COMMENT 'Account number as the custodian assigns it. PII: account_number.',
  "ACCOUNT_ID"               STRING NOT NULL COMMENT 'Platform account id.',
  "FIRM_ID"                  STRING NOT NULL COMMENT 'Firm the account belongs to.',
  "ACCOUNT_NAME"             STRING COMMENT 'Registration name of the account. PII: name.',
  "STATUS"                   STRING NOT NULL COMMENT 'OPEN, CLOSED or PENDING in the account system.',
  "OPENED_ON"                DATE COMMENT 'Date the account was opened.',
  "CLOSED_ON"                DATE COMMENT 'Date the account was closed; blank while open.',
  "SOURCE_UPDATED_AT"        TIMESTAMP_NTZ(6) COMMENT 'When the account system last changed the row.',
  "REPLICATED_AT"            TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was last written by a replication run.',
  "RUN_ID"                   STRING NOT NULL COMMENT 'Replication run that last wrote the row; see CONTROL.REFERENCE_DATA_RUNS.'
)
BASE_LOCATION = 'reference/account_xref/'
COMMENT = 'The account cross-reference: one row per custodian account the platform manages, with the platform account id and firm it belongs to, so that a custodian''s account number resolves to the platform''s ACCOUNT_ID by join. Replica of CAS; every row references its replication run.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."ACCOUNT_XREF_STAGING" (
  "CUSTODIAN_ID"             STRING,
  "CUSTODIAN_ACCOUNT_NUMBER" STRING,
  "ACCOUNT_ID"               STRING,
  "FIRM_ID"                  STRING,
  "ACCOUNT_NAME"             STRING,
  "STATUS"                   STRING,
  "OPENED_ON"                DATE,
  "CLOSED_ON"                DATE,
  "SOURCE_UPDATED_AT"        TIMESTAMP_NTZ(6),
  "SOURCE_FILE"              STRING NOT NULL,
  "SOURCE_ROW"               NUMBER(18,0) NOT NULL
)
BASE_LOCATION = 'reference/account_xref_staging/'
COMMENT = 'The newest Account cross-reference snapshot as loaded, before the delta is computed. Truncated by every run.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."ACCOUNT_XREF_CHANGES" (
  "RUN_ID"                   STRING NOT NULL,
  "CHANGED_AT"               TIMESTAMP_NTZ(6) NOT NULL,
  "CHANGE"                   STRING NOT NULL COMMENT 'inserted, updated or deleted',
  "CUSTODIAN_ID"             STRING NOT NULL,
  "CUSTODIAN_ACCOUNT_NUMBER" STRING NOT NULL,
  "BEFORE"                   STRING COMMENT 'The row before the change, as JSON; null when inserted',
  "AFTER"                    STRING COMMENT 'The row after the change, as JSON; null when deleted'
)
BASE_LOCATION = 'reference/account_xref_changes/'
COMMENT = 'Every change a replication run made to ACCOUNT_XREF: the delta since any run is the rows with a later RUN_ID.';

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."REFERENCE"."ACCOUNT_XREF_CONFLICTS" (
  "RUN_ID"                   STRING NOT NULL,
  "CUSTODIAN_ID"             STRING,
  "CUSTODIAN_ACCOUNT_NUMBER" STRING,
  "ROW_COUNT"                NUMBER(18,0) NOT NULL,
  "REJECTION_CODE"           STRING NOT NULL
)
BASE_LOCATION = 'reference/account_xref_conflicts/'
COMMENT = 'Snapshot keys a run left out because they were blank or repeated (REFERENCE_DATA_CONFLICT); the replica keeps what it had for them.';
