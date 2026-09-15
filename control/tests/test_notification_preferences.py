from __future__ import annotations

from pathlib import Path

import pytest

from astra_control.notification_preferences import (
    CHANNELS,
    SEVERITIES,
    NotificationPreferencesError,
    NotificationSettings,
    load_settings,
    reaches,
    render_thresholds_markdown,
    render_user_markdown,
    save_settings,
    set_custodian_threshold,
    set_user_preferences,
)

EMAIL = "steward@example.com"


# ---------------------------------------------------------------- the settings store: load/save round trip


def test_load_settings_with_no_path_is_empty():
    settings = load_settings(None)
    assert settings.users == {} and settings.custodian_thresholds == {}


def test_load_settings_missing_file_is_empty(tmp_path):
    settings = load_settings(tmp_path / "missing.yaml")
    assert settings.users == {} and settings.custodian_thresholds == {}


def test_save_and_load_round_trips(tmp_path):
    path = tmp_path / "notifications.yaml"
    settings = NotificationSettings(users={EMAIL: {"slack": "warning", "email": "critical"}}, custodian_thresholds={"pershing": "warning"})
    save_settings(settings, path)
    reloaded = load_settings(path)
    assert reloaded.users == settings.users
    assert reloaded.custodian_thresholds == settings.custodian_thresholds


def test_save_settings_with_nothing_set_still_writes_a_valid_file(tmp_path):
    path = tmp_path / "notifications.yaml"
    save_settings(NotificationSettings(), path)
    reloaded = load_settings(path)
    assert reloaded.users == {} and reloaded.custodian_thresholds == {}


def test_load_settings_rejects_a_non_mapping_file(tmp_path):
    path = tmp_path / "notifications.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(NotificationPreferencesError, match="mapping"):
        load_settings(path)


# ---------------------------------------------------------------- AC1: per-user settings, any user, their own record


def test_set_user_preferences_is_a_full_replacement():
    settings = NotificationSettings(users={EMAIL: {"slack": "info"}})
    updated = set_user_preferences(settings, email=EMAIL, channel_thresholds={"email": "critical"})
    assert updated.preferences_for(EMAIL) == {"email": "critical"}  # slack is gone, not merged


def test_set_user_preferences_does_not_affect_other_users():
    settings = NotificationSettings(users={"other@example.com": {"slack": "info"}})
    updated = set_user_preferences(settings, email=EMAIL, channel_thresholds={"email": "critical"})
    assert updated.preferences_for("other@example.com") == {"slack": "info"}
    assert updated.preferences_for(EMAIL) == {"email": "critical"}


def test_set_user_preferences_every_channel():
    updated = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={c: "info" for c in CHANNELS})
    assert set(updated.preferences_for(EMAIL)) == set(CHANNELS)


def test_set_user_preferences_refuses_blank_email():
    with pytest.raises(NotificationPreferencesError, match="email"):
        set_user_preferences(NotificationSettings(), email="  ", channel_thresholds={})


def test_set_user_preferences_refuses_an_unknown_channel():
    with pytest.raises(NotificationPreferencesError, match="not a channel"):
        set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"sms": "info"})


def test_set_user_preferences_refuses_an_unknown_severity():
    with pytest.raises(NotificationPreferencesError, match="not a severity"):
        set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"slack": "urgent"})


def test_set_user_preferences_to_no_channels_means_nothing_reaches_them():
    updated = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={})
    assert updated.preferences_for(EMAIL) == {}


def test_preferences_for_unknown_user_is_empty():
    assert NotificationSettings().preferences_for("nobody@example.com") == {}


# ---------------------------------------------------------------- AC2: severity thresholds per custodian


def test_set_custodian_threshold():
    updated = set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="warning")
    assert updated.threshold_for("pershing") == "warning"


def test_threshold_for_a_custodian_with_no_setting_defaults_to_info():
    assert NotificationSettings().threshold_for("pershing") == "info"


def test_set_custodian_threshold_refuses_blank_custodian():
    with pytest.raises(NotificationPreferencesError, match="custodian_id"):
        set_custodian_threshold(NotificationSettings(), custodian_id=" ", minimum_severity="warning")


