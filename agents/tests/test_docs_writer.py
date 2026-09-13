from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from astra_verification.chaos import SCENARIOS as REAL_CHAOS_SCENARIOS

from astra_agents.docs_writer import (
    CHAOS_REJECTION_CODE,
    CHAOS_SCENARIOS,
    DocsWriterError,
    chaos_scenarios_for,
    check_rendered,
    load_compiled_configs,
    render_markdown,
    rendered_files,
    resolution_rejection_codes,
    write_rendered,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"


def _configs():
    return load_compiled_configs([CONFIG], specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


def _config():
    return _configs()[0]


# ---------------------------------------------------------------- loading


def test_load_compiled_configs_loads_the_real_example():
    configs = _configs()
    assert len(configs) == 1
    assert configs[0].id == "pershing_position"


def test_load_compiled_configs_rejects_a_bad_specs_dir(tmp_path):
    with pytest.raises(DocsWriterError):
        load_compiled_configs([CONFIG], specs_dir=tmp_path, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- chaos scenarios stay in sync with the real drill


def test_chaos_scenarios_constant_matches_the_real_chaos_tool():
    assert CHAOS_SCENARIOS == REAL_CHAOS_SCENARIOS


def test_chaos_scenarios_for_returns_all_four_in_order():
    docs = chaos_scenarios_for(_config())
    assert tuple(d.scenario for d in docs) == CHAOS_SCENARIOS


def test_chaos_scenarios_for_malformed_reads_the_real_taxonomy_code():
    config = _config()
    docs = chaos_scenarios_for(config)
    malformed = next(d for d in docs if d.scenario == "malformed")
    rc = config.pack.rejections.code("RECORD_TYPE_UNKNOWN")
    assert malformed.code == "RECORD_TYPE_UNKNOWN"
    assert malformed.severity == rc.severity
    assert malformed.resolution == rc.resolution
    assert malformed.retryable is True


def test_chaos_scenarios_for_duplicate_reads_the_real_taxonomy_code():
    config = _config()
    docs = chaos_scenarios_for(config)
    duplicate = next(d for d in docs if d.scenario == "duplicate")
    rc = config.pack.rejections.code("MERGE_DUPLICATE_KEY")
    assert duplicate.code == "MERGE_DUPLICATE_KEY"
    assert duplicate.resolution == rc.resolution


def test_chaos_scenarios_for_late_has_no_code_and_is_not_retryable():
    late = next(d for d in chaos_scenarios_for(_config()) if d.scenario == "late")
    assert late.code is None
    assert late.alert_kind == "custodian_late_arrival"
    assert late.retryable is False


def test_chaos_scenarios_for_truncated_reads_the_real_control_total_rule():
    config = _config()
    truncated = next(d for d in chaos_scenarios_for(config) if d.scenario == "truncated")
    rule = next(r for r in config.dq_rules if r.kind == "control_total")
    assert truncated.code == rule.id == "trailer_control_total"
    assert truncated.severity == rule.severity
    assert truncated.detects == rule.check
    assert truncated.alert_kind == "chaos_scenario"


def test_chaos_scenarios_for_truncated_without_a_control_total_rule_says_so_instead_of_guessing():
    config = dataclasses.replace(_config(), dq_rules=())
    truncated = next(d for d in chaos_scenarios_for(config) if d.scenario == "truncated")
    assert truncated.code is None
    assert "no control_total dq_rule" in truncated.detects


def test_chaos_rejection_code_mapping_is_only_malformed_and_duplicate():
    assert set(CHAOS_REJECTION_CODE) == {"malformed", "duplicate"}


# ---------------------------------------------------------------- exceptions this source can raise


def test_resolution_rejection_codes_matches_pershing_positions_own_resolution_block():
    codes = {c.code for c in resolution_rejection_codes(_config())}
    assert "ACCOUNT_NOT_FOUND" in codes  # resolution.account is set
    assert "SECURITY_NOT_FOUND" in codes  # resolution.security is set
    assert "PRICE_MISSING" in codes  # resolution.price is set
    assert "TRANSACTION_CODE_UNMAPPED" not in codes  # resolution.transaction_code is not set


def test_resolution_rejection_codes_are_all_active():
    assert all(c.active for c in resolution_rejection_codes(_config()))


# ---------------------------------------------------------------- the doc


def test_render_markdown_includes_delivery_and_alerts():
    text = render_markdown(_config())
    assert "06:00" in text and "America/New_York" in text
    assert "late" in text and "task_failure" in text


def test_render_markdown_includes_every_chaos_scenario_and_its_resolution():
    config = _config()
    text = render_markdown(config)
    for scenario in CHAOS_SCENARIOS:
        assert scenario in text
    assert config.pack.rejections.code("RECORD_TYPE_UNKNOWN").resolution in text
    assert config.pack.rejections.code("MERGE_DUPLICATE_KEY").resolution in text


def test_render_markdown_includes_dq_rules_and_rule_catalog_entries():
    text = render_markdown(_config())
    assert "trailer_control_total" in text
    assert "pershing_gcus.quantity_sign" in text


def test_render_markdown_has_no_placeholder_text():
    text = render_markdown(_config())
    assert "TODO" not in text and "TBD" not in text


# ---------------------------------------------------------------- render and check, like astra_knowledge.cdm


def test_write_rendered_writes_one_file_named_after_the_release_bundle(tmp_path):
    written = write_rendered(_configs(), tmp_path)
    assert len(written) == 1
    assert written[0].name == "pershing-position.md"
    assert written[0].read_text(encoding="utf-8") == render_markdown(_config())


def test_check_rendered_is_missing_before_the_first_write(tmp_path):
    problems = check_rendered(_configs(), tmp_path, repo_root=REPO)
    assert len(problems) == 1
    assert "not rendered" in problems[0].message


def test_check_rendered_is_clean_right_after_write_rendered(tmp_path):
    configs = _configs()
    write_rendered(configs, tmp_path)
    assert check_rendered(configs, tmp_path, repo_root=REPO) == []


def test_check_rendered_flags_a_doc_that_went_stale(tmp_path):
    configs = _configs()
    write_rendered(configs, tmp_path)
    (tmp_path / "pershing-position.md").write_text("stale content", encoding="utf-8")
    problems = check_rendered(configs, tmp_path, repo_root=REPO)
    assert len(problems) == 1 and "stale" in problems[0].message


def test_write_rendered_removes_a_doc_no_longer_produced(tmp_path):
    configs = _configs()
    write_rendered(configs, tmp_path)
    write_rendered((), tmp_path)
    assert list(tmp_path.glob("*.md")) == []


def test_check_rendered_flags_a_doc_no_longer_produced(tmp_path):
    configs = _configs()
    write_rendered(configs, tmp_path)
    problems = check_rendered((), tmp_path, repo_root=REPO)
    assert len(problems) == 1 and "no longer produced" in problems[0].message


def test_rendered_files_is_idempotent():
    a = rendered_files(_configs())
    b = rendered_files(_configs())
    assert a == b


# ---------------------------------------------------------------- CLI


def test_cli_render_writes_docs_and_exits_zero(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing-position.md").exists()


def test_cli_check_is_clean_after_a_render(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out)])
    code = cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out), "--check"])
    assert code == 0, capsys.readouterr()


