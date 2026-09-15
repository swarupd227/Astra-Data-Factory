# ADR 0060: Shifted is checked before changed, and fields are matched by name, not position

Date: 2026-09-15
Status: Accepted
Story: S6.3.4 Spec registry viewer: build and evaluate (E6, F6.3, WBS 2.6.16)

## Context

AC1 ("field list with position, type, citation link") is almost entirely a presentation of data
that already exists: every `Field` in `astra_knowledge.registry` already carries a `position`, a
`type` and a `citation` — the Spec Reader (S5.1.1) has put a page citation on every field it
drafts since this plane's first agent existed. AC2 ("version compare highlights added, removed
and shifted fields") is the one genuinely new thing this story has to build: no utility anywhere
in this repository compares two versions of the same `SourceSpec`. `astra_knowledge.cdm.diff`
compares two canonical-model versions; `astra_verification.replay.config_diff` compares two
compiled configs. Neither reads a spec.

## Decision

1. **Fields are matched between versions by `(record label, field name)`, never by position.**
   Matching by position would make a shift indistinguishable from an unrelated field replacing
   another at the same offset; matching by name is what actually lets "shifted" mean something —
   the same field, moved. `SourceSpec.fields()` already returns every field flattened across
   every record as `(Record, Field)` pairs, so a whole record added or removed needs no special
   case: every one of its own fields simply has no match on the other side, and shows added or
   removed on its own.

2. **A dedicated `shifted` kind, checked before the generic `changed` bucket — because AC2 names
   it specifically, and because it is the one spec change that can break a downstream mapping
   without anything else about the field ever appearing to change.** A field present under the
   same name in both versions, with a different `position` (`start`/`length`), is `shifted`
   regardless of whether its `type`, `codes` or `required` flag also happens to differ — position
   is checked first, and a field that has moved is never also reported as merely `changed`. Proven
   against real, already-committed drift, not a synthetic example: `specs/pershing_gcus`'s two
   real versions differ in exactly two ways — a new `lot_id` field (`added`) pushed `filler`'s own
   `start` from 67 to 79 (`shifted`) — found by running `compare` against the actual files, not
   constructed to demonstrate the feature.

3. **`changed` is the fallback bucket for everything real that is not a position shift** — type,
   declared codes, or the required flag differing while position stays put — the same "more
   granularity than the AC strictly names" shape `astra_verification.replay.config_diff` already
   established for configs (its own `resolution:` bucket goes beyond "mappings and rules" too).
   Nothing in the real `pershing_gcus` pair exercises this path; a dedicated test constructs one
   directly with `dataclasses.replace` on the real spec object, the same technique this whole
   session has used whenever a real fixture does not happen to cover every branch.

4. **`citation_link` is written fresh for this module, not reused from `astra_control.diff_
   review`.** `astra_knowledge.registry.Citation` (`page`, `line`, `document`) and `astra_
   knowledge.rules.Citation` (`kind`, plus `spec_id`/`spec_version`/`page`/`line`/`file`/
   `repository`/`document`) are two different dataclasses for two different kinds of citation — a
   spec field's own citation is always "a page in this layout document," never a choice between a
   spec page and a line of legacy code the way a rule's citation is. Importing the wrong one would
   not even type-check; writing this module's own small, matching version is more honest than
   forcing a shape that does not fit.

## Consequences

- `spec-viewer show`/`compare` are retrofitted with the same optional `--role` shape every prior
  Control-plane command has (ADR 0057), as the eleventh and twelfth actions in `astra_control.
  permissions` — both reads, available to every role, no change to any of the ten existing
  actions or their own tests.
- No rendered spec-browser or diff-highlight screen exists after this story — the same honest gap
  every prior Control-plane ADR has already named for its own piece; this is the tested field list
  and version-compare logic a screen would be built on top of.
