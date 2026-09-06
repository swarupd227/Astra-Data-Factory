# rules

The rule catalog (product spec Section 4): every business rule the factory applies, stored once with its citation, class, owner, status and history, and referenced by configs. Git is the system of record; the workbench (E6) reads this catalog, it does not replace it.

```
rules/<group>/<name>.yaml      one rule; its id is <group>.<name>
```

The group is what the rule belongs to: a spec id (`pershing_gcus`), a custodian, or a domain. A rule has:

| Field | Meaning |
|---|---|
| `text` | The rule in plain words, as its owner would state it |
| `class` | `ingestion` (how a file is read and accepted), `normalisation` (how a value becomes canonical), `business` (what the data means or how it is treated) |
| `citation` | Exactly one of a spec page and line (`spec: { id, version, page, line }`), a file and line of legacy code (`code: { file, line, end_line, repository }`), or a document page (`document: { name, page, section }`). A spec citation must name a version in the registry and a page within its document. |
| `owner` | Who confirms or rejects it: name and email |
| `status` | `recovered` (found by an agent or a person, not yet confirmed), `confirmed`, `rejected`, `legacy_defect` (the legacy system did this, and it is wrong) |
| `history` | Every status the rule has had, oldest first, with who set it and when. The current status is the last entry. |
| `applies_to`, `tags` | Custodians and entities the rule concerns; free tags for search |

## Changing a status

```bash
astra-spec rules set-status pershing_gcus.refresh_mode --status confirmed --by steward@example.com --note "Checked against the sample files"
```

The command appends a history entry with the time and rewrites the file; a status edited by hand without a matching history entry fails validation, as does a history that goes backwards in time or repeats a status. `rejected` and `legacy_defect` are recorded the same way, so why a rule was rejected is in Git with its author.

## Lineage to configs

A config lists the rules it uses under `rules` and names one on each mapping it governs. `astra-spec rules show <id> --configs configs` lists the configs that reference a rule; `astra-spec rules list --configs configs` shows usage for every rule. A config that references a rule the catalog does not have, or one that has been rejected, fails `astra-data validate --rules rules`, which CI runs.

## Commands

| Command | What it does |
|---|---|
| `astra-spec rules validate [--configs configs]` | Every rule file against the schema, its place in the catalog, its history and its citation; with configs, every reference resolves and none is rejected |
| `astra-spec rules list [--status s] [--class c] [--group g] [--configs configs]` | The catalog, with usage when configs are given |
| `astra-spec rules show <id> [--configs configs]` | One rule with its citation, history and the configs that use it |
| `astra-spec rules set-status <id> --status s --by who [--note text]` | Record a status change |
