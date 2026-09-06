-- Security master: the key (SECURITY_ID) identifies one replica row. Returns keys with more than one.
SELECT "SECURITY_ID", COUNT(*) AS ROW_COUNT
FROM {{ DATABASE }}."REFERENCE"."SECURITY_MASTER"
GROUP BY "SECURITY_ID"
HAVING COUNT(*) > 1;
