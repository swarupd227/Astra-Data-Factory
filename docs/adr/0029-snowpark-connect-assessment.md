# ADR 0029: The Normalizer decision's third candidate is scored by a harness, not by opinion

Date: 2026-09-08
Status: Accepted
Story: S3.3.2 Snowpark Connect assessment hook (E3, F3.3, WBS 1.4.3)

## Context

The legacy Normalizer is Spark code. The product spec allows two ends for it: regenerate its behaviour from the confirmed rule catalog, or keep the code and run it through Snowpark Connect for Spark where the assessment scores that higher. The assessment memo lists three candidates and the criteria; what it lacked for the third candidate was evidence: does a Normalizer transformer run on Snowflake, what has to change, what does that cost, and does it produce the same rows.

## Decision

1. **An assessment is a directory in the repository.** `assessments/<decision>/` holds the memo, sample inputs that follow the spec layouts, the expected rows the pattern library produces for them, and one directory per transformer with the legacy code (`original.py`), the version that runs on Snowpark Connect (`snowpark.py`) and `changes.yaml`, where the engineer records each change, why it was needed and its effort in hours. A transformer without a changes file is refused: no change is a recorded fact too.

2. **The same code runs on two engines.** `astra-verify snowpark assess` loads `snowpark.py`, hands it the sample lines as one-column DataFrames, times the session start and the run, collects the rows and compares them with the expected output. `--engine local` is a local Spark session, the reference; `--engine snowpark-connect` is Snowpark Connect for Spark, the candidate, the same DataFrame code on Snowflake. The local engine feeds the lines through a `VALUES` query so the reference needs no Python worker and runs on any driver Python; Snowpark Connect uploads them with `createDataFrame`.

3. **Every run is a result file; the memo is rewritten from the result files.** `results/<engine>-<run id>.json` records status, rows, parity and mismatch count, seconds, the error when a transformer fails, the recorded changes and effort, the code delta between the two versions, and the environment (Python, pyspark, Snowpark Connect, account). The memo's evidence section, between markers, is regenerated from the latest run per transformer and engine, with the changes and effort listed; nothing else in the memo is touched. A failure or a parity miss is a row in the table, not an absence.

4. **The two transformers stand for the family's idioms.** `position_normalizer` (fixed-width parse, separate sign, implied decimals; RDD text read and a Python parse in the legacy code) and `drip_split` (one-to-many split; a Python UDF and an RDD flatMap). Their Snowpark Connect versions replace the RDD reads and Python functions with DataFrame expressions, which is the whole of what changed and is recorded as such.

## Consequences

- Both transformers ran on a local Spark session with parity on the samples during this story; the Snowpark Connect run needs a Snowflake account and the `snowpark-connect` package and is run per the memo before the decision. The evidence table shows "not run" until then, on purpose.
- The verification package gains `pyyaml` and the optional extras `spark` and `snowpark-connect`; CI installs `spark` for the reference run, which needs a Java runtime the GitHub runner has.
- Effort figures are the engineer's, per change. The memo says what the evidence does not: samples are not history, and operability around a kept transformer is not measured by the harness.
