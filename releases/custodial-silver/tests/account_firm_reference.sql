-- Account -> Firm: (FIRM_ID) must exist in FIRM. Returns rows with no match.
SELECT e."CUSTODIAN_ID", e."ACCOUNT_NUMBER", e."FIRM_ID"
FROM {{ DATABASE }}."SILVER"."ACCOUNT" AS e
LEFT JOIN {{ DATABASE }}."SILVER"."FIRM" AS r ON r."FIRM_ID" = e."FIRM_ID"
WHERE r."FIRM_ID" IS NULL;
