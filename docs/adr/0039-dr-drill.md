# ADR 0039: RTO and RPO are the client's numbers, never invented; RPO is proven by full replay, not measured as an invented time figure

Date: 2026-09-12
Status: Accepted
Story: S4.3.3 DR drill (E4, F4.3, WBS 2.4.10)

## Context

"The standardized zone" is Bronze (raw lines turned into typed, standardized records) and the Silver it derives — the two schemas a source's own pipeline populates and the two a real disaster against Snowflake's own storage would take out. The files a custodian delivers, and the golden datasets captured from them, are retained independently, in S3 (ADR 0003, ADR 0031); a DevOps engineer needs a scripted, repeatable way to prove that a disaster against the database alone can be recovered from that retained storage, within the RTO and RPO the client's proposal already promises.

## Decision

1. **RTO and RPO are required inputs, never defaulted.** Unlike the volume test's literal 20-minute budget (stated in that story's own acceptance criteria) or the DQ runner's documented platform-default target, this story gives no number at all — "the proposed RTO", "RPO" are named as if defined elsewhere, in a client proposal. `--rto-minutes` and `--rpo-minutes` are required flags with no default; inventing a number here would misrepresent a commercial commitment as an engineering one.

2. **RPO is a binary outcome in this drill, not a manufactured time figure.** The drill loads a normal day's sample (the baseline), drops `BRONZE` and `SILVER` outright (the disaster), then recreates the schemas and replays the exact same retained files (the restore). Because the replay is complete, a working restore path achieves zero rows lost by construction — RPO met — and any table whose restored count does not exactly match its baseline is reported as a named breach (the table and the row gap), not converted into an invented number of minutes with no principled basis. A `--rpo-minutes` target is still accepted and reported, so the drill's evidence reads the same way the client's proposal does, but the pass/fail is `rows_lost == {}`, not a computed duration.

3. **RTO is measured, not simulated, from the moment the disaster happens to the moment the restore's own load-and-process cycle completes** — recreating the schemas, redeploying the canonical model and the source bundle, restaging and reloading the same files, and reprocessing them. The sandbox's warehouse size affects this number the same way it affects the volume test's; a DR drill and a volume test share the same warehouse-sizing conversation.

4. **The drill runs in the same kind of throwaway sandbox every other NFR story this session uses**, not against a real environment. A destructive `DROP SCHEMA` against production is exactly the kind of action this factory's own safety discipline would refuse to run unsupervised; simulating the disaster inside an ephemeral sandbox proves the recovery *procedure* without ever touching anything real. `CONTROL_TABLES`, `CONTROL.ALERTS` and the sandbox's other machinery this drill reuses were already built for the dry run, the volume test and the chaos drill (S4.1.1, S4.3.1, S4.3.2); nothing about the sandbox itself changes for this story.

## Consequences

- Restoring "the standardized zone" necessarily restores from the files given to the drill, standing in for the landing zone's own retained copies; this drill does not re-verify that the landing zone's actual retention window covers the outage being simulated — that is a separate operational fact about the foundation's S3 lifecycle policy, not something a sandbox-based drill can observe.
- `REFERENCE`, `EXCEPTIONS` and `CONTROL` are not dropped — only `BRONZE` and `SILVER`, matching what "the standardized zone" names. A disaster scenario that also takes out reference data or exception history is a different, larger drill than this story asks for.
- The drill needs exactly the source's own detail record to exist in Bronze to measure a baseline; a source with no detail record (there is none in this domain pack) has nothing to restore and the drill refuses to start.
