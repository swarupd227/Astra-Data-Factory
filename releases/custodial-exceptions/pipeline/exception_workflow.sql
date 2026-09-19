-- Move an exception out of NEW, refusing every invalid move with a named rule before anything is written. The rules run
-- in this order: the source name, the exception exists, the status is a state, an actor is named, the move is an allowed edge,
-- what was done (or why) is given, and AUTO_RESOLVED only for a code the taxonomy whitelists. The status change on the exception's
-- own row and its history event are written together; the update only applies while the row still has the status that was read.
-- Rendered by astra-data exceptions render.
CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."TRANSITION_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "TO_STATUS" STRING, "ACTOR" STRING, "RESOLUTION" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Moves an exception between states (NEW to RESOLVED, NEW to AUTO_RESOLVED, NEW to DISMISSED); refuses any other move. Rendered by astra-data exceptions render.'
AS
$$
DECLARE
  source_invalid EXCEPTION (-20201, 'the source id is not a valid source name');
  exception_not_found EXCEPTION (-20101, 'no such exception in that source');
  not_a_state EXCEPTION (-20102, 'the status is not one of the exception states');
  actor_required EXCEPTION (-20103, 'who is making the change must be given');
  illegal_transition EXCEPTION (-20104, 'the exception cannot move from its status to that one');
  resolution_required EXCEPTION (-20105, 'moving out of NEW needs what was done, or why it was dismissed');
  not_whitelisted EXCEPTION (-20106, 'the rejection code is not whitelisted for auto-resolve');
  changed_concurrently EXCEPTION (-20202, 'the exception changed while it was being updated; read it again');
  v_table STRING;
  v_code STRING;
  v_status STRING;
  v_owner STRING;
  v_ok INTEGER DEFAULT 0;
BEGIN
  IF (NOT REGEXP_LIKE(SOURCE_ID, '^[A-Za-z][A-Za-z0-9_]*$')) THEN
    RAISE source_invalid;
  END IF;
  v_table := '{{ DATABASE }}."EXCEPTIONS"."' || UPPER(SOURCE_ID) || '"';
  SELECT "REJECTION_CODE", "STATUS" INTO :v_code, :v_status FROM IDENTIFIER(:v_table) WHERE "EXCEPTION_ID" = :EXCEPTION_ID;
  IF (v_code IS NULL) THEN
    RAISE exception_not_found;
  END IF;
  SELECT MAX("OWNER") INTO :v_owner FROM {{ DATABASE }}."CONTROL"."REJECTION_CODES" WHERE "CODE" = :v_code;
  SELECT COUNT(*) INTO :v_ok FROM (SELECT * FROM VALUES ('NEW'), ('RESOLVED'), ('AUTO_RESOLVED'), ('DISMISSED') AS s ("STATE")) WHERE "STATE" = :TO_STATUS;
  IF (v_ok = 0) THEN
    RAISE not_a_state;
  END IF;
  IF (ACTOR IS NULL OR TRIM(ACTOR) = '') THEN
    RAISE actor_required;
  END IF;
  SELECT COUNT(*) INTO :v_ok FROM (SELECT * FROM VALUES ('NEW', 'RESOLVED'), ('NEW', 'AUTO_RESOLVED'), ('NEW', 'DISMISSED') AS t ("FROM_STATUS", "TO_STATUS")) WHERE "FROM_STATUS" = :v_status AND "TO_STATUS" = :TO_STATUS;
  IF (v_ok = 0) THEN
    RAISE illegal_transition;
  END IF;
  IF (RESOLUTION IS NULL OR TRIM(RESOLUTION) = '') THEN
    RAISE resolution_required;
  END IF;
  IF (TO_STATUS = 'AUTO_RESOLVED') THEN
    SELECT COUNT(*) INTO :v_ok FROM {{ DATABASE }}."CONTROL"."REJECTION_CODES" WHERE "CODE" = :v_code AND "AUTO_RESOLVE";
    IF (v_ok = 0) THEN
      RAISE not_whitelisted;
    END IF;
  END IF;

  BEGIN TRANSACTION;
  UPDATE IDENTIFIER(:v_table) SET "STATUS" = :TO_STATUS, "RESOLUTION" = TRIM(:RESOLUTION), "RESOLVED_BY" = TRIM(:ACTOR), "RESOLVED_AT" = SYSDATE(), "UPDATED_AT" = SYSDATE()
  WHERE "EXCEPTION_ID" = :EXCEPTION_ID AND "STATUS" = :v_status;
  IF (SQLROWCOUNT = 0) THEN
    ROLLBACK;
    RAISE changed_concurrently;
  END IF;
  INSERT INTO {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" ("EVENT_ID", "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "KIND", "FROM_STATUS", "TO_STATUS", "ASSIGNEE", "ACTOR", "EVENT_AT", "NOTE")
  SELECT UUID_STRING(), :SOURCE_ID, :EXCEPTION_ID, :v_code, :v_owner, 'transition', :v_status, :TO_STATUS, NULL, TRIM(:ACTOR), SYSDATE(), TRIM(:RESOLUTION);
  COMMIT;
  RETURN 'exception ' || EXCEPTION_ID || ' moved from ' || v_status || ' to ' || TO_STATUS || ' by ' || TRIM(ACTOR);
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;

