-- What a consumer reads: each Gold table joined to the watermark, so only days every table of which is complete are
-- visible. A snapshot without a business date is visible once its custodian has any published day. Rendered by
-- astra-data gold render.

CREATE OR REPLACE VIEW {{ DATABASE }}."GOLD"."POSITIONS_PUBLISHED"
COMMENT = 'One row per custodian, account, security and business date: the holding as the custodian reported it, valued. Complete days only: rows of POSITIONS whose CUSTODIAN and AS_OF_DATE have a watermark. Rendered by astra-data gold render.'
AS
SELECT g.*
FROM {{ DATABASE }}."GOLD"."POSITIONS" g
JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE";

CREATE OR REPLACE VIEW {{ DATABASE }}."GOLD"."TRANSACTIONS_PUBLISHED"
COMMENT = 'One row per custodian and canonical transaction, keyed to the trade date the consumer reports by. Complete days only: rows of TRANSACTIONS whose CUSTODIAN and TRADE_DATE have a watermark. Rendered by astra-data gold render.'
AS
SELECT g.*
FROM {{ DATABASE }}."GOLD"."TRANSACTIONS" g
JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."TRADE_DATE";

CREATE OR REPLACE VIEW {{ DATABASE }}."GOLD"."CASH_BALANCES_PUBLISHED"
COMMENT = 'One row per custodian, account, currency, balance type and business date. Complete days only: rows of CASH_BALANCES whose CUSTODIAN and AS_OF_DATE have a watermark. Rendered by astra-data gold render.'
AS
SELECT g.*
FROM {{ DATABASE }}."GOLD"."CASH_BALANCES" g
JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE";

CREATE OR REPLACE VIEW {{ DATABASE }}."GOLD"."ACCOUNTS_PUBLISHED"
COMMENT = 'The custodian''s accounts as currently known: a snapshot published whole with every publish, not keyed to a business date. Complete days only: rows of ACCOUNTS whose CUSTODIAN has a published day. Rendered by astra-data gold render.'
AS
SELECT g.*
FROM {{ DATABASE }}."GOLD"."ACCOUNTS" g
WHERE EXISTS (SELECT 1 FROM {{ DATABASE }}."GOLD"."WATERMARK" w WHERE w."CUSTODIAN_ID" = g."CUSTODIAN");
