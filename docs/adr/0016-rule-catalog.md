# ADR 0016: The rule catalog is files in Git with a recorded status history

Date: 2026-09-06
Status: Accepted
Story: S2.4.1 Rule catalog store (E2, F2.4, WBS 2.2.11)

## Context

The product spec defines the Rule Catalog as every business rule, mapping, validation and exception rule with citation, owner, status and lineage, stored as database plus Git. Rules recovered from legacy code by an agent, or written by a person, must be confirmed or rejected by their owner before a pipeline depends on them, and a rejected rule must not be used. Until now configs carried rules inline, so the same rule could be stated twice with two statuses and nothing recorded who changed a status.

## Decision

1. **A rule is a file**, `rules/<group>/<name>.yaml`, with the id `<group>.<name>`. The group is what the rule belongs to: a spec, a custodian or a domain. The file carries the rule in plain words, exactly one citation (a spec page and line, a file and line of legacy code, or a document page), its class (ingestion, normalisation, business), its owner, its status and its history. Git is the system of record; the workbench (E6) mirrors the catalog into its own store and writes back through the same files.

2. **The status is the history.** Every status a rule has had is an entry with who set it and when, oldest first, and the current status must equal the last entry. `astra-spec rules set-status` is the only way the tooling changes a status: it appends an entry with the time and rewrites the file. A file whose status disagrees with its history, whose history goes backwards, or which repeats a status fails validation, so a status cannot change without a record of who changed it.

3. **Citations are checked.** A spec citation must name a version in the spec registry and a page within its document. Code and document citations are recorded as given; the RE Harness (S5.4.x) is what makes them precise.

4. **Configs reference rules by id** under `rules` and on the mappings they govern; they no longer define rules. A mapping's rule must be listed under the config's rules, so the config states what it depends on in one place. With the catalog, `astra-data validate --rules rules` fails a config that references a rule the catalog does not have or one its owner has rejected. A legacy defect is a documented behaviour the client may choose to reproduce for parity or to fix; referencing one is allowed and the parity stories (S7.1.6) decide.

5. **Lineage is computed, not stored.** Which configs use a rule is read from the configs, because a stored list would drift from them. `astra-spec rules show` and `rules list --configs` print it; validation notes recovered or confirmed rules no config uses.

## Consequences

- The config schema's inline `rules` block becomes a list of catalog ids; the example config references `pershing_gcus.quantity_sign`.
- The Rule Recovery agent (S5.4.x) writes catalog entries as recovered with code citations; stewards confirm or reject them through the same command the workbench will call.
- CI validates the catalog, checks spec citations against the registry, and validates configs against the catalog.
- Searching rules across engagements (product spec Section 8, with client isolation) is a later story; the file layout and the group make that a filter, not a migration.
