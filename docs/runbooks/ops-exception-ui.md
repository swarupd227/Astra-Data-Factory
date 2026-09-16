# Runbook: working an exception group from suggestion to close

Story S6.2.5 (ADR 0074), closing feature F6.2. Exception Triage's own real suggestion groups,
grouped by cause, moved through `new -> suggested -> approved -> resubmitted -> closed`, plus an
ageing report by code.

## Showing the board

```bash
astra-control exception-review show \
  --board work/exception-review/board.yaml \
  --report work/exception-triage/pershing/report.json \
  --role ops
```

`--report` is optional; when given, every real suggestion group in that file not yet tracked is
synced onto the board **for this display only** — `show` never writes anything. A group whose
code has no taxonomy resolution yet shows as `new`; one Exception Triage already proposed a real
resolution for shows as `suggested`. A whitelisted, already-`auto_apply` group is never shown here
at all — it needs no operator.

## Working a group through its lifecycle

```bash
astra-control exception-review accept   --board work/exception-review/board.yaml --report work/exception-triage/pershing/report.json --code ACCOUNT_NOT_FOUND --group "value:ACC9999999" --by ops@example.com --role ops
astra-control exception-review edit     --board work/exception-review/board.yaml --code ACCOUNT_NOT_FOUND --group "value:ACC9999999" --resolution "Escalated for manual cross-reference add." --by ops@example.com --role ops
astra-control exception-review resubmit --board work/exception-review/board.yaml --code ACCOUNT_NOT_FOUND --group "value:ACC9999999" --by ops@example.com --role ops
astra-control exception-review close    --board work/exception-review/board.yaml --code ACCOUNT_NOT_FOUND --group "value:ACC9999999" --by ops@example.com --role ops
```

Each step is refused outright, nothing written, unless the group is exactly in the status the step
requires: `accept` needs `suggested` (a `new` group has no resolution yet — get a taxonomy entry
added and re-run Exception Triage first); `edit` needs `approved`; `resubmit` needs `approved`;
`close` needs `resubmitted`. `--code`/`--group` are the group's own `rejection_code`/`group_key`
from Exception Triage's own report; the first command against a not-yet-tracked group needs
`--report` to sync it in first.

## Ageing, by code

```bash
astra-control exception-review ageing \
  --exceptions work/exception-triage/pershing/exceptions.csv \
  --role pm
```

Reads the real per-exception `raised_at` timestamps from the same CSV Exception Triage itself
reads (still-open, `status: NEW` rows only), grouped by code, oldest-open age in days — this is
the only place a real per-exception timestamp exists anywhere in this codebase; it is not derived
from when a group was first synced onto this screen's own board.

## Deciding

- **`error: '<code>:<group>' is new; only a suggested exception group can be accepted — it has no
  taxonomy resolution yet`**: add a `resolution` for this code to the domain pack's own
  `rejections.yaml`, then re-run `astra-agents exception-triage run` and `sync` again.
- **`error: '<code>:<group>' is not tracked; sync() a real Exception Triage report first`**: pass
  `--report` on this call so the group is synced onto the board before the transition runs.
- **A group sits at `resubmitted` with no visible progress**: `resubmit` never actually re-runs
  anything — no environment here has a callable single-record resolution step. `close` it once a
  person has confirmed the batch's own next real resolution run actually cleared it; this screen
  does not verify that for you.
- **`--by` blank, or an edited `--resolution` blank**: both are refused outright, nothing written.

## Notes

- Tracked at suggestion-group granularity (a rejection code plus its root-cause `group_key`), the
  same identity Exception Triage's own report and `astra_control.queue` already use — not a real
  per-exception-id store, which is its own later story (the backlog's own S7.1.4).
- `exception-review show|ageing` are reads, available to every role; `accept|edit|resubmit|close`
  are granted to ops and BSA together, the same pairing `astra_control.queue`'s own
  `KIND_ROLES[QueueItemKind.EXCEPTION]` already anticipated.