def test_cli_check_fails_when_a_doc_has_gone_stale(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out)])
    (out / "pershing-position.md").write_text("edited by hand", encoding="utf-8")
    code = cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out), "--check"])
    assert code == 1


def test_cli_check_fails_before_any_render(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS), "--root", str(REPO), "--out", str(out), "--check"])
    assert code == 1
    text = capsys.readouterr().out
    assert "not rendered" in text


def test_cli_render_start_error_exits_two(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["docs-writer", "render", str(CONFIG), "--specs", str(tmp_path / "missing"), "--rules", str(RULES), "--domains", str(DOMAINS), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """S5.12.1: the runbook covers every real chaos scenario with source-grounded detection and
    resolution text (usable in the chaos drill), and the render/check pair proves regeneration
    is deterministic and verifiable on every release."""
    configs = _configs()

    # AC1: a person running the chaos drill against this source finds, for every real scenario
    # the drill actually runs, what it detects and how to resolve it — grounded in the same
    # taxonomy and dq_rule data the drill itself reads, not invented prose.
    docs = chaos_scenarios_for(configs[0])
    assert tuple(d.scenario for d in docs) == REAL_CHAOS_SCENARIOS
    assert all(d.resolution for d in docs)

    # AC2: docs regenerate on every release — write_rendered/check_rendered is the same
    # render-and-check pair astra_knowledge.cdm already uses in CI on every pull request; a
    # fresh render is clean, and a change to the config would change the render (proven by
    # write_rendered's own output being exactly what check_rendered compares against).
    write_rendered(configs, tmp_path)
    assert check_rendered(configs, tmp_path, repo_root=REPO) == []
    changed = dataclasses.replace(configs[0], dq_rules=())
    assert render_markdown(changed) != render_markdown(configs[0])
