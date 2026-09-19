-- Nothing is assigned after the exception left NEW. Returns assignments made later than the exception's move.
SELECT a."SOURCE_ID", a."EXCEPTION_ID", a."EVENT_AT" AS "ASSIGNED_AT", t."EVENT_AT" AS "MOVED_AT"
FROM {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" a
JOIN {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" t ON t."SOURCE_ID" = a."SOURCE_ID" AND t."EXCEPTION_ID" = a."EXCEPTION_ID" AND t."KIND" = 'transition'
WHERE a."KIND" = 'assign' AND a."EVENT_AT" > t."EVENT_AT";
