# ADR 0018: One command renders a release bundle, deterministically, from the compiled config alone

Date: 2026-09-06
Status: Accepted
Story: S3.1.2 Render a release bundle (E3, F3.1, WBS 2.3.1, 2.3.14)

## Context

A release is one reviewable unit (product spec Appendix A): everything rendered for a source, committed together, deployed by the pipeline and traceable to its inputs. The bundle contract (ADR 0005) fixed the layout and how the pipeline deploys and tests it; the compiler (ADR 0017) produces the resolved model renderers need. What remained was the rendering itself: which artifacts, from what, and the guarantee that re-rendering an unchanged config changes nothing, so a diff in `releases/` is always a change in an input.

## Decision

1. **Renderers take only the compiled config.** `astra_data.render` is a set of renderers per artifact kind, selected by target profile, each a pure function of the `CompiledConfig`. No renderer reads a clock, the environment, the file system or the network. The bundle version is a digest of the rendered files and PROVENANCE.json carries no timestamp, so an unchanged config renders byte-identical output and `astra-data render --check` can tell a stale bundle from a current one.

2. **Every artifact kind is rendered now, with the content the compiled model already determines.** Bronze DDL types every field of each logical record from the spec's pictures and types, keeps header and trailer values on the file registry, and gives problems their rejection code. Pipeline SQL is the lines view that scopes RAW_LINES to the source's files, the intake procedure that registers landed files as pending, the process procedure that runs the stages in order, and the task that runs it on the tier's warehouse every fifteen minutes. Data metric functions measure row counts, nulls in required fields and duplicates of a single-column merge key. Tests check that files are not stuck, are registered once, that every problem code is in the taxonomy, that merge keys are unique within a file and that every parsed record traces to a registered file. Docs describe the source from its layout to its delivery. The Atlan payload declares the Bronze and Silver tables, their columns with PII classifications, the glossary term of each Silver entity, and the lineage processes between them.

3. **Stages are procedures; the process procedure is the seam.** Parse (S3.2.2), merge (S3.2.3), resolution (S3.2.4) and DQ (F3.3) each add a procedure and a line to the process procedure. The task, the file registry and the problems table do not change shape when they arrive, and the rules the config states in words are documented until the DQ Generator gives them SQL.

4. **Names are the contract with the foundation.** Tasks are named `<CUSTODIAN>_..._PROCESS` so the task-failure detector attributes failures to the custodian (ADR 0007). Bronze objects are named `<SOURCE>_<RECORD>`, `<SOURCE>_FILES`, `<SOURCE>_PROBLEMS`, `<SOURCE>_LINES`; every identifier is quoted. The lines view reads RAW_LINES through the delivery patterns the alerting already uses, and intake reads CONTROL.FILE_LOAD_LOG, so what the pipeline processes is exactly what the landing zone reported.

5. **Environment-neutral output.** SQL uses the bundle placeholders (`{{ DATABASE }}`, the tier warehouses). The Atlan payload additionally uses `{{ ATLAN_CONNECTION }}`, which the governance integration (E8) fills when it pushes the payload.

6. **The example config's bundle is not committed.** The example is a smoke test that is never deployed; CI renders it to a scratch directory and runs the bundle check there. Real configs render into `releases/` and their bundles are committed and checked with `--check`.

## Consequences

- A pull request that changes a config, a spec version, the canonical model or a rule's status shows the resulting diff in the bundle, because the bundle is a function of those inputs and CI fails when it is stale.
- Renderers for a second target profile are a second entry in `RENDERERS`; the bundle layout and provenance are the same.
- S3.2.1 renders the per-custodian Snowpipe alongside; S3.2.2 fills the typed Bronze tables and the problems table from the lines view; S3.2.6 turns the fixed fifteen-minute schedule into the cutoff-driven orchestration the 20-minute window needs.