-- Give a NEW exception to a person, or to a different one. Refused, with a named rule, if the exception does not exist, no actor or
-- assignee is named, the exception is already closed, or it is already assigned to that person. Rendered by astra-data exceptions render.
CREATE OR REPLACE PROCEDURE {{ DATABASE }}."CONTROL"."ASSIGN_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "ASSIGNEE" STRING, "ACTOR" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Assigns a NEW exception to a person; refuses a closed one. Rendered by astra-data exceptions render.'
AS
$$
DECLARE
  source_invalid EXCEPTION (-20201, 'the source id is not a valid source name');
  exception_not_found EXCEPTION (-20101, 'no such exception in that source');
  actor_required EXCEPTION (-20103, 'who is making the change must be given');
  assignee_required EXCEPTION (-20107, 'who the exception is assigned to must be given');
  not_new EXCEPTION (-20108, 'only a NEW exception can be assigned');
  already_assigned EXCEPTION (-20109, 'the exception is already assigned to that person');
  changed_concurrently EXCEPTION (-20202, 'the exception changed while it was being updated; read it again');
  v_table STRING;
  v_code STRING;
  v_status STRING;
  v_owner STRING;
  v_current STRING;
  v_ok INTEGER DEFAULT 0;
BEGIN
  IF (NOT REGEXP_LIKE(SOURCE_ID, '^[A-Za-z][A-Za-z0-9_]*$')) THEN
    RAISE source_invalid;
  END IF;
  v_table := '{{ DATABASE }}."EXCEPTIONS"."' || UPPER(SOURCE_ID) || '"';
  SELECT "REJECTION_CODE", "STATUS" INTO :v_code, :v_status FROM IDENTIFIER(:v_table) WHERE "EXCEPTION_ID" = :EXCEPTION_ID;
  IF (v_code IS NULL) THEN
    RAISE exception_not_found;
  END IF;
  SELECT MAX("OWNER") INTO :v_owner FROM {{ DATABASE }}."CONTROL"."REJECTION_CODES" WHERE "CODE" = :v_code;
  IF (ACTOR IS NULL OR TRIM(ACTOR) = '') THEN
    RAISE actor_required;
  END IF;
  IF (ASSIGNEE IS NULL OR TRIM(ASSIGNEE) = '') THEN
    RAISE assignee_required;
  END IF;
  IF (v_status <> 'NEW') THEN
    RAISE not_new;
  END IF;
  v_current := (SELECT "ASSIGNEE" FROM {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" WHERE "SOURCE_ID" = :SOURCE_ID AND "EXCEPTION_ID" = :EXCEPTION_ID AND "KIND" = 'assign' ORDER BY "EVENT_AT" DESC LIMIT 1);
  IF (v_current = TRIM(ASSIGNEE)) THEN
    RAISE already_assigned;
  END IF;

  BEGIN TRANSACTION;
  INSERT INTO {{ DATABASE }}."CONTROL"."EXCEPTION_HISTORY" ("EVENT_ID", "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "KIND", "FROM_STATUS", "TO_STATUS", "ASSIGNEE", "ACTOR", "EVENT_AT", "NOTE")
  SELECT UUID_STRING(), :SOURCE_ID, :EXCEPTION_ID, :v_code, :v_owner, 'assign', NULL, NULL, TRIM(:ASSIGNEE), TRIM(:ACTOR), SYSDATE(), NULL;
  SELECT COUNT(*) INTO :v_ok FROM IDENTIFIER(:v_table) WHERE "EXCEPTION_ID" = :EXCEPTION_ID AND "STATUS" = 'NEW';
  IF (v_ok = 0) THEN
    ROLLBACK;
    RAISE changed_concurrently;
  END IF;
  COMMIT;
  RETURN 'exception ' || EXCEPTION_ID || ' assigned to ' || TRIM(ASSIGNEE) || ' by ' || TRIM(ACTOR);
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
