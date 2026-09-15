# ADR 0059: Config version is a date and a hash together, and every missing source is "no data," never a guess

Date: 2026-09-15
Status: Accepted
Story: S6.3.3 Custodian page: build and evaluate (E6, F6.3, WBS 2.6.15)

## Context

This story's own field list — family, tier, config version, current station, files today, parity
trend, open exceptions, cost — already has a real, already-built source for six of the eight:
`astra_data.compiler.compile_config`, `astra_control.board` (S6.1.1) and `astra_control.queue`
(S6.3.2) between them. The two genuinely open questions are what "config version" means (this
schema has no version number) and what to show for the two fields — files-arrival status and
cost — that have no live data source anywhere in this repository at all.

## Decision

1. **Config version is `effective_from` plus the config's own content hash, shown together, not
   a version number this schema does not have.** `astra_knowledge`'s spec registry already dates
   its own versions (`specs/<id>/<version>.yaml`, the version itself a date); `effective_from` is
   the config schema's own equivalent field, so it is the human-readable half. The provenance
   `sha256` (`CompiledConfig.provenance["config"]["sha256"]`, already computed by the compiler,
   never recomputed here) is the precise half — "which exact bytes are in force," useful the
   moment a person actually runs a config diff against this page's own `config_diff` hint.

2. **Current station is read straight off the board — never a second tracker.** `astra_control.
   custodian_page.build` takes an optional `board_path`, loads it with `astra_control.board.
   load_board`, and reads `Board.card(custodian_id).station` directly. A custodian not on the
   given board (or no board given at all) shows `station: None`, honestly, not a fabricated
   default station.

3. **Open exceptions reuses `astra_control.queue.exceptions_from` exactly — not a second
   definition of "open."** The count on this page and the count a person's own queue (S6.3.2)
   shows for the same report are, by construction, the same number; a test proves this directly
   rather than trusting two independent implementations to agree.

4. **Files today needs a live file-load-log query no environment here has — `arrivals` is a
   real, working stand-in, the same shape `board.yaml` already is for Postgres.** A caller passes
   a mapping from a delivery pattern to a real arrival time; a pattern with no entry shows "not
   yet," never inferred, never defaulted to "arrived." `control/examples/arrivals.yaml` is
   explicitly labeled illustrative — no live ingestion pipeline populates it today.

5. **Cost has no live source at all, and this module does not invent one.** The product spec's
   own agent table names a FinOps agent ("query tags, warehouse metrics" → "cost per source per
   day"), but it was never built as part of this repository's E5 backlog. `cost` is an optional
   caller-supplied number; `None` renders as "no data — the FinOps agent is not built yet," not a
   plausible-looking placeholder dollar figure. The same honesty this whole plane has practiced
   since ADR 0054 (`board.yaml`'s own Postgres stand-in) and ADR 0058 (`queue`'s own "no
   assignment field exists") applies to a number as much as it applies to a missing report.

6. **AC2's four links are real references where a real one exists, an honest note where it does
   not.** `spec` and `config` are real file paths that exist in this repository today. `parity_
   viewer` and `exceptions` point at whatever real report the caller gave — genuine, inspectable
   data — paired with a note that the *viewer* screen itself (S6.3.7, S6.2.5) is not built yet;
   neither field is ever a link to a screen this repository does not have. `config_diff` is a
   runnable command, not a static link — `astra_control.diff_review` needs two config versions to
   compare, so this page cannot point at a single target the way the other three links do; it
   shows the actual command a person would run, naming this config as the `--new` side.

7. **AC1 ("loads in under two seconds") is tested as this module's own aggregation latency, not a
   rendered page's full load time.** `test_build_completes_well_under_the_two_second_budget`
   times a real `build()` call against every real fixture this story has (one config compile, one
   parity report, one exception report, one arrivals file) and asserts it finishes in a small
   fraction of the budget — a genuine, if partial, proxy. The browser-rendered page's own load
   time needs the real React screen (later Control-plane work) to verify definitively; this test
   proves the backend half does not squander the budget before a screen even exists.

## Consequences

- `custodian-page show` is retrofitted into `astra_control.permissions` as a tenth action,
  `custodian-page.show`, a read every role may take — the same additive, backward-compatible
  `--role` shape S6.3.1 established; adding it required no change to any of the nine existing
  actions or their own tests.
- No rendered custodian-page screen exists after this story — the same honest gap every prior
  Control-plane ADR has already named for its own piece; this is the tested aggregation and CLI a
  screen would be built on top of.
