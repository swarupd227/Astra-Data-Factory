# ADR 0002: Iceberg storage and Open Catalog

Date: 2026-09-05
Status: Accepted
Story: S1.1.2 Iceberg external volume and Open Catalog (E1, F1.1, WBS 2.1.2)

## Context

The spec (Section 10) fixes the MVP storage format as Snowflake-managed Iceberg on S3, read by Snowflake and by other engines, first of all pg_lake for UMP (S7.2.2). The story requires Bronze, Silver and Gold tables to be Iceberg on an S3 external volume, exposed through Snowflake Open Catalog so that an external client can read a table within a minute of its creation, and that roles without the catalog grant are denied.

Snowflake Open Catalog is a hosted Apache Polaris with no Terraform provider. Its account is created from Snowsight and cannot be scripted.

## Decision

1. **One dedicated bucket per environment**, in the client's AWS account, created by the same Terraform root as the Snowflake objects. Versioned, SSE-S3, public access blocked, TLS enforced by bucket policy. The bucket holds nothing but Iceberg data and metadata, so its policy stays simple.

2. **Two IAM roles with distinct trust and distinct privilege.** Snowflake's role may read, write and delete: it is the only writer. Open Catalog's role may read only: it vends read credentials to external engines. Both roles use an external id condition.

3. **The Snowflake role ARN is composed** from the account id and a fixed name so the external volume can be created before the role. The role's trust policy is then built from the IAM user and external id the volume reports. One apply, no manual step in between.

4. **Iceberg by default at the database level.** `EXTERNAL_VOLUME`, `CATALOG = 'SNOWFLAKE'` and `STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'` are set on the database, so renderers write `CREATE ICEBERG TABLE` without naming storage and the files stay readable by third-party engines.

5. **Open Catalog is fed by CATALOG_SYNC, not by an external catalog.** Snowflake stays the catalog of record and pushes metadata to Open Catalog through a catalog integration. External engines read through Open Catalog's Iceberg REST endpoint. This keeps the "no LLM or engine other than Snowflake writes the lake" principle and avoids Snowflake depending on Open Catalog availability for its own reads.

6. **Open Catalog is provisioned by a small Python tool, `tools/opencatalog`**, driving the Polaris management API idempotently: catalog, two catalog roles (manage content, read only), two principal roles, two principals. It is the pattern for any managed service without a provider: explicit ensure-style functions, tests against an in-memory fake, credentials printed once and never stored by the tool.

7. **Two-pass enablement.** Terraform creates bucket and volume; the tool creates the catalog and reports the IAM identity Open Catalog assumes; Terraform then creates the Open Catalog role, the integration and `CATALOG_SYNC`. Open Catalog stays optional in Terraform so environments can exist before the Open Catalog account does.

8. **Acceptance is measured live by `opencatalog verify`**: probe table created in Snowflake, catalog listing timed, DuckDB read through the reader principal, and a role-less principal shown to be denied. Results are printable as JSON for the gate evidence pack.

## Consequences

- Every environment carries its own bucket and catalog; nothing is shared across environments or clients (spec Section 12).
- `CATALOG_SYNC` is set with `snowflake_execute` until the provider exposes the parameter on databases.
- The Open Catalog admin service connection is the one credential created by hand, alongside the Terraform service user from ADR 0001.
- Synced tables appear in Open Catalog under a namespace derived from the Snowflake database and schema. The verification searches every namespace rather than assuming the layout, so it holds if Snowflake changes the mapping.
- DuckDB is the reference external reader because it needs no cluster. pg_lake and Spark use the same reader principal and endpoint.
