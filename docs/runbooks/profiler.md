# Runbook: profile a sample file against a Source Spec

Story S5.2.1 (ADR 0042). The Profiler reads a fixed-width sample file against a Source Spec already in the registry and reports types, nulls, value sets and record-type counts, flagging any field whose observed data does not fit the type the spec declares for it. It is read-only: it never writes into the registry and never touches a production table.

## Before the first run

| Step | Who | Done when |
|---|---|---|
| 1. The spec | Agent engineer | The Source Spec this sample should match is already in the registry (or a draft from the Spec Reader), as a `fixed_width` file. |
| 2. The sample | Agent engineer | A sample file of the same layout is on hand — a handful of real or de-identified rows is enough; the agent reads every line given to it. |

No credentials, no deploy, no Snowflake connection: this is plain Python reading two files already on disk.

## Running it

```bash
astra-agents profiler run sample.dat --spec specs/pershing_gcus/2017-07-25.yaml
```

Writes `report.md` next to `report.json` under `work/profiler/<spec id>/<spec version>/`.

Read the report in order:

1. **Record types** — how many lines matched each record type. A count of 0 for a record type the spec declares (or lines left unmatched entirely) is worth a look before reading anything else.
2. **Per field: nulls, distinct, top values** — how complete and how varied a field actually is in this sample; a field profiled as always the same one value, or always blank, is worth confirming against what the layout document says it should be.
3. **Type** — `OK` or `**MISMATCH**`, with the fraction of rows that failed and a citation to the offending line. This is the drift the story asks for: the spec says a field is one type, this sample's own characters say otherwise.

## Deciding

- **A flagged field**: read the document at the line the report names. It might be a genuine data quality issue in the sample (worth escalating), a spec that has drifted from what the custodian actually sends (the Spec Reader or a person should update it), or a sample that is not representative (get a better one before drawing conclusions).
- **This agent never fixes anything itself**: "findings confirmed by reviewer" is the product spec's own metric for it. A profile is an input to updating a spec or filing a DQ finding, not a change on its own.

## Notes

- Delimited files are out of scope today; `astra-agents profiler run` against a non-`fixed_width` spec fails clearly rather than guessing at a parse.
- `agents/examples/profiler/` is a self-contained, committed example (a 750-character illustrative layout and a sample with two deliberately corrupted values) — run the command above against it to see the flagging behavior without needing a real document.
