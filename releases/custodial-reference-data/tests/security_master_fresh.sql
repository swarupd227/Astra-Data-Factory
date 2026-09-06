-- Security master: once replicated, replicated again within 26 hours. Returns the feed when the last success is older.
SELECT FEED_ID, MAX(FINISHED_AT) AS LAST_SUCCESS
FROM {{ DATABASE }}."CONTROL"."REFERENCE_DATA_RUNS"
WHERE FEED_ID = 'security_master' AND STATUS = 'succeeded'
GROUP BY FEED_ID
HAVING MAX(FINISHED_AT) < DATEADD('hour', -26, SYSDATE());
