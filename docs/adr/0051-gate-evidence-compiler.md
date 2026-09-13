# ADR 0051: The Gate Evidence Compiler never omits a criterion, and an empty evidence path is a real NOT MET

Date: 2026-09-13
Status: Accepted
Story: S5.11.1 Gate Evidence Compiler: build and evaluate (E5, F5.11, WBS 2.5.22)

## Context

No fixed list of gate criteria existed anywhere in this repository before this story. ADR 0034 (the parity report) said so directly: compiling the full gate evidence pack was "Control-plane work well past E4," and left parity's own `report.json` as "the one piece of evidence... in the place a later compiler would look for it." This story arrived earlier than that ADR expected (WBS 2.5.22, the Agents plane, not the Control plane) — the compiler this agent builds reads exactly the evidence that ADR anticipated, from six tools this factory has already built, plus a seventh kind of evidence — a human's own approval — that has never had a file format at all.

## Decision

1. **Seven named criteria, one per verification tool already built, plus one for approval.** `GATE_CRITERIA` names `dq_score`, `parity`, `volume`, `chaos`, `dr_drill`, `agent_eval` and `approval` — not invented from nothing, but the direct list of "verification results... and metrics" this factory can actually produce today (DQ runner, parity report, volume test, chaos scenarios, DR drill, agent evaluation). A later story can extend the list; this one is the complete set of evidence this repository's own tools can currently supply.

2. **Nothing here re-scores anything.** Each tool's own `report.json` already carries a computed pass/fail signal — `meets_target` (DQ, parity), `proven` (chaos, DR), `status == "published"` (volume, which has no single boolean of its own), `all_passed`/`passed` (agent eval) — verified directly against each tool's own `to_dict()` before being read here, not assumed. This agent's only job is picking the right already-computed field per tool and citing it; re-deriving any of these numbers here would risk disagreeing with the tool that owns them.

3. **A criterion with no evidence path, a missing file, or an unreadable file are all the same thing: NOT MET, with no evidence, not an error.** `_read_json` returns `None` for all three cases rather than raising — a corrupt report is itself evidence something upstream is broken, and this agent should say so rather than crash on it. This is not a special case bolted onto the design; the criteria list is built by iterating all seven specs unconditionally and asking each "do I have evidence for this," so there is no code path that can produce a pack with fewer than seven criteria. A test proves it directly: a pack built from `EvidenceSources()` (nothing given at all) still lists all seven, every one `NOT MET`.

4. **Approval is a small, append-only log — the same shape Exception Triage's own decisions log already established**, not a new format invented from scratch. `record_approval` mirrors `exception_triage.record_decision` almost exactly: append one entry, rewrite the file, oldest first. The Control plane's own real approval workflow (`POST /approvals/{id}`, product spec's API sketch) does not exist yet; this is a real, working stand-in with the same shape a later UI-backed version would likely take, not a placeholder that has to be thrown away.

5. **An approval only counts for the exact release it names.** `generate` looks for an approval entry whose `release` matches the pack's own `release` argument; an approval for a different release does not carry over — a test proves this directly. A gate pack is per-release evidence; an approval from a different release is not evidence for this one, the same way a DQ score from last week is not evidence for today's release either (each tool's own report already carries its own business date or run identity for the same reason).

6. **No eval gold set.** This story's acceptance criteria are behavioral (every criterion listed with its evidence; a criterion with none is shown as NOT MET), not a precision/recall percentage — the same shape DQ Generator's, Rule Recovery's, Exception Triage's and Drift Watcher's own acceptance criteria took, all without a formal `eval.yaml` for the same reason. `test_the_story_acceptance_criteria_are_satisfied` proves both criteria directly, against a committed illustrative evidence set and an empty one.

## Consequences

- The six tool-report fixtures under `agents/examples/gate_evidence_compiler/` are hand-authored to match each tool's own confirmed `to_dict()` shape exactly (verified by reading `dq.py`, `parity_report.py`, `volume.py`, `chaos.py`, `dr.py` and `agent_eval.py` directly, not guessed) — illustrative content, not real run output, since volume, chaos and DR all need a live Snowflake sandbox this session does not have.
- A gate pack's `all_met` is a simple AND across all seven criteria; there is no partial-credit or weighted scoring here, matching "gate decisions are made on the pack alone" (the product spec's own guardrail for this agent) — a pack either says the release is ready, or it names exactly what is missing.
- Extending the criteria list (a new verification tool, a second kind of approval, a per-criterion severity) is adding one more `CriterionSpec` and, where needed, one more field on `EvidenceSources` — the shape does not need to change.
