-- A break names a known check and category, and is unverifiable exactly when transactions made it impossible to evaluate.
-- Returns breaks that do not.
SELECT "RUN_ID", "CHECK_NAME", "CATEGORY", "ACCOUNT_NUMBER"
FROM {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS"
WHERE "CHECK_NAME" NOT IN ('position_quantity', 'cash_balance')
   OR "CATEGORY" NOT IN ('mismatch', 'appeared', 'disappeared', 'unverifiable')
   OR ("CATEGORY" = 'unverifiable') <> ("UNVERIFIABLE_COUNT" > 0)
   OR ("CHECK_NAME" = 'position_quantity' AND "SECURITY_ID" IS NULL)
   OR ("CHECK_NAME" = 'cash_balance' AND ("CURRENCY" IS NULL OR "BALANCE_TYPE" IS NULL));
