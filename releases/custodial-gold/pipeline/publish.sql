-- Publish Gold for one custodian: after a run of its Tasks DAG, every business date whose canonical rows changed since
-- the previous publish is rewritten read model by read model, and the watermark row for the date is written last, in the
-- same transaction. A consumer that joins on GOLD.WATERMARK sees a day only once every table of it is complete; a rerun of
-- the day (late file, re-delivery) replaces the day and its watermark atomically. Read models without a business date are
-- the custodian's snapshot, rewritten whole before the dated ones. Rendered by astra-data gold render.
CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."PUBLISH_GOLD"("CUSTODIAN_ID" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Publishes the Gold read models (positions, transactions, cash_balances, accounts) for a custodian and writes the watermark last; called by the custodian''s <CUSTODIAN>_PUBLISH task.'
AS
$$
DECLARE
  publish_id STRING DEFAULT UUID_STRING();
  started_at TIMESTAMP_NTZ(6) DEFAULT SYSDATE();
  since TIMESTAMP_NTZ(6);
  run_id STRING;
  published INTEGER DEFAULT 0;
  rows_written INTEGER DEFAULT 0;
  first_date DATE;
  last_date DATE;
  n_positions INTEGER DEFAULT 0;
  n_transactions INTEGER DEFAULT 0;
  n_cash_balances INTEGER DEFAULT 0;
  n_accounts INTEGER DEFAULT 0;
BEGIN
  -- 1. Only after a run of the custodian's DAG that no publish has followed; rows changed since the previous publish
  --    started are what gets republished.
  since := (SELECT COALESCE(MAX("STARTED_AT"), '1900-01-01'::TIMESTAMP_NTZ(6)) FROM {{ DATABASE }}."CONTROL"."GOLD_PUBLISH_LOG" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID);
  run_id := (SELECT MAX_BY("RUN_ID", "STARTED_AT") FROM {{ DATABASE }}."CONTROL"."CUSTODIAN_RUNS" WHERE "CUSTODIAN_ID" = :CUSTODIAN_ID AND "STARTED_AT" > :since);
  IF (run_id IS NULL) THEN
    RETURN 'nothing to publish for ' || CUSTODIAN_ID;
  END IF;

  -- 2. Snapshots without a business date: the custodian's whole set, before the dated models.
  BEGIN TRANSACTION;
  -- accounts
  DELETE FROM {{ DATABASE }}."GOLD"."ACCOUNTS" WHERE "CUSTODIAN" = :CUSTODIAN_ID;
  INSERT INTO {{ DATABASE }}."GOLD"."ACCOUNTS" ("CUSTODIAN", "ACCOUNT_NUMBER", "ACCOUNT_ID", "FIRM_ID", "ACCOUNT_NAME", "ACCOUNT_TYPE", "REGISTRATION_TYPE", "TAX_STATUS", "BASE_CURRENCY", "OPENED_ON", "CLOSED_ON", "STATUS", "PUBLISH_ID", "PUBLISHED_AT")
  SELECT s."CUSTODIAN_ID" AS "CUSTODIAN", s."ACCOUNT_NUMBER", s."ACCOUNT_ID", s."FIRM_ID", s."ACCOUNT_NAME", s."ACCOUNT_TYPE", s."REGISTRATION_TYPE", s."TAX_STATUS", s."BASE_CURRENCY", s."OPENED_ON", s."CLOSED_ON", s."STATUS", :publish_id, SYSDATE()
  FROM {{ DATABASE }}."SILVER"."ACCOUNT" s
  WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID;
  n_accounts := SQLROWCOUNT;
  COMMIT;

  -- 3. Each business date whose canonical rows changed, oldest first: every dated read model, then the watermark, committed together.
  LET dates RESULTSET := (
    SELECT DISTINCT "D" FROM (
      SELECT s."AS_OF_DATE" AS "D" FROM {{ DATABASE }}."SILVER"."POSITION" s WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND COALESCE(s."UPDATED_AT", s."LOADED_AT") > :since
      UNION
      SELECT s."TRADE_DATE" AS "D" FROM {{ DATABASE }}."SILVER"."TRANSACTION" s WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND COALESCE(s."UPDATED_AT", s."LOADED_AT") > :since
      UNION
      SELECT s."AS_OF_DATE" AS "D" FROM {{ DATABASE }}."SILVER"."CASH_BALANCE" s WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND COALESCE(s."UPDATED_AT", s."LOADED_AT") > :since
    ) WHERE "D" IS NOT NULL ORDER BY "D"
  );
  FOR d IN dates DO
    LET business_date DATE := d."D";
    BEGIN TRANSACTION;
    -- positions
    DELETE FROM {{ DATABASE }}."GOLD"."POSITIONS" WHERE "CUSTODIAN" = :CUSTODIAN_ID AND "AS_OF_DATE" = :business_date;
    INSERT INTO {{ DATABASE }}."GOLD"."POSITIONS" ("CUSTODIAN", "ACCOUNT_NUMBER", "AS_OF_DATE", "SECURITY_ID", "CUSTODIAN_SECURITY_ID", "POSITION_TYPE", "QUANTITY", "PRICE", "MARKET_VALUE", "COST_BASIS", "ACCRUED_INTEREST", "CURRENCY", "PUBLISH_ID", "PUBLISHED_AT")
    SELECT s."CUSTODIAN_ID" AS "CUSTODIAN", s."ACCOUNT_NUMBER", s."AS_OF_DATE", s."SECURITY_ID", s."CUSTODIAN_SECURITY_ID", s."POSITION_TYPE", s."QUANTITY", s."PRICE", (COALESCE(MARKET_VALUE, QUANTITY * PRICE)) AS "MARKET_VALUE", s."COST_BASIS", s."ACCRUED_INTEREST", s."CURRENCY", :publish_id, SYSDATE()
    FROM {{ DATABASE }}."SILVER"."POSITION" s
    WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND s."AS_OF_DATE" = :business_date;
    n_positions := SQLROWCOUNT;
    -- transactions
    DELETE FROM {{ DATABASE }}."GOLD"."TRANSACTIONS" WHERE "CUSTODIAN" = :CUSTODIAN_ID AND "TRADE_DATE" = :business_date;
    INSERT INTO {{ DATABASE }}."GOLD"."TRANSACTIONS" ("CUSTODIAN", "TRANSACTION_ID", "SOURCE_TRANSACTION_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "CUSTODIAN_SECURITY_ID", "TRANSACTION_TYPE", "CUSTODIAN_TRANSACTION_CODE", "TRADE_DATE", "SETTLE_DATE", "QUANTITY", "PRICE", "GROSS_AMOUNT", "FEES", "NET_AMOUNT", "CURRENCY", "DESCRIPTION", "STATUS", "PUBLISH_ID", "PUBLISHED_AT")
    SELECT s."CUSTODIAN_ID" AS "CUSTODIAN", s."TRANSACTION_ID", s."SOURCE_TRANSACTION_ID", s."ACCOUNT_NUMBER", s."SECURITY_ID", s."CUSTODIAN_SECURITY_ID", s."TRANSACTION_TYPE", s."CUSTODIAN_TRANSACTION_CODE", s."TRADE_DATE", s."SETTLE_DATE", s."QUANTITY", s."PRICE", s."GROSS_AMOUNT", s."FEES", s."NET_AMOUNT", s."CURRENCY", s."DESCRIPTION", s."STATUS", :publish_id, SYSDATE()
    FROM {{ DATABASE }}."SILVER"."TRANSACTION" s
    WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND s."TRADE_DATE" = :business_date;
    n_transactions := SQLROWCOUNT;
    -- cash_balances
    DELETE FROM {{ DATABASE }}."GOLD"."CASH_BALANCES" WHERE "CUSTODIAN" = :CUSTODIAN_ID AND "AS_OF_DATE" = :business_date;
    INSERT INTO {{ DATABASE }}."GOLD"."CASH_BALANCES" ("CUSTODIAN", "ACCOUNT_NUMBER", "CURRENCY", "BALANCE_TYPE", "AS_OF_DATE", "AMOUNT", "PUBLISH_ID", "PUBLISHED_AT")
    SELECT s."CUSTODIAN_ID" AS "CUSTODIAN", s."ACCOUNT_NUMBER", s."CURRENCY", s."BALANCE_TYPE", s."AS_OF_DATE", s."AMOUNT", :publish_id, SYSDATE()
    FROM {{ DATABASE }}."SILVER"."CASH_BALANCE" s
    WHERE s."CUSTODIAN_ID" = :CUSTODIAN_ID AND s."AS_OF_DATE" = :business_date;
    n_cash_balances := SQLROWCOUNT;
    -- the watermark, last
    MERGE INTO {{ DATABASE }}."GOLD"."WATERMARK" w
    USING (SELECT :CUSTODIAN_ID AS "CUSTODIAN_ID", :business_date AS "BUSINESS_DATE") s
      ON w."CUSTODIAN_ID" = s."CUSTODIAN_ID" AND w."BUSINESS_DATE" = s."BUSINESS_DATE"
    WHEN MATCHED THEN UPDATE SET "PUBLISHED_AT" = SYSDATE(), "PUBLISH_ID" = :publish_id, "RUN_ID" = :run_id, "ROWS" = :n_positions + :n_transactions + :n_cash_balances, "DETAIL" = TO_JSON(OBJECT_CONSTRUCT('positions', :n_positions, 'transactions', :n_transactions, 'cash_balances', :n_cash_balances))
    WHEN NOT MATCHED THEN INSERT ("CUSTODIAN_ID", "BUSINESS_DATE", "PUBLISHED_AT", "PUBLISH_ID", "RUN_ID", "ROWS", "DETAIL")
      VALUES (s."CUSTODIAN_ID", s."BUSINESS_DATE", SYSDATE(), :publish_id, :run_id, :n_positions + :n_transactions + :n_cash_balances, TO_JSON(OBJECT_CONSTRUCT('positions', :n_positions, 'transactions', :n_transactions, 'cash_balances', :n_cash_balances)));
    COMMIT;
    published := published + 1;
    rows_written := rows_written + n_positions + n_transactions + n_cash_balances;
    first_date := COALESCE(first_date, business_date);
    last_date := business_date;
  END FOR;

  -- 4. The publish itself.
  INSERT INTO {{ DATABASE }}."CONTROL"."GOLD_PUBLISH_LOG" ("PUBLISH_ID", "CUSTODIAN_ID", "RUN_ID", "STARTED_AT", "PUBLISHED_AT", "BUSINESS_DATES", "FIRST_DATE", "LAST_DATE", "ROWS")
  SELECT :publish_id, :CUSTODIAN_ID, :run_id, :started_at, SYSDATE(), :published, :first_date, :last_date, :rows_written + (:n_accounts);
  RETURN published || ' business date(s) published for ' || CUSTODIAN_ID || ' after run ' || run_id || ' (publish ' || publish_id || ')';
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
