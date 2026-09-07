# ADR 0026: Gold read models published per custodian and business date behind a watermark written last

Date: 2026-09-07
Status: Accepted
Story: S3.2.8 Gold read model and watermark (E3, F3.2, WBS 2.3.9)

## Context

The canonical Silver tables are the platform's model, not the consumer's. UMP, reading through pg_lake, needs tables in its own shape, and it must never read a day that is half written: positions in, transactions not yet. The product spec puts the read model in Gold and asks for a published watermark; the backlog asks for one watermark row per custodian and business date written after every Gold refresh, and for a consumer query filtered by it to return complete days only.

## Decision

1. **Read models are declared in the domain pack.** `domains/<pack>/read-models.yaml` lists the Gold tables: for each, the entity it reads, the consumer's columns (a column of the entity under the consumer's name, or an expression over the entity's columns with its own type), and which Gold columns name the custodian and the business date. The file pins the model version it reads, so a new model version changes nothing in Gold until the read models move to it; the pack loader resolves them against that version: unknown entities and columns, expressions that name no column, and a custodian or business-date column of the wrong type are errors. The custodial pack ships canonical shapes for positions, transactions, cash balances and accounts; a client instance replaces them with the consumer's own (for Envestnet, the UMP read model of S7.2.1).

2. **The generation plane renders a Gold bundle per pack**, `releases/<pack>-gold`, committed and checked in CI like the reference-data bundle: one Iceberg table per read model in `GOLD`, with the consumer's columns plus `PUBLISH_ID` and `PUBLISHED_AT`, PII tags carried over from the entity columns; `GOLD.WATERMARK`, one row per custodian and business date; `CONTROL.GOLD_PUBLISH_LOG`, one row per publish; the procedure `CONTROL.PUBLISH_GOLD(custodian)`; and a `<TABLE>_PUBLISHED` view per read model.

3. **A publish rewrites the days a DAG run touched and writes each day's watermark last, atomically.** The procedure runs only when a `CONTROL.CUSTODIAN_RUNS` row exists that no publish has followed. It finds the business dates whose canonical rows changed since the previous publish started, and for each, oldest first, in one transaction: deletes and re-inserts the custodian's rows of that date in every dated read model, then merges the watermark row. A consumer therefore sees either the previous complete day or the new complete day, never the rows between; a rerun after a late file or a re-delivery replaces the day and its watermark together. Read models without a business date are the custodian's snapshot, rewritten whole before the dated ones. The publish is logged once at the end, so a failure leaves the log unchanged and the next publish covers the same days again.

4. **The business date is the data's, not the delivery's.** A row belongs to the day its business-date column says (`AS_OF_DATE` for a position, `TRADE_DATE` for a transaction). The watermark says the custodian's rows of that day are complete in Gold as of the run that published them. The late detector and the gate reason about delivery dates (ADR 0007, ADR 0024); the watermark reasons about data dates, and the run id on it links the two.

5. **The DAG ends in the publish.** Every source's rendered `tasks.sql` creates `<CUSTODIAN>_PUBLISH` once, with `IF NOT EXISTS`, and adds its own process task as a predecessor with `ALTER TASK ... ADD AFTER`. Because a process task is replaced on every deploy, the edge is fresh each time; the task runs after every source of the custodian has processed, and it belongs to the custodian's DAG alone. When nothing changed it returns without touching Gold.

6. **Consumers read the views or join the watermark.** `GOLD.<TABLE>_PUBLISHED` joins the table to the watermark on custodian and business date (a snapshot is visible once its custodian has any published day). A consumer that reads the base table must join `GOLD.WATERMARK` itself; the rendered tests hold the invariants the views rely on: every Gold day has a watermark, no row of a day is newer than its watermark, one watermark per custodian and date.

## Consequences

- The canonical Silver tables must exist for the procedure to run; they are deployed by the CDM bundle (S7.1.1). The procedure compiles without them.
- Rows the canonical MERGE retires are not deleted from Silver (ADR 0022), so a Gold day is what Silver holds for it; a full-refresh source that removes a position leaves it in Silver until that is addressed with the parity work.
- The publish warehouse is the tier of whichever source first created the task; the procedure's work is proportional to the days a run touched.
- Two publishes of the same custodian never overlap (one DAG, no overlapping execution); two custodians publish concurrently and touch only their own rows.
- `MAX_BY`, explicit transactions inside a procedure and DML on Iceberg tables inside them are the documented forms; they are confirmed live with the rest of the bundle in S7.1.1.
