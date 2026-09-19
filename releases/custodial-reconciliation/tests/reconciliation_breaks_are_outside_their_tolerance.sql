-- A break that can be evaluated differs by more than the tolerance of its field type. Returns breaks whose values agree.
SELECT "RUN_ID", "CHECK_NAME", "ACCOUNT_NUMBER", "EXPECTED", "ACTUAL"
FROM {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS"
WHERE "CATEGORY" <> 'unverifiable' AND CASE WHEN "CHECK_NAME" = 'position_quantity' THEN ("EXPECTED" = COALESCE("ACTUAL", 0)) WHEN "CHECK_NAME" = 'cash_balance' THEN ("EXPECTED" = COALESCE("ACTUAL", 0)) ELSE FALSE END;
