-- POSITIONS: no row of a day was written after the day's watermark. Returns rows newer than their watermark.
SELECT g."CUSTODIAN", g."AS_OF_DATE", g."PUBLISHED_AT", w."PUBLISHED_AT" AS WATERMARK_AT
FROM {{ DATABASE }}."GOLD"."POSITIONS" g
JOIN {{ DATABASE }}."GOLD"."WATERMARK" w ON w."CUSTODIAN_ID" = g."CUSTODIAN" AND w."BUSINESS_DATE" = g."AS_OF_DATE"
WHERE g."PUBLISHED_AT" > w."PUBLISHED_AT";
