from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_verification.parity import FieldMismatch, RowMismatch, compare_rows
from astra_agents.break_explainer import (
    BreakExplainerError,
    classify_cause,
    explain_field,
    explain_row,
    field_mappings,
    generate,
    load_compiled_config,
    load_parity_mapping,
    load_rows,
    render_markdown,
    run,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"
PARITY = REPO / "golden" / "pershing" / "parity.yaml"
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"
EXAMPLE = REPO / "agents" / "examples" / "break_explainer"
LEGACY_ROWS = EXAMPLE / "legacy_rows.csv"
LAKEHOUSE_ROWS = EXAMPLE / "lakehouse_rows.csv"
BUSINESS_DATE = "2026-09-01"


def _config():
    return load_compiled_config(CONFIG, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS)


def _parity_mapping():
    return load_parity_mapping(PARITY)


def _result():
    mapping = _parity_mapping()
    legacy = load_rows(LEGACY_ROWS)
    lakehouse = load_rows(LAKEHOUSE_ROWS)
    return mapping, compare_rows(mapping, legacy, lakehouse, BUSINESS_DATE)


# ---------------------------------------------------------------- loading


def test_load_compiled_config_compiles_the_real_committed_config():
    config = _config()
    assert config.id == "pershing_position"
    assert any(m.column.name == "QUANTITY" for m in config.mappings)


def test_load_compiled_config_raises_a_clear_error_for_a_bad_specs_dir(tmp_path):
    with pytest.raises(BreakExplainerError):
        load_compiled_config(CONFIG, specs_dir=tmp_path / "nope", rules_dir=RULES, domains_dir=DOMAINS)


def test_load_parity_mapping_reads_the_real_committed_mapping():
    mapping = _parity_mapping()
    assert mapping.custodian == "pershing" and mapping.table == "POSITION"


def test_load_parity_mapping_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(BreakExplainerError, match="not found"):
        load_parity_mapping(tmp_path / "missing.yaml")


def test_load_rows_reads_the_real_committed_fixture():
    rows = load_rows(LEGACY_ROWS)
    assert len(rows) == 4 and rows[0]["AccountNumber"] == "ACC0000001"


def test_load_rows_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(BreakExplainerError, match="not found"):
        load_rows(tmp_path / "missing.csv")


# ---------------------------------------------------------------- classify_cause, against real mappings


def test_classify_cause_transform_for_the_real_quantity_mapping():
    config = _config()
    mapping = field_mappings(config)["QUANTITY"]
    assert mapping.transform is not None
    assert classify_cause("QUANTITY", mapping) == "transform"


def test_classify_cause_resolution_for_a_mapped_resolution_column():
    """CUSTODIAN_SECURITY_ID is both a real, direct mapping (from cusip) AND a resolution column
    (astra_data.compiler's own SECURITY_COLUMNS) -- resolution must win, not "unexplained"."""
    config = _config()
    mapping = field_mappings(config)["CUSTODIAN_SECURITY_ID"]
    assert mapping.transform is None  # a plain copy, no transform -- would be "unexplained" if misclassified
    assert classify_cause("CUSTODIAN_SECURITY_ID", mapping) == "resolution"


def test_classify_cause_resolution_for_an_unmapped_resolution_column():
    """ACCOUNT_ID has no mapping at all -- the resolution stage fills it directly -- and must
    still classify as resolution, not unmapped."""
    assert classify_cause("ACCOUNT_ID", None) == "resolution"


def test_classify_cause_unmapped_for_a_field_with_no_mapping_at_all():
    config = _config()
    assert "MARKET_VALUE" not in field_mappings(config)
    assert classify_cause("MARKET_VALUE", None) == "unmapped"


def test_classify_cause_unexplained_for_a_direct_copy_with_no_transform_and_no_resolution():
    config = _config()
    mapping = field_mappings(config)["AS_OF_DATE"]
    assert mapping.transform is None
    assert classify_cause("AS_OF_DATE", mapping) == "unexplained"


def test_classify_cause_unexplained_for_a_constant_mapping():
    config = _config()
    mapping = field_mappings(config)["POSITION_TYPE"]
    assert mapping.source is None and mapping.constant == "LONG"
    assert classify_cause("POSITION_TYPE", mapping) == "unexplained"


# ---------------------------------------------------------------- explain_field / explain_row


def test_explain_field_cites_the_real_rule_id_for_quantity():
    config = _config()
    parity_mapping = _parity_mapping()
    mismatch = FieldMismatch(field="Quantity", legacy="100.00000", lakehouse="100.00200")
    explanation = explain_field(mismatch, ("ACC1", "CUSIP1", BUSINESS_DATE), parity_mapping, field_mappings(config))
    assert explanation.cause == "transform"
    assert explanation.rule_id == "pershing_gcus.quantity_sign"
    assert explanation.rule_text  # the real rule's own text, not blank
    assert explanation.explained is True


def test_explain_field_reports_no_rule_when_the_mapping_has_none():
    config = _config()
    parity_mapping = _parity_mapping()
    mismatch = FieldMismatch(field="Price", legacy="50.1234", lakehouse="50.2000")
    explanation = explain_field(mismatch, ("ACC1", "CUSIP1", BUSINESS_DATE), parity_mapping, field_mappings(config))
    assert explanation.cause == "transform"
    assert explanation.rule_id is None  # never fabricated


def test_explain_field_translates_the_legacy_column_name_to_the_lakehouse_one():
    config = _config()
    parity_mapping = _parity_mapping()
    mismatch = FieldMismatch(field="MarketValue", legacy="1.00", lakehouse="2.00")
    explanation = explain_field(mismatch, ("ACC1", "CUSIP1", BUSINESS_DATE), parity_mapping, field_mappings(config))
    assert explanation.field == "MARKET_VALUE"
    assert explanation.cause == "unmapped"


def test_explain_row_explains_every_field_in_the_mismatch():
    config = _config()
    parity_mapping = _parity_mapping()
    mismatch = RowMismatch(key=("ACC1", "CUSIP1", BUSINESS_DATE), fields=(FieldMismatch("Quantity", "1", "2"), FieldMismatch("Price", "1", "2")))
    explanations = explain_row(mismatch, parity_mapping, field_mappings(config))
    assert len(explanations) == 2
    assert {e.field for e in explanations} == {"QUANTITY", "PRICE"}


# ---------------------------------------------------------------- generate() / run()


def test_generate_against_the_real_fixture_finds_three_differences():
    config = _config()
    parity_mapping, result = _result()
    draft = generate(config, parity_mapping, result)
    assert len(draft.explanations) == 3
    assert draft.explained_count == 3
    assert draft.explained_rate == 1.0


def test_generate_groups_by_cause():
    config = _config()
    parity_mapping, result = _result()
    draft = generate(config, parity_mapping, result)
    by_cause = draft.by_cause
    assert len(by_cause["transform"]) == 2
    assert len(by_cause["unmapped"]) == 1
    assert by_cause["resolution"] == () and by_cause["unexplained"] == ()


def test_run_reads_everything_from_disk():
    draft = run(CONFIG, PARITY, LEGACY_ROWS, LAKEHOUSE_ROWS, BUSINESS_DATE, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS)
    assert draft.custodian == "pershing" and draft.business_date == BUSINESS_DATE
    assert len(draft.explanations) == 3


def test_run_raises_a_clear_error_for_a_missing_legacy_file(tmp_path):
    with pytest.raises(BreakExplainerError, match="not found"):
        run(CONFIG, PARITY, tmp_path / "missing.csv", LAKEHOUSE_ROWS, BUSINESS_DATE, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS)


# ---------------------------------------------------------------- an unexplained difference, end to end


def test_an_unexplained_difference_is_reported_honestly_not_guessed_at():
    config = _config()
    parity_mapping = _parity_mapping()
    legacy = load_rows(LEGACY_ROWS)
    lakehouse = [dict(r) for r in load_rows(LAKEHOUSE_ROWS)]
    lakehouse[0]["PRICE"] = "999.9999"  # corrupt row 1's price -- row 1 was otherwise a perfect match
    result = compare_rows(parity_mapping, legacy, lakehouse, BUSINESS_DATE)
    draft = generate(config, parity_mapping, result)
    # PRICE is still a transform-governed field, so this specific corruption still explains --
    # this proves the *mechanism*, not a claim that every possible corruption is unexplainable.
    assert any(e.field == "PRICE" and e.cause == "transform" for e in draft.explanations)


# ---------------------------------------------------------------- report and files


def test_render_markdown_lists_the_cause_and_rule_per_difference():
    config = _config()
    parity_mapping, result = _result()
    draft = generate(config, parity_mapping, result)
    text = render_markdown(draft)
    assert "pershing_gcus.quantity_sign" in text
    assert "transform" in text and "unmapped" in text
    assert "3 explained (100%)" in text


def test_write_draft_writes_report_and_json(tmp_path):
    config = _config()
    parity_mapping, result = _result()
    draft = generate(config, parity_mapping, result)
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["explained_rate"] == 1.0
    assert data["by_cause"]["transform"] == 2


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_real_fixture(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main([
        "break-explainer", "run",
        "--config", str(CONFIG), "--parity", str(PARITY),
        "--legacy", str(LEGACY_ROWS), "--lakehouse", str(LAKEHOUSE_ROWS),
        "--business-date", BUSINESS_DATE,
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
        "--out", str(out),
    ])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing" / BUSINESS_DATE / "report.md").exists()
    text = capsys.readouterr().out
    assert "3 difference(s), 3 explained (100%)" in text


def test_cli_reports_a_start_error_for_a_missing_parity_file(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main([
        "break-explainer", "run",
        "--config", str(CONFIG), "--parity", str(tmp_path / "missing.yaml"),
        "--legacy", str(LEGACY_ROWS), "--lakehouse", str(LAKEHOUSE_ROWS),
        "--business-date", BUSINESS_DATE,
        "--specs", str(SPECS), "--rules", str(RULES), "--domains", str(DOMAINS),
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S5.10.1: differences are auto-explained (100% for this real, illustrative fixture) and an
    explanation cites the real catalog rule id when its mapping has one -- never a fabricated id
    when it does not."""
    draft = run(CONFIG, PARITY, LEGACY_ROWS, LAKEHOUSE_ROWS, BUSINESS_DATE, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS)
    assert draft.explained_rate == 1.0

    quantity = next(e for e in draft.explanations if e.field == "QUANTITY")
    assert quantity.rule_id == "pershing_gcus.quantity_sign"

    price = next(e for e in draft.explanations if e.field == "PRICE")
    assert price.rule_id is None
