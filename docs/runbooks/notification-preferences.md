# Runbook: set who hears which alert, and where

Story S6.3.13 (ADR 0069). Which alerts reach a person on which channel, and a per-custodian
severity floor ops can tune on top — so alerts are read, not muted.

## Setting your own preferences

```bash
astra-control notification-preferences set \
  --settings work/notifications.yaml \
  --user steward@example.com \
  --channel slack:warning --channel email:critical
```

`--channel` is repeatable, each a `channel:severity` pair — channels are `slack`, `email`,
`in_app`; severities are `info`, `warning`, `error`, `critical` (the same four values a source
config's own `alerts:` block already uses). This **replaces** the user's own preferences
entirely — a channel left out never reaches them. Any role may set their own, including auditor:
it changes nothing about shared factory state, only a personal setting.

```bash
astra-control notification-preferences show --settings work/notifications.yaml --user steward@example.com
```

## Setting a custodian's own severity floor (ops)

```bash
astra-control notification-preferences set-threshold \
  --settings work/notifications.yaml \
  --custodian pershing --severity error \
  --role ops
```

Below this severity, nothing about this custodian reaches anyone, regardless of any individual's
own channel choice — a noise floor for a chatty or low-priority custodian. Granted to ops alone.

```bash
astra-control notification-preferences show-thresholds --settings work/notifications.yaml
```

## Checking whether an alert would actually reach someone

```bash
astra-control notification-preferences reaches \
  --settings work/notifications.yaml \
  --user steward@example.com --custodian pershing --channel slack --severity warning
```

Prints `yes`/`no` and exits 1 on `no` — an alert reaches a person on a channel only when it
clears **both** the custodian's own floor and that person's own channel threshold.

## Deciding

- **`reaches` says no, but the user's own settings look right**: check the custodian's own
  threshold with `show-thresholds` — a floor set above the alert's severity blocks it for
  everyone, no matter what any individual chose.
- **A channel never shows in `show`**: the user never set it, or it was replaced by a later `set`
  call that left it out — `set` is a full replacement, not a merge.
- **`in_app` preferences are accepted but nothing is delivered there**: honest — no in-app
  notification mechanism exists anywhere in this repository yet; the preference is still real,
  storable data for whenever one is built.

## Notes

- This is the first per-*user* settings store in this Control plane — everything else here is
  keyed by custodian, role, or change event.
- `notification-preferences show|set|show-thresholds|reaches` take the same optional `--role`
  every command in this plane does; `show`/`show-thresholds`/`reaches` are reads, available to
  every role. `set` is available to every role including auditor (ADR 0069). `set-threshold` is
  granted to ops alone.
