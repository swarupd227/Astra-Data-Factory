-- Firm: the key (FIRM_ID) identifies one row. Returns keys with more than one row.
SELECT "FIRM_ID", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."SILVER"."FIRM"
GROUP BY "FIRM_ID"
HAVING COUNT(*) > 1;
