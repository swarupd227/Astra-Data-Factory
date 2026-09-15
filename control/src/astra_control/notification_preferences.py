"""Notification preferences: which alerts reach a person on which channel, and a per-custodian
noise floor ops can tune, so alerts are read, not muted (S6.3.13, ADR 0069, product spec's own
"Alert severity is configurable per custodian," S1.2.4 — this module adds the two dimensions that
story's own real, built alerting (`infra/terraform/foundation/alerting.tf`: `CONTROL.ALERTS`,
`CONTROL.ALERT_ROUTES`, real Slack/Jira/email delivery) does not have: a per-*person* channel
choice, and a per-custodian floor layered on top of a severity-routed broadcast).

**Severity is the config schema's own four-value alert vocabulary** (`info`, `warning`, `error`,
`critical` — `generation/src/astra_data/schemas/config-v0.schema.json`'s own `$defs.severity`,
already the type of a source config's own `alerts.late`/`alerts.task_failure`), not the domain
pack's own three-value rejection-code severity (`critical`/`error`/`warning`, no `info`) — the two
vocabularies look similar but are not the same closed set, and this module is about *alerts*, the
same thing S1.2.4's own severity field already names.

**Channels are this story's own three** — `slack`, `email`, `in_app` — not S1.2.4's own three
(`slack`, `jira`, `email`). Jira is not a personal preference dimension this story asks a person to
control (a Jira ticket is an operational artifact, not a notification a person opts into); `in_app`
has no delivery mechanism anywhere in this codebase — no inbox table, no rendered screen — the same
honest "not built yet" gap this whole plane has already named repeatedly for other missing
infrastructure. A preference for `in_app` is still real, storable data; nothing here claims to
deliver it.

**AC2's "severity thresholds per custodian" is a new, additive noise floor, not an edit to a
source config's own real `alerts:` block.** That block classifies which severity a *type* of
event (`late`, `task_failure`) is raised at — a different question from "below what severity
should nobody be bothered about this custodian at all." Editing the real `alerts:` block would
mean safely round-tripping a real, compiled source config, the same risk this plane already
declined for `domains/*/rejections.yaml` in S6.3.12 — this module owns its own small, full-state
settings file instead (`custodian_thresholds`, one row per custodian, no per-change history), the
same "current value, not an event log" shape `astra_control.board`'s own `wip_limits` already has.

**Both a user's own channel preference and this module never touches anything but its own
settings file** — no live Slack/email/in-app delivery happens here, the same "compose and show,
never actually send/run" boundary this whole plane has already drawn around every action no
environment here can perform live (S6.3.7 parity capture, S6.3.8 golden capture, S6.3.9 git,
S6.3.12 rejections.yaml).

**Setting one's own preferences is granted to every role, including auditor — the first write
action in this plane with no role gate at all.** S6.3.1's own "an auditor role can read everything
and change nothing" is about this plane's *shared, auditable factory state* (a rule's status, a
board position, an autonomy level) — a person's own notification channel choice changes nothing
about the factory; it is personal communication preference, not factory state, so `auditor`'s own
guarantee is not weakened by being able to set it. `set_custodian_threshold`, by contrast, changes
a shared setting affecting every user's own alerts for that custodian — AC2's own "for ops roles"
— granted to `Role.OPS` alone, the one role that name most literally names (no textual precedent
anywhere in this repository for a broader "ops roles" cluster — named plainly here rather than
guessed, the same honest-gap convention this file's own module docstring already established for
"QE engineer," "SRE," "security reviewer" and "architect").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from astra_core.yamlsource import SourceError, load

SEVERITIES = ("info", "warning", "error", "critical")  # config-v0.schema.json's own alert severity enum, exactly
CHANNELS = ("slack", "email", "in_app")  # this story's own three -- module docstring
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


class NotificationPreferencesError(RuntimeError):
    pass


def _severity_rank(value: str) -> int:
    if value not in _RANK:
        raise NotificationPreferencesError(f"'{value}' is not a severity; severities are {', '.join(SEVERITIES)}")
    return _RANK[value]


def _yaml_str(value: str) -> str:
    return json.dumps(value)


# -- the settings store: full state, one row per user and per custodian, rewritten on every save --


@dataclass
class NotificationSettings:
    users: dict[str, dict[str, str]] = field(default_factory=dict)  # email -> {channel: minimum_severity}
    custodian_thresholds: dict[str, str] = field(default_factory=dict)  # custodian_id -> minimum_severity

    def preferences_for(self, email: str) -> dict[str, str]:
        return dict(self.users.get(email, {}))

    def threshold_for(self, custodian_id: str) -> str:
        """No threshold ever set means no floor: everything from `info` up can reach someone."""
        return self.custodian_thresholds.get(custodian_id, "info")

    def to_dict(self) -> dict:
        return {"users": {e: dict(c) for e, c in self.users.items()}, "custodian_thresholds": dict(self.custodian_thresholds)}


def load_settings(path: Path | None) -> NotificationSettings:
    if path is None:
        return NotificationSettings()
    path = Path(path)
    if not path.is_file():
        return NotificationSettings()
    try:
        data = load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, SourceError) as exc:
        raise NotificationPreferencesError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise NotificationPreferencesError(f"{path}: expected a mapping")
    return NotificationSettings(
        users={str(email): {str(ch): str(sev) for ch, sev in (prefs or {}).items()} for email, prefs in (data.get("users") or {}).items()},
        custodian_thresholds={str(c): str(sev) for c, sev in (data.get("custodian_thresholds") or {}).items()},
    )


def save_settings(settings: NotificationSettings, path: Path) -> None:
    lines = ["# Notification preferences: current settings, rewritten on every save (no history).", "settings_version: 0", "", "users:"]
    if not settings.users:
        lines.append("  {}")
    for email in sorted(settings.users):
        prefs = settings.users[email]
        lines.append(f"  {_yaml_str(email)}:")
        if not prefs:
            lines.append("    {}")
        for channel in sorted(prefs):
            lines.append(f"    {channel}: {prefs[channel]}")
    lines.append("")
    lines.append("custodian_thresholds:")
    if not settings.custodian_thresholds:
        lines.append("  {}")
    for custodian_id in sorted(settings.custodian_thresholds):
        lines.append(f"  {custodian_id}: {settings.custodian_thresholds[custodian_id]}")
    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")


# -- AC1: per-user settings for Slack, email, in-app -- any user, their own record only ----------


def set_user_preferences(settings: NotificationSettings, *, email: str, channel_thresholds: dict[str, str]) -> NotificationSettings:
    """Replaces this one user's own preferences entirely -- a channel absent from
    `channel_thresholds` never reaches them; a channel present reaches them at that severity and
    above. Refused outright, the settings unchanged, on a blank email, an unknown channel or an
    unknown severity."""
    email = email.strip()
    if not email:
        raise NotificationPreferencesError("email must be given")
    for channel, severity in channel_thresholds.items():
        if channel not in CHANNELS:
            raise NotificationPreferencesError(f"'{channel}' is not a channel; channels are {', '.join(CHANNELS)}")
        _severity_rank(severity)
    users = dict(settings.users)
    users[email] = dict(channel_thresholds)
    return NotificationSettings(users=users, custodian_thresholds=dict(settings.custodian_thresholds))


# -- AC2: severity thresholds per custodian, for ops -----------------------------------------


def set_custodian_threshold(settings: NotificationSettings, *, custodian_id: str, minimum_severity: str) -> NotificationSettings:
    custodian_id = custodian_id.strip()
    if not custodian_id:
        raise NotificationPreferencesError("custodian_id must be given")
    _severity_rank(minimum_severity)
    thresholds = dict(settings.custodian_thresholds)
    thresholds[custodian_id] = minimum_severity
    return NotificationSettings(users=dict(settings.users), custodian_thresholds=thresholds)


# -- would a given alert actually reach this person, on this channel? ----------------------------


def reaches(settings: NotificationSettings, *, email: str, custodian_id: str, channel: str, severity: str) -> bool:
    """An alert reaches a person on a channel only when it clears both the custodian's own floor
    (AC2) and that person's own channel threshold (AC1) — the two knobs this story gives, combined."""
    alert_rank = _severity_rank(severity)
    if alert_rank < _severity_rank(settings.threshold_for(custodian_id)):
        return False
    prefs = settings.preferences_for(email)
    if channel not in prefs:
        return False
    return alert_rank >= _severity_rank(prefs[channel])


# -- the view --------------------------------------------------------------------------


def render_user_markdown(email: str, prefs: dict[str, str]) -> str:
    out = [f"# Notification preferences: {email}", ""]
    if not prefs:
        out += ["No channel reaches this user yet.", ""]
        return "\n".join(out)
    out.append("| Channel | Minimum severity |")
    out.append("|---|---|")
    for channel in CHANNELS:
        if channel in prefs:
            out.append(f"| {channel} | {prefs[channel]} |")
    out.append("")
    return "\n".join(out)


def render_thresholds_markdown(settings: NotificationSettings) -> str:
    out = ["# Custodian severity thresholds", ""]
    if not settings.custodian_thresholds:
        out += ["No threshold set for any custodian; every severity from `info` up can reach someone.", ""]
        return "\n".join(out)
    out.append("| Custodian | Minimum severity |")
    out.append("|---|---|")
    for custodian_id in sorted(settings.custodian_thresholds):
        out.append(f"| {custodian_id} | {settings.custodian_thresholds[custodian_id]} |")
    out.append("")
    return "\n".join(out)
