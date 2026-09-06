-- Security: the key (SECURITY_ID) identifies one row. Returns keys with more than one row.
SELECT "SECURITY_ID", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."SILVER"."SECURITY"
GROUP BY "SECURITY_ID"
HAVING COUNT(*) > 1;
