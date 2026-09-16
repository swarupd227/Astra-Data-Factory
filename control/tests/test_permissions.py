from __future__ import annotations

from pathlib import Path

import pytest

from astra_control.permissions import (
    PERMISSIONS,
    READ_ACTIONS,
    UNIVERSAL_WRITE_ACTIONS,
    WRITE_ACTIONS,
    Action,
    AuthorizationError,
    Role,
    RoleMapping,
    authorized,
    identity_from_claims,
    load_role_mapping,
    render_permissions,
    require,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE_MAPPING = REPO / "control" / "examples" / "role-mapping.yaml"


# ---------------------------------------------------------------- the closed vocabularies


def test_six_roles_the_backlogs_own_list():
    assert {r.value for r in Role} == {"steward", "bsa", "engineer", "ops", "pm", "auditor"}


def test_every_action_is_read_or_write_and_never_both():
    assert set(READ_ACTIONS) & set(WRITE_ACTIONS) == set()
    assert set(READ_ACTIONS) | set(WRITE_ACTIONS) == set(Action)


# ---------------------------------------------------------------- AC2: every role's actions are listed (PERMISSIONS itself) and enforced


def test_every_role_has_a_permissions_entry():
    assert set(PERMISSIONS) == set(Role)


def test_every_role_can_take_every_read_action():
    for role in Role:
        for action in READ_ACTIONS:
            assert authorized(role, action), f"{role} should be able to {action}"


def test_require_raises_for_a_disallowed_action():
    with pytest.raises(AuthorizationError, match="not allowed"):
        require(Role.AUDITOR, Action.BOARD_ADD)


def test_require_does_not_raise_for_an_allowed_action():
    require(Role.BSA, Action.CONFIG_STUDIO_START)  # no raise


def test_authorized_accepts_string_role_and_action():
    assert authorized("bsa", "config-studio.start") is True
    assert authorized("auditor", "board.add") is False


def test_an_invalid_role_string_is_a_clear_error():
    with pytest.raises(AuthorizationError, match="is not a role"):
        require("wizard", Action.BOARD_SHOW)


def test_an_invalid_action_string_is_a_clear_error():
    with pytest.raises(AuthorizationError, match="is not an action"):
        require(Role.OPS, "board.delete")


# ---------------------------------------------------------------- AC3: auditor reads everything, changes nothing


def test_auditor_has_zero_factory_state_write_actions():
    """S6.3.1's own "reads everything, changes nothing" is about this plane's shared factory
    state -- UNIVERSAL_WRITE_ACTIONS (S6.3.13's own notification-preferences.set) is a person's
    own preference, not factory state, and is deliberately excluded from this guarantee
    (permissions.py's own module docstring)."""
    assert PERMISSIONS[Role.AUDITOR] & (set(WRITE_ACTIONS) - UNIVERSAL_WRITE_ACTIONS) == set()


def test_auditor_has_every_read_action_plus_the_universal_writes():
    assert PERMISSIONS[Role.AUDITOR] == set(READ_ACTIONS) | UNIVERSAL_WRITE_ACTIONS


def test_auditor_is_refused_every_factory_state_write_action_individually():
    """Not just an aggregate set check -- every single write action that touches shared factory
    state, one at a time, actually raises when attempted as auditor (AC3, exhaustively) --
    excluding S6.3.13's own UNIVERSAL_WRITE_ACTIONS, which auditor is deliberately allowed."""
    for action in WRITE_ACTIONS:
        if action in UNIVERSAL_WRITE_ACTIONS:
            require(Role.AUDITOR, action)  # no raise -- allowed on purpose
            continue
        with pytest.raises(AuthorizationError):
            require(Role.AUDITOR, action)


def test_auditor_is_never_refused_a_read_action():
    for action in READ_ACTIONS:
        require(Role.AUDITOR, action)  # no raise


# ---------------------------------------------------------------- grounded, story-specific permissions


def test_bsa_can_run_the_self_service_config_studio_flow():
    for action in (Action.CONFIG_STUDIO_START, Action.CONFIG_STUDIO_ADVANCE, Action.CONFIG_STUDIO_REQUEST_PROMOTION):
        assert authorized(Role.BSA, action)


def test_pm_owns_the_wip_limit_the_delivery_leads_own_story_built():
    assert authorized(Role.PM, Action.BOARD_SET_WIP_LIMIT)
    assert not authorized(Role.BSA, Action.BOARD_SET_WIP_LIMIT)
    assert not authorized(Role.ENGINEER, Action.BOARD_SET_WIP_LIMIT)


def test_steward_owns_rule_review_agent_review_drift_review_and_approvals():
    """S6.1.3's own actor built only a read action; S6.3.5 gave steward its first writes
    (rule-review), S6.3.6 gave it agent-review's accept/reject, S6.3.9 gives it drift-review's
    approve, and S6.2.1 gives it the general approvals.approve/reject too -- steward is this
    plane's own busiest write role, story after story naming it as (joint) actor."""
    assert PERMISSIONS[Role.STEWARD] & (set(WRITE_ACTIONS) - UNIVERSAL_WRITE_ACTIONS) == {
        Action.RULE_REVIEW_SET_STATUS,
        Action.RULE_REVIEW_BULK_CONFIRM,
        Action.AGENT_REVIEW_ACCEPT,
        Action.AGENT_REVIEW_REJECT,
        Action.DRIFT_REVIEW_APPROVE,
        Action.APPROVALS_APPROVE,
        Action.APPROVALS_REJECT,
    }
    assert not authorized(Role.STEWARD, Action.BOARD_ADD)


def test_parity_viewer_adds_only_read_actions():
    """S6.3.7's own story adds four actions and no write -- every role already reads every one
    of them uniformly, so its "steward or QE engineer" actor (QE engineer is not one of the six
    closed roles) needed no new permission grant to resolve."""
    parity_actions = {Action.PARITY_VIEWER_TREND, Action.PARITY_VIEWER_BREAKS, Action.PARITY_VIEWER_RECORDS, Action.PARITY_VIEWER_RECORD}
    assert parity_actions <= set(READ_ACTIONS)
    assert parity_actions & set(WRITE_ACTIONS) == set()
    for action in parity_actions:
        for role in Role:
            assert authorized(role, action)


def test_run_status_adds_only_read_actions():
    """S6.3.8's own story adds two actions and no write -- "operations user or SRE" (SRE is not
    one of the six closed roles) needed no new permission grant to resolve either."""
    run_status_actions = {Action.RUN_STATUS_SHOW, Action.RUN_STATUS_DASHBOARD}
    assert run_status_actions <= set(READ_ACTIONS)
    assert run_status_actions & set(WRITE_ACTIONS) == set()
    for action in run_status_actions:
        for role in Role:
            assert authorized(role, action)


def test_throughput_metrics_adds_only_read_actions():
    """S6.2.3's own story adds two actions and no write -- assembling and exporting a report is
    a read, available to every role, even though its own actor (project manager) is a real role."""
    metrics_actions = {Action.THROUGHPUT_METRICS_SHOW, Action.THROUGHPUT_METRICS_EXPORT}
    assert metrics_actions <= set(READ_ACTIONS)
    assert metrics_actions & set(WRITE_ACTIONS) == set()
    for action in metrics_actions:
        for role in Role:
            assert authorized(role, action)


def test_gate_evidence_pack_adds_only_read_actions():
    """S6.2.4's own story adds two actions and no write -- assembling and exporting a PDF pack is
    a read, available to every role, the same shape S6.2.3's own report export already has, even
    though its own actor (project manager) is a real role."""
    pack_actions = {Action.GATE_EVIDENCE_PACK_SHOW, Action.GATE_EVIDENCE_PACK_EXPORT}
    assert pack_actions <= set(READ_ACTIONS)
    assert pack_actions & set(WRITE_ACTIONS) == set()
    for action in pack_actions:
        for role in Role:
            assert authorized(role, action)


def test_exception_review_show_and_ageing_are_read_actions():
    """S6.2.5's own story adds two reads -- seeing the board and the ageing report needs no
    special grant, the same shape every other viewer in this plane already has."""
    read_actions = {Action.EXCEPTION_REVIEW_SHOW, Action.EXCEPTION_REVIEW_AGEING}
    assert read_actions <= set(READ_ACTIONS)
    assert read_actions & set(WRITE_ACTIONS) == set()
    for action in read_actions:
        for role in Role:
            assert authorized(role, action)


def test_exception_review_writes_are_granted_to_ops_and_bsa_only():
    """S6.2.5's own actor -- "a reconciliation operator" -- is docs/ux/personas.md's own renaming
    of the product spec's "ops / business analyst" persona; astra_control.queue's own
    KIND_ROLES[QueueItemKind.EXCEPTION], written before this story, already anticipated both roles
    sharing this exact screen, the same shape S6.3.9's own DRIFT grant already established."""
    write_actions = {Action.EXCEPTION_REVIEW_ACCEPT, Action.EXCEPTION_REVIEW_EDIT, Action.EXCEPTION_REVIEW_RESUBMIT, Action.EXCEPTION_REVIEW_CLOSE}
    assert write_actions <= set(WRITE_ACTIONS)
    for action in write_actions:
        assert authorized(Role.OPS, action)
        assert authorized(Role.BSA, action)
        for role in (Role.STEWARD, Role.ENGINEER, Role.PM, Role.AUDITOR):
            assert not authorized(role, action)


def test_git_provenance_commit_is_granted_to_engineer_only():
    """S6.2.2's own actor is "a data engineer" -- Role.ENGINEER."""
    assert authorized(Role.ENGINEER, Action.GIT_PROVENANCE_COMMIT)
    for role in (Role.STEWARD, Role.BSA, Role.OPS, Role.PM, Role.AUDITOR):
        assert not authorized(role, Action.GIT_PROVENANCE_COMMIT)


def test_git_provenance_verify_is_available_to_every_role():
    assert Action.GIT_PROVENANCE_VERIFY in READ_ACTIONS
    for role in Role:
        assert authorized(role, Action.GIT_PROVENANCE_VERIFY)


def test_approvals_approve_and_reject_are_granted_to_steward_only():
    for action in (Action.APPROVALS_APPROVE, Action.APPROVALS_REJECT):
        assert authorized(Role.STEWARD, action)
        for role in (Role.BSA, Role.ENGINEER, Role.OPS, Role.PM, Role.AUDITOR):
            assert not authorized(role, action)


def test_approvals_show_is_available_to_every_role():
    assert Action.APPROVALS_SHOW in READ_ACTIONS
    for role in Role:
        assert authorized(role, Action.APPROVALS_SHOW)


def test_notification_preferences_set_is_granted_to_every_role():
    """S6.3.13's own first write with no role gate: a person's own notification preference, not
    factory state."""
    for role in Role:
        assert authorized(role, Action.NOTIFICATION_PREFERENCES_SET)


def test_notification_preferences_set_threshold_is_granted_to_ops_only():
    assert authorized(Role.OPS, Action.NOTIFICATION_PREFERENCES_SET_THRESHOLD)
    for role in (Role.STEWARD, Role.BSA, Role.ENGINEER, Role.PM, Role.AUDITOR):
        assert not authorized(role, Action.NOTIFICATION_PREFERENCES_SET_THRESHOLD)


def test_notification_preferences_reads_are_available_to_every_role():
    for action in (Action.NOTIFICATION_PREFERENCES_SHOW, Action.NOTIFICATION_PREFERENCES_SHOW_THRESHOLDS):
        assert action in READ_ACTIONS
        for role in Role:
            assert authorized(role, action)


def test_autonomy_admin_writes_are_granted_to_pm_only():
    """S6.3.12's own "architect" actor is not a role, but the product spec's own persona table
    attributes exactly this responsibility to the "Artizent delivery lead" -- Role.PM -- so both
    writes are granted there alone, the first non-role-name actor that actually forces a
    real grant decision (module docstring)."""
    for action in (Action.AUTONOMY_ADMIN_SET_LEVEL, Action.AUTONOMY_ADMIN_REQUEST_WHITELIST_CHANGE):
        assert authorized(Role.PM, action)
        for role in (Role.STEWARD, Role.BSA, Role.ENGINEER, Role.OPS, Role.AUDITOR):
            assert not authorized(role, action)


def test_autonomy_admin_reads_are_available_to_every_role():
    for action in (Action.AUTONOMY_ADMIN_SHOW_LEVELS, Action.AUTONOMY_ADMIN_SHOW_WHITELIST, Action.AUTONOMY_ADMIN_SHOW_WHITELIST_REQUESTS):
        assert action in READ_ACTIONS
        for role in Role:
            assert authorized(role, action)


def test_audit_log_adds_only_read_actions():
    """S6.3.11's own story adds two actions and no write -- auditor already reads everything and
    writes nothing (S6.3.1's own AC3); "security reviewer" (not one of the six closed roles)
    needed no new permission grant to resolve either."""
    audit_actions = {Action.AUDIT_LOG_SHOW, Action.AUDIT_LOG_EXPORT}
    assert audit_actions <= set(READ_ACTIONS)
    assert audit_actions & set(WRITE_ACTIONS) == set()
    for action in audit_actions:
        for role in Role:
            assert authorized(role, action)


def test_golden_viewer_adds_only_a_read_action():
    """S6.3.10's own story adds one action and no write -- "QE engineer" (not one of the six
    closed roles) needed no new permission grant to resolve, same as S6.3.7/S6.3.8."""
    assert Action.GOLDEN_VIEWER_SHOW in READ_ACTIONS
    assert Action.GOLDEN_VIEWER_SHOW not in WRITE_ACTIONS
    for role in Role:
        assert authorized(role, Action.GOLDEN_VIEWER_SHOW)


def test_drift_review_approve_is_granted_to_engineer_and_steward_only():
    """S6.3.9's own actor -- engineer or steward -- matching queue.py's own KIND_ROLES[DRIFT],
    written before this story to anticipate it."""
    assert authorized(Role.ENGINEER, Action.DRIFT_REVIEW_APPROVE)
    assert authorized(Role.STEWARD, Action.DRIFT_REVIEW_APPROVE)
    for role in (Role.BSA, Role.OPS, Role.PM, Role.AUDITOR):
        assert not authorized(role, Action.DRIFT_REVIEW_APPROVE)


def test_agent_review_accept_reject_are_granted_to_steward_and_bsa_only():
    """The one action set in this file granted to two roles at once (module docstring) --
    grounded in the story's own "steward or BSA" actor, not any other role."""
    for action in (Action.AGENT_REVIEW_ACCEPT, Action.AGENT_REVIEW_REJECT):
        assert authorized(Role.STEWARD, action)
        assert authorized(Role.BSA, action)
        assert not authorized(Role.ENGINEER, action)
        assert not authorized(Role.OPS, action)
        assert not authorized(Role.PM, action)
        assert not authorized(Role.AUDITOR, action)


# ---------------------------------------------------------------- identity: claims -> role


def test_load_role_mapping_reads_the_real_committed_example():
    mapping = load_role_mapping(EXAMPLE_MAPPING)
    assert mapping.by_group["Astra-BSA"] is Role.BSA
    assert mapping.by_group["Astra-Auditors"] is Role.AUDITOR


def test_load_role_mapping_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(AuthorizationError, match="not found"):
        load_role_mapping(tmp_path / "missing.yaml")


def test_identity_from_claims_resolves_a_single_matching_group():
    mapping = RoleMapping(by_group={"Astra-BSA": Role.BSA})
    identity = identity_from_claims({"email": "a@example.com", "name": "A", "groups": ["Astra-BSA"]}, mapping)
    assert identity.role is Role.BSA
    assert identity.can(Action.CONFIG_STUDIO_START) is True
    assert identity.can(Action.BOARD_SET_WIP_LIMIT) is False


def test_identity_from_claims_against_the_real_example_mapping():
    identity = identity_from_claims({"email": "steward@example.com", "name": "A Steward", "groups": ["Astra-Stewards"]}, load_role_mapping(EXAMPLE_MAPPING))
    assert identity.role is Role.STEWARD


def test_identity_from_claims_rejects_no_email():
    with pytest.raises(AuthorizationError, match="email"):
        identity_from_claims({"groups": ["Astra-BSA"]}, RoleMapping(by_group={"Astra-BSA": Role.BSA}))


def test_identity_from_claims_rejects_no_matching_group():
    with pytest.raises(AuthorizationError, match="none of"):
        identity_from_claims({"email": "a@example.com", "groups": ["Unmapped-Group"]}, RoleMapping(by_group={"Astra-BSA": Role.BSA}))


def test_identity_from_claims_rejects_two_groups_mapping_to_different_roles():
    mapping = RoleMapping(by_group={"Astra-BSA": Role.BSA, "Astra-Ops": Role.OPS})
    with pytest.raises(AuthorizationError, match="more than one role"):
        identity_from_claims({"email": "a@example.com", "groups": ["Astra-BSA", "Astra-Ops"]}, mapping)


def test_identity_from_claims_two_groups_mapping_to_the_same_role_is_fine():
    mapping = RoleMapping(by_group={"Astra-BSA": Role.BSA, "Astra-BSA-Alt": Role.BSA})
    identity = identity_from_claims({"email": "a@example.com", "groups": ["Astra-BSA", "Astra-BSA-Alt"]}, mapping)
    assert identity.role is Role.BSA


def test_identity_from_claims_defaults_name_to_email():
    mapping = RoleMapping(by_group={"Astra-BSA": Role.BSA})
    identity = identity_from_claims({"email": "a@example.com", "groups": ["Astra-BSA"]}, mapping)
    assert identity.name == "a@example.com"


# ---------------------------------------------------------------- render_permissions: AC2's own "listed"


def test_render_permissions_lists_every_role_and_action():
    text = render_permissions()
    for role in Role:
        assert f"## {role.value}" in text
    for action in Action:
        assert action.value in text


def test_render_permissions_for_one_role_only():
    text = render_permissions(Role.AUDITOR)
    assert "## auditor" in text
    assert "## bsa" not in text


def test_render_permissions_marks_write_actions_not_allowed_for_auditor():
    text = render_permissions(Role.AUDITOR)
    lines = [l for l in text.splitlines() if l.startswith("| board.add ")]
    assert lines and "no" in lines[0]


# ---------------------------------------------------------------- CLI: retrofit is additive, and enforces when given


def test_cli_board_add_without_role_is_unchanged(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial"])
    assert code == 0, capsys.readouterr()


def test_cli_board_add_as_auditor_is_refused(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial", "--role", "auditor"])
    assert code == 2
    assert "not allowed" in capsys.readouterr().err
    assert not board_path.exists()  # refused outright, nothing written


def test_cli_board_add_as_ops_succeeds(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial", "--role", "ops"])
    assert code == 0, capsys.readouterr()
    assert board_path.exists()


def test_cli_board_show_as_auditor_succeeds_reads_always_allowed(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial", "--role", "ops"])
    capsys.readouterr()
    code = cli.main(["board", "show", "--board", str(board_path), "--role", "auditor"])
    assert code == 0


def test_cli_config_studio_request_promotion_as_pm_is_refused(tmp_path, capsys):
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    requests_path = tmp_path / "requests.yaml"
    cli.main(["config-studio", "start", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial", "--by", "bsa@example.com"])
    cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "draft", "--by", "bsa@example.com"])
    cli.main(["config-studio", "advance", "--board", str(board_path), "--custodian", "pershing", "--to", "dry_run", "--by", "bsa@example.com"])
    capsys.readouterr()
    code = cli.main(["config-studio", "request-promotion", "--board", str(board_path), "--requests", str(requests_path), "--custodian", "pershing", "--tier", "simple", "--requested-by", "pm@example.com", "--role", "pm"])
    assert code == 2
    assert not requests_path.exists()


def test_cli_permissions_show(capsys):
    import astra_control.cli as cli

    code = cli.main(["permissions", "show"])
    assert code == 0
    text = capsys.readouterr().out
    assert "## auditor" in text and "board.add" in text


def test_cli_permissions_show_one_role(capsys):
    import astra_control.cli as cli

    code = cli.main(["permissions", "show", "--role", "bsa"])
    assert code == 0
    text = capsys.readouterr().out
    assert "## bsa" in text and "## engineer" not in text


def test_cli_permissions_show_invalid_role(capsys):
    import astra_control.cli as cli

    code = cli.main(["permissions", "show", "--role", "wizard"])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_permissions_resolve_against_the_real_example(capsys):
    import astra_control.cli as cli

    code = cli.main(["permissions", "resolve", "--mapping", str(EXAMPLE_MAPPING), "--email", "bsa@example.com", "--name", "A BSA", "--group", "Astra-BSA"])
    assert code == 0, capsys.readouterr()
    assert "bsa" in capsys.readouterr().out


def test_cli_permissions_resolve_with_no_matching_group(capsys):
    import astra_control.cli as cli

    code = cli.main(["permissions", "resolve", "--mapping", str(EXAMPLE_MAPPING), "--email", "x@example.com", "--name", "X", "--group", "Nobody"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S6.3.1: each role's allowed actions are listed (PERMISSIONS / render_permissions) and
    enforced server-side (require, and the CLI's own --role retrofit); the auditor role reads
    everything and changes nothing, exhaustively."""
    for role in Role:
        assert role in PERMISSIONS  # listed

    for action in READ_ACTIONS:
        require(Role.AUDITOR, action)  # auditor reads everything
    for action in WRITE_ACTIONS:
        if action in UNIVERSAL_WRITE_ACTIONS:
            continue  # S6.3.13's own notification-preferences.set: a person's own preference, not factory state
        with pytest.raises(AuthorizationError):
            require(Role.AUDITOR, action)  # auditor changes no shared factory state

    # enforced server-side: a real CLI call is actually refused, not just a library-level check
    import astra_control.cli as cli

    board_path = tmp_path / "board.yaml"
    code = cli.main(["board", "add", "--board", str(board_path), "--custodian", "pershing", "--stream", "envestnet-custodial", "--role", "auditor"])
    assert code == 2
    assert not board_path.exists()
