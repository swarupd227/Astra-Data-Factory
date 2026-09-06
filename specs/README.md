# specs

The spec registry: every custodian file layout as a machine-readable Source Spec, versioned, in Git (product spec Section 4). Story S2.1.1.

## Layout

```
specs/
  <spec id>/
    <version>.yaml      # one file per version; the file name is the version
```

A spec id names a layout, not a custodian: every custodian that delivers the same layout reuses the same spec. Each version carries the business date it comes into force. Two versions of one spec coexist as two files; the registry answers which is in force for a custodian and file type on a given date.

## A spec

```yaml
spec_version: 0
spec:
  id: pershing_gcus              # directory name
  version: "2017-07-25"          # file name
  effective_from: 2017-07-25     # in force from this business date
  file_type: position
  custodians: [pershing]         # who delivers files on this layout
  family: pershing_gcus          # optional, assigned by the Pattern Matcher
  provider: Pershing LLC
document:
  title: GCUS Position File Layout
  reference: GCUS_20170725.pdf   # what every citation points into
file:
  format: fixed_width            # or delimited
  record_length: 120
records:
  - type: header                 # header, detail or trailer; several details need names and match rules
    match: { position: { start: 1, length: 3 }, value: HDR }
    fields:
      - name: file_date
        position: { start: 4, length: 8 }   # 1-based; delimited files use `column`
        picture: 9(8)                        # COBOL-style picture from the document
        type: date                           # derived from the picture when omitted
        format: YYYYMMDD
        citation: { page: 4, line: 5 }       # page and/or line in the document
        codes: [{ value: R, meaning: full refresh }]
```

Every field carries its position (or column), its picture or type, and a citation. The picture is authoritative for width and numeric shape: `9(13)V9(5)` is eighteen positions, thirteen digits and five implied decimals; a sign in a separate field is named by `sign_field`.

## Commands

```bash
astra-spec validate                                           # every version, plus registry-wide checks
astra-spec list
astra-spec resolve --custodian pershing --file-type position --date 2026-09-06
astra-spec show --id pershing_gcus --version 2026-01-01
astra-spec search --custodian schwab --family pershing_gcus --file-type position   # exact custodian first, then family
astra-spec unclassified                                                            # specs with no family yet
```

Validation reports every problem with file, line and a plain sentence: a field beyond the record length, two fields overlapping, a picture that does not fit the position, a type that does not fit the picture, a date without a format, a code listed twice, a version whose file name does not match, or two versions in force for the same custodian on the same date. CI validates the registry on every pull request and checks that every config's `spec` reference points at a version that exists, lists the config's custodian, and describes the config's file type.
