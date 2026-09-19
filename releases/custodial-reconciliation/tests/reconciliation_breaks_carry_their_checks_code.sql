-- Every break carries the taxonomy code of the check that found it. Returns breaks that carry another.
SELECT b."RUN_ID", b."CHECK_NAME", b."REJECTION_CODE"
FROM {{ DATABASE }}."CONTROL"."RECONCILIATION_BREAKS" b
LEFT JOIN (SELECT * FROM VALUES ('position_quantity', 'POSITION_QUANTITY_MISMATCH'), ('cash_balance', 'CASH_BALANCE_MISMATCH') AS c ("CHECK_NAME", "REJECTION_CODE")) c ON c."CHECK_NAME" = b."CHECK_NAME" AND c."REJECTION_CODE" = b."REJECTION_CODE"
WHERE c."CHECK_NAME" IS NULL;
