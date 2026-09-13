# Runbook: recover a draft rule catalog from legacy Java

Story S5.4.1 (ADR 0044). Rule Recovery reads a Splitter/Loader's Java source and proposes rule catalog entries — one business rule per entry, with a citation to exactly where it lives in the code — for a steward to review. It never writes into `rules/` directly and never marks an entry confirmed.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The source | Steward / agent engineer | The Java file(s) to recover from are on hand — `agents/examples/rule_recovery/java/{Splitter,Loader}.java` is a committed illustrative stand-in until real Splitter/Loader source is available. |
| 2. Credentials | Agent engineer | `ANTHROPIC_API_KEY` is set in the environment; `pip install "astra-agents[llm]"`. Check it with `astra-agents spec-reader test-connection` — the same key and account serve every agent that calls the Anthropic API. |
| 3. What to call it | Steward | The catalog group these rules belong to (`rules/<group>/`, usually a custodian or spec id) and who owns them (confirms or rejects) are known. |

## Running it

```bash
astra-agents rule-recovery run Splitter.java Loader.java \
  --group pershing_loader --owner-name "Data steward, custodial" --owner-email steward@example.com
```

Writes one `rules/<group>/<name>.yaml` per recovered entry, next to `candidate_tests.yaml` and `report.md`, under `work/rule-recovery/<group>/`.

Read the report in order:

1. **Entries** — every rule recovered, its class, its citation, and (for a rejection rule) the code it traces.
2. **Loader rejection codes** — how many of the codes actually found in the source (scanned independently of the model) are traced to at least one entry. Anything listed as not traced is either a rule the model missed or a code the source declares but never actually uses — read the citation and decide which.
3. **Embedded SQL — routed to SnowConvert AI, not parsed** — every SQL snippet found, with its dialect and why it was not turned into a rule. A "not covered by any embedded_sql citation" line means a T-SQL idiom was found in the source that the model did not route; that source needs a second look before trusting the entries around it.
4. **Unrecovered** — passages the model would not guess a rule for. Read the citation and either recover it by hand or note why it genuinely is not a rule (dead code, a comment, plumbing with no business meaning).

## Deciding

- **A recovered entry**: every entry is `status: recovered`, never anything else — this agent has no way to set another status. A steward reviews the citation against the actual code and either confirms it, rejects it, or marks it a legacy defect through `astra-spec rules set-status` (`rules/README.md`), the same command for every rule regardless of who or what proposed it.
- **A candidate test**: a starting point, not a runnable test — `candidate_tests.yaml`'s `given`/`expect` describe what a real test should prove; writing the real test is a person's job (or, once it exists, the Test Generator's).
- **An untraced rejection code or an unrouted T-SQL line**: read the citation the report points near and look for what the model missed; re-run with a note in mind, or recover that entry by hand.

## Notes

- The real model call needs `ANTHROPIC_API_KEY`; there is no offline mode. `astra_agents.rule_recovery.LlmClient` is the interface a test double implements instead, for CI and local development without an account.
- `find_rejection_codes`'s `"L###"` pattern is calibrated to the illustrative fixture's own convention — the real Loader's rejection-code convention is not yet known (`docs/backlog-v0.2.md`'s own Risks section says so) and this will need recalibrating once real source is available.
- A large source file can produce a tool call big enough to hit the token budget before the model finishes it, the same as Spec Reader — pass `--max-tokens` with a higher number and re-run.
- `--group` decides where entries are placed (`rules/<group>/`) and is a person's choice, not something the agent infers.
