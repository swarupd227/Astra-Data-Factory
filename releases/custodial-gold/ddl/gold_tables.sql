-- Gold read models of the custodial domain pack: the tables a consumer reads, in the consumer's shape, published per
-- custodian and business date by CONTROL.PUBLISH_GOLD with the watermark written last. Rendered by astra-data gold render.

-- positions: One row per custodian, account, security and business date: the holding as the custodian reported it, valued.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."GOLD"."POSITIONS" (
  "CUSTODIAN"              STRING NOT NULL COMMENT 'Custodian that reported the position. From Position.CUSTODIAN_ID.',
  "ACCOUNT_NUMBER"         STRING NOT NULL COMMENT 'Account the position is held in. From Position.ACCOUNT_NUMBER.',
  "AS_OF_DATE"             DATE NOT NULL COMMENT 'Business date the snapshot describes. From Position.AS_OF_DATE.',
  "SECURITY_ID"            STRING NOT NULL COMMENT 'Security held, resolved through the security master. From Position.SECURITY_ID.',
  "CUSTODIAN_SECURITY_ID"  STRING COMMENT 'Identifier the custodian sent for the security, before resolution. From Position.CUSTODIAN_SECURITY_ID.',
  "POSITION_TYPE"          STRING NOT NULL COMMENT 'Long or short. From Position.POSITION_TYPE.',
  "QUANTITY"               NUMBER(28,8) NOT NULL COMMENT 'Units held; negative for short positions. From Position.QUANTITY.',
  "PRICE"                  NUMBER(28,10) COMMENT 'Price the custodian valued the position at. From Position.PRICE.',
  "MARKET_VALUE"           NUMBER(28,4) COMMENT 'Market value as the custodian reported it, or quantity times price when it reported none. From COALESCE(MARKET_VALUE, QUANTITY * PRICE).',
  "COST_BASIS"             NUMBER(28,4) COMMENT 'Total cost basis the custodian reported. From Position.COST_BASIS.',
  "ACCRUED_INTEREST"       NUMBER(28,4) COMMENT 'Accrued interest included in the valuation, for fixed income. From Position.ACCRUED_INTEREST.',
  "CURRENCY"               STRING NOT NULL COMMENT 'ISO 4217 currency of the price and values. From Position.CURRENCY.',
  "PUBLISH_ID"             STRING NOT NULL COMMENT 'Publish that wrote the row; see CONTROL.GOLD_PUBLISH_LOG',
  "PUBLISHED_AT"           TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written; never later than the watermark of its custodian and business date'
)
BASE_LOCATION = 'gold/positions/'
COMMENT = 'One row per custodian, account, security and business date: the holding as the custodian reported it, valued. Read model positions of Position, published per CUSTODIAN and AS_OF_DATE; read POSITIONS_PUBLISHED for complete days only. Rendered by astra-data gold render.';
ALTER ICEBERG TABLE {{ DATABASE }}."GOLD"."POSITIONS" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'account_number';

