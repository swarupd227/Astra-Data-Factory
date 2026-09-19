-- Every move in the history is an edge the exception workflow allows. Returns moves that are not.
SELECT h."SOURCE_ID", h."EXCEPTION_ID", h."FROM_STATUS", h."TO_STATUS"
FROM {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" h
LEFT JOIN (SELECT * FROM VALUES ('NEW', 'RESOLVED'), ('NEW', 'AUTO_RESOLVED'), ('NEW', 'DISMISSED') AS t ("FROM_STATUS", "TO_STATUS")) l ON l."FROM_STATUS" = h."FROM_STATUS" AND l."TO_STATUS" = h."TO_STATUS"
WHERE h."KIND" = 'transition' AND l."FROM_STATUS" IS NULL;