def test_set_custodian_threshold_refuses_an_unknown_severity():
    with pytest.raises(NotificationPreferencesError, match="not a severity"):
        set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="urgent")


def test_set_custodian_threshold_overwrites_the_previous_value():
    settings = set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="info")
    updated = set_custodian_threshold(settings, custodian_id="pershing", minimum_severity="critical")
    assert updated.threshold_for("pershing") == "critical"


# ---------------------------------------------------------------- reaches(): both knobs combined


def test_reaches_true_when_both_thresholds_are_cleared():
    settings = set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="warning")
    settings = set_user_preferences(settings, email=EMAIL, channel_thresholds={"slack": "warning"})
    assert reaches(settings, email=EMAIL, custodian_id="pershing", channel="slack", severity="error") is True


def test_reaches_false_below_the_custodian_floor():
    settings = set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="error")
    settings = set_user_preferences(settings, email=EMAIL, channel_thresholds={"slack": "info"})
    assert reaches(settings, email=EMAIL, custodian_id="pershing", channel="slack", severity="warning") is False


def test_reaches_false_below_the_user_s_own_channel_threshold():
    settings = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"slack": "critical"})
    assert reaches(settings, email=EMAIL, custodian_id="pershing", channel="slack", severity="error") is False


def test_reaches_false_when_the_channel_was_never_chosen():
    settings = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"email": "info"})
    assert reaches(settings, email=EMAIL, custodian_id="pershing", channel="slack", severity="critical") is False


def test_reaches_false_for_a_user_with_no_preferences_at_all():
    assert reaches(NotificationSettings(), email=EMAIL, custodian_id="pershing", channel="slack", severity="critical") is False


def test_reaches_with_no_custodian_threshold_set_only_the_user_s_own_choice_applies():
    settings = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"slack": "info"})
    assert reaches(settings, email=EMAIL, custodian_id="unconfigured_custodian", channel="slack", severity="info") is True


def test_reaches_exactly_at_the_threshold_is_true():
    settings = set_user_preferences(NotificationSettings(), email=EMAIL, channel_thresholds={"slack": "warning"})
    assert reaches(settings, email=EMAIL, custodian_id="pershing", channel="slack", severity="warning") is True


# ---------------------------------------------------------------- render_*


def test_render_user_markdown_shows_every_channel():
    text = render_user_markdown(EMAIL, {"slack": "warning", "email": "critical"})
    assert "slack" in text and "warning" in text and "email" in text and "critical" in text


def test_render_user_markdown_with_no_preferences_says_so():
    assert "No channel reaches this user yet." in render_user_markdown(EMAIL, {})


def test_render_thresholds_markdown_with_none_set_says_so():
    assert "No threshold set" in render_thresholds_markdown(NotificationSettings())


def test_render_thresholds_markdown_shows_every_custodian():
    settings = set_custodian_threshold(NotificationSettings(), custodian_id="pershing", minimum_severity="warning")
    text = render_thresholds_markdown(settings)
    assert "pershing" in text and "warning" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a user's own per-channel settings, saved and reloaded. AC2: a per-custodian severity
    threshold, layered on top so a below-floor alert reaches no one regardless of a user's own
    channel choice."""
    path = tmp_path / "notifications.yaml"
    settings = load_settings(path if path.is_file() else None)

    settings = set_user_preferences(settings, email=EMAIL, channel_thresholds={"slack": "warning", "email": "critical"})
    settings = set_custodian_threshold(settings, custodian_id="pershing", minimum_severity="error")
    save_settings(settings, path)

    reloaded = load_settings(path)
    assert reloaded.preferences_for(EMAIL) == {"slack": "warning", "email": "critical"}
    assert reloaded.threshold_for("pershing") == "error"

    # a warning-level alert never reaches this user for pershing -- the custodian's own floor is error
    assert reaches(reloaded, email=EMAIL, custodian_id="pershing", channel="slack", severity="warning") is False
    # an error-level alert does, on slack (warning threshold cleared) and on email (critical threshold not cleared)
    assert reaches(reloaded, email=EMAIL, custodian_id="pershing", channel="slack", severity="error") is True
    assert reaches(reloaded, email=EMAIL, custodian_id="pershing", channel="email", severity="error") is False
