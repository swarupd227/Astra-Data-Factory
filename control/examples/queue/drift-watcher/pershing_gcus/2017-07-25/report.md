# Drift Watcher: pershing_gcus 2017-07-25 vs drifted_sample.dat

6 line(s) checked. Drift detected: yes. This spec was not modified.

## Proposed delta

| Path | Current | Proposed | Description |
|---|---|---|---|
| file.record_length | 120 | 125 | 6/6 line(s) are 125 characters; the spec declares record_length 120 |
| records[detail].fields[security_type].codes | ['EQ', 'FI', 'MF', 'OP'] | CD | detail.security_type has 2 occurrence(s) of 'CD', which is not a declared code (EQ, FI, MF, OP) |

Nothing here has been written to the spec; a person reviews this delta and edits the spec file themselves.
