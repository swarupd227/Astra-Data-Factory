-- POSITIONS: every custodian and business date in Gold has a watermark row. Returns days without one.
SELECT g."CUSTODIAN", g."AS_OF_DATE", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."GOLD"."POSITIONS" g
LEFT JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE"
WHERE w."CUSTODIAN_ID" IS NULL
GROUP BY g."CUSTODIAN", g."AS_OF_DATE";
