-- Exception: the key (EXCEPTION_ID) identifies one row. Returns keys with more than one row.
SELECT "EXCEPTION_ID", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."SILVER"."EXCEPTION"
GROUP BY "EXCEPTION_ID"
HAVING COUNT(*) > 1;
