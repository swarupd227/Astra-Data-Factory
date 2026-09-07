-- One watermark row per custodian and business date. Returns pairs with more than one.
SELECT "CUSTODIAN_ID", "BUSINESS_DATE", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."GOLD"."WATERMARK"
GROUP BY "CUSTODIAN_ID", "BUSINESS_DATE"
HAVING COUNT(*) > 1;