-- transactions: One row per custodian and canonical transaction, keyed to the trade date the consumer reports by.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."GOLD"."TRANSACTIONS" (
  "CUSTODIAN"                   STRING NOT NULL COMMENT 'Custodian that reported the transaction. From Transaction.CUSTODIAN_ID.',
  "TRANSACTION_ID"              STRING NOT NULL COMMENT 'Canonical transaction id; the custodian''s id, suffixed with the part name when a record was split. From Transaction.TRANSACTION_ID.',
  "SOURCE_TRANSACTION_ID"       STRING NOT NULL COMMENT 'Transaction id as the custodian sent it. From Transaction.SOURCE_TRANSACTION_ID.',
  "ACCOUNT_NUMBER"              STRING NOT NULL COMMENT 'Account the transaction belongs to. From Transaction.ACCOUNT_NUMBER.',
  "SECURITY_ID"                 STRING COMMENT 'Security involved; null for cash-only transactions. From Transaction.SECURITY_ID.',
  "CUSTODIAN_SECURITY_ID"       STRING COMMENT 'Identifier the custodian sent for the security, before resolution. From Transaction.CUSTODIAN_SECURITY_ID.',
  "TRANSACTION_TYPE"            STRING NOT NULL COMMENT 'Canonical type, resolved from the custodian''s transaction code. From Transaction.TRANSACTION_TYPE.',
  "CUSTODIAN_TRANSACTION_CODE"  STRING NOT NULL COMMENT 'Transaction code as the custodian sent it. From Transaction.CUSTODIAN_TRANSACTION_CODE.',
  "TRADE_DATE"                  DATE NOT NULL COMMENT 'Date the transaction took place. From Transaction.TRADE_DATE.',
  "SETTLE_DATE"                 DATE COMMENT 'Date the transaction settled or is due to settle. From Transaction.SETTLE_DATE.',
  "QUANTITY"                    NUMBER(28,8) COMMENT 'Units transacted; null for cash-only transactions. From Transaction.QUANTITY.',
  "PRICE"                       NUMBER(28,10) COMMENT 'Price per unit. From Transaction.PRICE.',
  "GROSS_AMOUNT"                NUMBER(28,4) COMMENT 'Amount before fees; positive for cash in, negative for cash out. From Transaction.GROSS_AMOUNT.',
  "FEES"                        NUMBER(28,4) COMMENT 'Commissions and fees charged. From Transaction.FEES.',
  "NET_AMOUNT"                  NUMBER(28,4) NOT NULL COMMENT 'Amount that moved cash; positive for cash in, negative for cash out. From Transaction.NET_AMOUNT.',
  "CURRENCY"                    STRING NOT NULL COMMENT 'ISO 4217 currency of the amounts. From Transaction.CURRENCY.',
  "DESCRIPTION"                 STRING COMMENT 'Narrative the custodian sent. From Transaction.DESCRIPTION.',
  "STATUS"                      STRING NOT NULL COMMENT 'Lifecycle status of the transaction. From Transaction.STATUS.',
  "PUBLISH_ID"                  STRING NOT NULL COMMENT 'Publish that wrote the row; see CONTROL.GOLD_PUBLISH_LOG',
  "PUBLISHED_AT"                TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written; never later than the watermark of its custodian and business date'
)
BASE_LOCATION = 'gold/transactions/'
COMMENT = 'One row per custodian and canonical transaction, keyed to the trade date the consumer reports by. Read model transactions of Transaction, published per CUSTODIAN and TRADE_DATE; read TRANSACTIONS_PUBLISHED for complete days only. Rendered by astra-data gold render.';
ALTER ICEBERG TABLE {{ DATABASE }}."GOLD"."TRANSACTIONS" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'account_number';

-- cash_balances: One row per custodian, account, currency, balance type and business date.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."GOLD"."CASH_BALANCES" (
  "CUSTODIAN"       STRING NOT NULL COMMENT 'Custodian that reported the balance. From Cash Balance.CUSTODIAN_ID.',
  "ACCOUNT_NUMBER"  STRING NOT NULL COMMENT 'Account the cash is held in. From Cash Balance.ACCOUNT_NUMBER.',
  "CURRENCY"        STRING NOT NULL COMMENT 'ISO 4217 currency of the balance. From Cash Balance.CURRENCY.',
  "BALANCE_TYPE"    STRING NOT NULL COMMENT 'Which balance this is. From Cash Balance.BALANCE_TYPE.',
  "AS_OF_DATE"      DATE NOT NULL COMMENT 'Business date the balance describes. From Cash Balance.AS_OF_DATE.',
  "AMOUNT"          NUMBER(28,4) NOT NULL COMMENT 'Balance; negative when the account owes cash. From Cash Balance.AMOUNT.',
  "PUBLISH_ID"      STRING NOT NULL COMMENT 'Publish that wrote the row; see CONTROL.GOLD_PUBLISH_LOG',
  "PUBLISHED_AT"    TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written; never later than the watermark of its custodian and business date'
)
BASE_LOCATION = 'gold/cash_balances/'
COMMENT = 'One row per custodian, account, currency, balance type and business date. Read model cash_balances of Cash Balance, published per CUSTODIAN and AS_OF_DATE; read CASH_BALANCES_PUBLISHED for complete days only. Rendered by astra-data gold render.';
ALTER ICEBERG TABLE {{ DATABASE }}."GOLD"."CASH_BALANCES" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'account_number';

