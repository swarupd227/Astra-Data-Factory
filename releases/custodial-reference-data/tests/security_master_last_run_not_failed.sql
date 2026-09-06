-- Security master: the most recent replication run did not fail. Returns the run when it did.
SELECT RUN_ID, STARTED_AT, STATUS, ERROR
FROM (SELECT * FROM {{ DATABASE }}."CONTROL"."REFERENCE_DATA_RUNS" WHERE FEED_ID = 'security_master' QUALIFY ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) = 1)
WHERE STATUS = 'failed';
