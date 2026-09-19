-- Exception workflow of the custodial domain pack: every move of an exception between states and every
-- assignment of it to a person, append only. Written by CONTROL.TRANSITION_EXCEPTION and CONTROL.ASSIGN_EXCEPTION;
-- the status itself stays on the exception's own row in EXCEPTIONS.<SOURCE>. Rendered by astra-data exceptions render.

CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" (
  "EVENT_ID"       STRING NOT NULL COMMENT 'Identifier of this event',
  "SOURCE_ID"      STRING NOT NULL COMMENT 'The source config whose EXCEPTIONS table holds the exception',
  "EXCEPTION_ID"   STRING NOT NULL COMMENT 'The exception, in that source',
  "REJECTION_CODE" STRING NOT NULL COMMENT 'Its code in the rejection taxonomy',
  "OWNER"          STRING COMMENT 'The taxonomy owner of the code when the event happened: custodian, steward, data_engineer or platform',
  "KIND"           STRING NOT NULL COMMENT 'One of transition, assign: a move between states, or an assignment to a person',
  "FROM_STATUS"    STRING COMMENT 'Status before a move; null for an assignment',
  "TO_STATUS"      STRING COMMENT 'Status after a move; null for an assignment',
  "ASSIGNEE"       STRING COMMENT 'The person an assignment gave the exception to; null for a move',
  "ACTOR"          STRING NOT NULL COMMENT 'Who made the move or the assignment',
  "EVENT_AT"       TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When it happened',
  "NOTE"           STRING COMMENT 'For a move: what was done, or why the exception was dismissed'
)
BASE_LOCATION = 'control/exception_history/'
COMMENT = 'Every move and assignment of an exception, append only. Rendered by astra-data exceptions render.';

-- Who each exception is assigned to now: its latest assignment. A closed exception keeps its last assignee;
-- join the exception's own STATUS to leave those out.
CREATE OR REPLACE VIEW {{ DATABASE }}."CONTROL"."EXCEPTION_ASSIGNMENTS"
COMMENT = 'The latest assignment of each exception. Rendered by astra-data exceptions render.'
AS
SELECT "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "ASSIGNEE", "ACTOR" AS "ASSIGNED_BY", "EVENT_AT" AS "ASSIGNED_AT"
FROM {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY"
WHERE "KIND" = 'assign'
QUALIFY ROW_NUMBER() OVER (PARTITION BY "SOURCE_ID", "EXCEPTION_ID" ORDER BY "EVENT_AT" DESC) = 1;