-- accounts: The custodian's accounts as currently known: a snapshot published whole with every publish, not keyed to a business date.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."GOLD"."ACCOUNTS" (
  "CUSTODIAN"          STRING NOT NULL COMMENT 'Custodian that holds the account. From Account.CUSTODIAN_ID.',
  "ACCOUNT_NUMBER"     STRING NOT NULL COMMENT 'Account number as the custodian assigns it. From Account.ACCOUNT_NUMBER.',
  "ACCOUNT_ID"         STRING COMMENT 'Platform account id from the account cross-reference; null until resolved. From Account.ACCOUNT_ID.',
  "FIRM_ID"            STRING NOT NULL COMMENT 'Firm the account belongs to. From Account.FIRM_ID.',
  "ACCOUNT_NAME"       STRING COMMENT 'Registration name of the account. From Account.ACCOUNT_NAME.',
  "ACCOUNT_TYPE"       STRING COMMENT 'Custodian''s account type, as received. From Account.ACCOUNT_TYPE.',
  "REGISTRATION_TYPE"  STRING COMMENT 'Canonical registration of the account. From Account.REGISTRATION_TYPE.',
  "TAX_STATUS"         STRING COMMENT 'Whether the account is taxable. From Account.TAX_STATUS.',
  "BASE_CURRENCY"      STRING NOT NULL COMMENT 'ISO 4217 currency the account is valued in. From Account.BASE_CURRENCY.',
  "OPENED_ON"          DATE COMMENT 'Date the custodian opened the account. From Account.OPENED_ON.',
  "CLOSED_ON"          DATE COMMENT 'Date the custodian closed the account; null while open. From Account.CLOSED_ON.',
  "STATUS"             STRING NOT NULL COMMENT 'Whether the account is open. From Account.STATUS.',
  "PUBLISH_ID"         STRING NOT NULL COMMENT 'Publish that wrote the row; see CONTROL.GOLD_PUBLISH_LOG',
  "PUBLISHED_AT"       TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written; never later than the watermark of its custodian and business date'
)
BASE_LOCATION = 'gold/accounts/'
COMMENT = 'The custodian''s accounts as currently known: a snapshot published whole with every publish, not keyed to a business date. Read model accounts of Account, published per CUSTODIAN, as a whole; read ACCOUNTS_PUBLISHED for complete days only. Rendered by astra-data gold render.';
ALTER ICEBERG TABLE {{ DATABASE }}."GOLD"."ACCOUNTS" MODIFY COLUMN "ACCOUNT_NUMBER" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'account_number';
ALTER ICEBERG TABLE {{ DATABASE }}."GOLD"."ACCOUNTS" MODIFY COLUMN "ACCOUNT_NAME" SET TAG {{ DATABASE }}."CONTROL"."PII" = 'name';

-- The watermark: one row per custodian and business date, written after every Gold table of the day. A consumer that
-- joins on it, or reads the <TABLE>_PUBLISHED views, never sees a half day.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."GOLD"."WATERMARK" (
  "CUSTODIAN_ID"  STRING NOT NULL,
  "BUSINESS_DATE" DATE NOT NULL,
  "PUBLISHED_AT"  TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the day became complete in Gold; later than every row of the day',
  "PUBLISH_ID"    STRING NOT NULL COMMENT 'The publish that completed the day; see CONTROL.GOLD_PUBLISH_LOG',
  "RUN_ID"        STRING COMMENT 'The custodian DAG run the publish followed; see CONTROL.CUSTODIAN_RUNS',
  "ROWS"          NUMBER(18,0) NOT NULL COMMENT 'Gold rows of the day across the dated read models',
  "DETAIL"        STRING COMMENT 'Rows per read model, as JSON'
)
BASE_LOCATION = 'gold/watermark/'
COMMENT = 'One row per custodian and business date whose Gold tables are complete; written last by CONTROL.PUBLISH_GOLD. Rendered by astra-data gold render.';

-- Every publish: which custodian, which DAG run, how many business dates and rows.
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."GOLD_PUBLISH_LOG" (
  "PUBLISH_ID"     STRING NOT NULL,
  "CUSTODIAN_ID"   STRING NOT NULL,
  "RUN_ID"         STRING COMMENT 'The custodian DAG run the publish followed',
  "STARTED_AT"     TIMESTAMP_NTZ(6) NOT NULL COMMENT 'Rows changed after the previous publish started are republished',
  "PUBLISHED_AT"   TIMESTAMP_NTZ(6) NOT NULL,
  "BUSINESS_DATES" NUMBER(18,0) NOT NULL COMMENT 'Business dates rewritten, each with its watermark',
  "FIRST_DATE"     DATE,
  "LAST_DATE"      DATE,
  "ROWS"           NUMBER(18,0) NOT NULL COMMENT 'Gold rows written, dated and undated read models together'
)
BASE_LOCATION = 'control/gold_publish_log/'
COMMENT = 'Every Gold publish per custodian. Written by CONTROL.PUBLISH_GOLD. Rendered by astra-data gold render.';
