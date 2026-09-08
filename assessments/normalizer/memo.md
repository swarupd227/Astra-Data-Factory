# Assessment memo: what to do with the Normalizer

Decision owner: platform architect. Status: evidence being gathered; decision at the end of Discovery (WBS 1.4). Story S3.3.2 (ADR 0029) adds the third candidate's evidence.

## The question

The legacy Normalizer is a set of Spark transformers that turn custodian files into the shapes the Loader loads. The rule catalog recovers what they do (E2); the question is how the target platform gets that behaviour. Three candidates:

| | Candidate | What it means |
|---|---|---|
| A | Regenerate from the catalog | The confirmed rules become configs; the factory renders the parse, merge and resolution pipelines (F3.2). The Spark code is retired. |
| B | Rewrite by hand in Snowpark | Engineers port each transformer to Snowpark DataFrame code inside the factory's bundles. |
| C | Keep the Spark code, run it through Snowpark Connect | The transformers stay PySpark; Snowpark Connect for Spark runs the DataFrame API on Snowflake. Only what Snowpark Connect does not support is changed. |

## How the candidates are scored

| Criterion | Weight | A | B | C |
|---|---|---|---|---|
| Parity with the golden dataset | must | proven by golden replay (E4) | proven by golden replay | proven by the harness on samples, then golden replay |
| Effort to first run | high | config per source; renderers exist | hours per transformer, all of them | hours per transformer, only where the API differs (evidence below) |
| Run time in the 20-minute window | high | dynamic tables and set-based procedures | Snowpark, set-based | Snowpark Connect; per-run figures below |
| Operability: exceptions, lineage, run ledger, DMFs | high | native to the bundle | to be built per transformer | to be built around the transformer |
| Agent-ability: can the factory regenerate it on drift | high | yes, by design | no | no |
| Licence and platform cost | medium | none beyond Snowflake | none | none; Snowpark Connect is free with the account |

The decision is not made in this memo: it is made when the evidence rows below are filled for both transformers on both engines and the golden replay of E4 has run. What this memo carries is the evidence for C, gathered by the harness, so that C is scored on what ran and what it cost, not on opinion.

## The two transformers

Two Normalizer transformers stand in for the family, chosen because they use the two idioms the legacy code relies on most:

- `position_normalizer`: fixed-width parse of a GCUS position detail line with a separate sign field and implied decimals; the legacy job reads text through the RDD API and parses each line in Python.
- `drip_split`: the one-to-many split of a DRIP transaction into a dividend and a purchase; the legacy job uses a Python parse function and an RDD flatMap.

Each transformer directory holds the legacy code (`original.py`), the version that runs on Snowpark Connect (`snowpark.py`) and the changes the engineer recorded with their effort (`changes.yaml`). The samples under `samples/` follow the spec layouts; `expected/` holds the rows the pattern library produces for them.

## How the evidence is gathered

```bash
astra-verify snowpark assess assessments/normalizer --engine local             # the reference: a local Spark session
astra-verify snowpark assess assessments/normalizer --engine snowpark-connect  # the candidate: the same code on Snowflake
```

Each run times the session start and every transformer, compares the rows with `expected/`, and writes `results/<engine>-<run id>.json`; the section below is rewritten from the result files. A run on Snowpark Connect needs the `snowpark-connect` package and the Snowflake connection variables the foundation uses.

## Evidence

<!-- evidence:start -->
Rewritten by `astra-verify snowpark assess` from `results/`; the latest run per transformer and engine. Do not edit by hand.

| Transformer | Engine | Run | Result | Rows | Parity with expected | Seconds | Changes for Snowpark Connect | Effort (h) | Code delta |
|---|---|---|---|---|---|---|---|---|---|
| `drip_split` | local | 20260908T063117Z-abcd1f15 (2026-09-08T06:31:17Z) | succeeded | 4 | yes | 5.3 | 3 | 4 | +43 / -57 lines |
| `drip_split` | snowpark-connect | not run | | | | | 3 | 4 | |
| `position_normalizer` | local | 20260908T063117Z-abcd1f15 (2026-09-08T06:31:17Z) | succeeded | 3 | yes | 0.796 | 3 | 3 | +35 / -47 lines |
| `position_normalizer` | snowpark-connect | not run | | | | | 3 | 3 | |

**drip_split**, 4 hours recorded by platform architect:

- api_gap: RDD text read (sparkContext.textFile) replaced by a one-column DataFrame of lines handed in by the caller (0.5 h). Snowpark Connect implements the DataFrame API only; there is no RDD.
- api_gap: Per-line Python parse (rdd.map) rewritten as substring, trim, cast and to_date expressions (1.5 h). Python functions on RDDs do not run on Snowpark Connect.
- api_gap: The split (rdd.flatMap over a Python function) rewritten as explode over an array of struct parts chosen by when (2 h). The one-to-many step must be a DataFrame expression to run on Snowflake; explode(array(...)) is the idiom for it.
- unchanged: The split rule from the spec: the dividend keeps the amount and drops the quantity, the purchase negates the amount; part ids are the source id suffixed -1 and -2.
- unchanged: The output schema.

**position_normalizer**, 3 hours recorded by platform architect:

- api_gap: RDD text read (sparkContext.textFile) replaced by a one-column DataFrame of lines handed in by the caller (1 h). Snowpark Connect implements the DataFrame API only; there is no RDD, and the executor cannot read a local path.
- api_gap: Per-line Python parse function (rdd.map) rewritten as substring, trim, cast and when expressions (1.5 h). Python functions on RDDs do not run on Snowpark Connect; DataFrame expressions run on Snowflake unchanged.
- semantics: Implied decimals computed with decimal casts and division instead of Python Decimal (0.5 h). Keeps the scale explicit so both engines produce the same DECIMAL(18,5) and DECIMAL(15,6) values.
- unchanged: The layout offsets, the sign convention and the drop of header, trailer and short lines.
- unchanged: The output schema: account_number, cusip, quantity, price, as_of_date.

Session start, latest run per engine: local 8.609 s (pyspark 3.5.3).

<!-- evidence:end -->

## What the evidence does not say

- Samples are not history: parity on samples shows the transformer runs and does what the legacy code did on the cases the samples cover; the golden replay (E4) is the proof.
- The effort recorded is the engineer's, per change, for these two transformers; extrapolation to the family is by the count of transformers that use the same idioms, which Rule Recovery reports.
- Operability is not measured here: a transformer kept as Spark code still needs exception routing, the run ledger and lineage around it, which candidate A gets from the bundle.
