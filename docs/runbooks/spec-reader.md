# Runbook: read a layout document into a draft Source Spec

Story S5.1.1 (ADR 0041). The Spec Reader agent turns a custodian's layout document into a draft Source Spec with a page citation on every field. It never writes into the registry itself.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The document | Agent engineer | A layout document (PDF today; Word via the same interface) is on hand. |
| 2. Credentials | Agent engineer | `ANTHROPIC_API_KEY` is set in the environment; `pip install "astra-agents[llm,pdf]"` (add `,docx` for a Word document). Check it with `astra-agents spec-reader test-connection` before the first real run. |
| 3. What to call it | Agent engineer | The spec id (the `specs/<id>/` directory it would become), the version (the file name), the file type and the custodians that deliver it are known. |

## Checking the key

```bash
astra-agents spec-reader test-connection
```

Lists the account's models — a free call that generates nothing and sends no document — to prove `ANTHROPIC_API_KEY` authenticates and the configured `--model` (default `claude-sonnet-5`) is one the account can actually use. Exit 0 means connected; exit 1 names what failed (authentication, connectivity, or an API error) without ever printing the key itself. There is no UI for this — the key is set as an environment variable the same way every other credential in this repository is, from Snowflake's `SNOWFLAKE_ACCOUNT` on down, never typed into a page or stored by any tool here.

## Running it

```bash
astra-agents spec-reader run GCUS.pdf \
  --id pershing_gcus_full --version 2017-07-25 --effective-from 2017-07-25 \
  --file-type position --custodian pershing
```

Writes `spec.yaml` next to `report.md` / `report.json` under `work/spec-reader/<id>/<version>/`.

Read the report in order:

1. **Valid** — whether the draft passes the same `source-spec-v0` schema the registry validates every committed spec against. An invalid draft is a bug in this agent, not a normal outcome; it should not happen.
2. **Citations** — every field's citation coverage per record. A field with no citation cannot appear here at all — the tool schema itself requires one — so full coverage on every listed field is expected; what to check is completeness against the document, not correctness of the citations that are there.
3. **Unparsed** — every passage the model would not guess. Read the document at the citation given and either fill the gap by hand or note why it genuinely cannot be extracted (a table split across a page break, a footnote referencing an appendix not included).

## Deciding

- **Promoting a draft**: this is a person's decision. Move `spec.yaml` to `specs/<id>/<version>.yaml`, add it to the registry, and review it exactly as carefully as a hand-written spec — the agent proposed it, it did not approve it.
- **A draft with unparsed passages**: fill the gaps by hand before promoting, or re-run with a note about what to look for; do not promote a spec with an unresolved unparsed passage covering anything the pipeline needs.
- **Building this agent's gold set**: once a draft has been reviewed and corrected into ground truth, its fields become the `expected` items of a `agents/spec_reader/eval.yaml` case (S4.3.4) — canonicalized the same way as the illustrative `agents/examples/spec_reader` one.

## Notes

- The real model call needs `ANTHROPIC_API_KEY`; there is no offline mode. `astra_agents.spec_reader.LlmClient` is the interface a test double implements instead, for CI and local development without an account.
- Word documents have no reliable page concept outside a renderer; a Word-sourced draft's `document.pages` is left unset, and citations from it name no page.
- `merge`, `split`, `lifecycle` and `pairing` (how a file changes Silver, split and lifecycle rules, pairing) are out of scope for this agent — they need the Modeler's and Rule Recovery's own reasoning, not a first pass at reading a layout.
