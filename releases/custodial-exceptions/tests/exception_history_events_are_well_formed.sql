-- Every event has an actor and the shape of its kind: a move has both statuses and what was done, an assignment has its assignee.
-- Returns events that do not.
SELECT "SOURCE_ID", "EXCEPTION_ID", "KIND"
FROM {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY"
WHERE TRIM(COALESCE("ACTOR", '')) = ''
   OR "KIND" NOT IN ('transition', 'assign')
   OR ("KIND" = 'transition' AND ("FROM_STATUS" IS NULL OR "TO_STATUS" IS NULL OR TRIM(COALESCE("NOTE", '')) = ''))
   OR ("KIND" = 'assign' AND TRIM(COALESCE("ASSIGNEE", '')) = '');
