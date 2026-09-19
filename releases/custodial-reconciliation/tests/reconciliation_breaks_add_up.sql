-- Expected is the prior value plus the movement, and the difference is actual minus expected. Returns breaks whose arithmetic is wrong.
SELECT "RUN_ID", "CHECK_NAME", "ACCOUNT_NUMBER", "EXPECTED", "DIFFERENCE"
FROM {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS"
WHERE "EXPECTED" <> COALESCE("PRIOR_VALUE", 0) + "MOVEMENT" OR "DIFFERENCE" <> COALESCE("ACTUAL", 0) - "EXPECTED";
