from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_knowledge.picture import parse_picture
from astra_knowledge.registry import Citation, Field, LifecycleRule, MergeRule, Pairing, Record, Registry, SourceSpec
from astra_agents.pattern_matcher import (
    FAMILY_THRESHOLD,
    PatternMatcherError,
    assign_tier,
    best_match,
    classify,
    load_registry,
    render_markdown,
    run,
    similarity,
    tier_score,
    write_assignments,
)

REPO = Path(__file__).resolve().parents[2]
GOLD = REPO / "agents" / "pattern_matcher" / "gold" / "specs"


# ---------------------------------------------------------------- building specs without YAML


def _field(name: str, picture_text: str | None = None, type_: str = "string", **kw) -> Field:
    picture = parse_picture(picture_text) if picture_text else None
    return Field(name=name, citation=Citation(page=1), type=type_, picture=picture, position=(1, 1), **kw)


def _detail(fields: list[Field], name: str | None = None) -> Record:
    return Record(type="detail", fields=tuple(fields), name=name)


def _spec(
    id_: str,
    version: str = "2026-01-01",
    *,
    family: str | None = None,
    file_type: str = "position",
    fmt: str = "fixed_width",
    details: list[Record],
    size: int = 100,
    merge: MergeRule | None = None,
    pairings: tuple[Pairing, ...] = (),
    splits: tuple = (),
    lifecycle: LifecycleRule | None = None,
) -> SourceSpec:
    header = Record(type="header", fields=(_field("record_type", "X(3)", "code"),))
    trailer = Record(type="trailer", fields=(_field("record_type", "X(3)", "code"),))
    file = {"format": fmt, "record_length": size} if fmt == "fixed_width" else {"format": fmt, "column_count": size}
    return SourceSpec(
        id=id_,
        version=version,
        effective_from=date(2026, 1, 1),
        file_type=file_type,
        custodians=("demo",),
        records=(header, *details, trailer),
        document={},
        file=file,
        path=Path(f"specs/{id_}/{version}.yaml"),
        family=family,
        merge=merge,
        pairings=pairings,
        splits=splits,
        lifecycle=lifecycle,
    )


# A single-detail-record "position" layout: account, cusip, signed quantity.
def _position_detail(extra: list[Field] = ()) -> Record:
    return _detail(
        [
            _field("record_type", "X(3)", "code"),
            _field("account_number", "X(10)"),
            _field("cusip", "X(9)"),
            _field("quantity", "9(13)V9(5)", "decimal", sign_field="quantity_sign"),
            _field("quantity_sign", "X(1)", "code"),
            *extra,
        ]
    )


# ---------------------------------------------------------------- similarity


def test_lcs_and_record_similarity_of_identical_sequences_is_perfect():
    from astra_agents.pattern_matcher import _lcs_len, _record_similarity

    seq = (("alphanumeric", "code"), ("numeric", "decimal"))
    assert _lcs_len(seq, seq) == 2
    assert _record_similarity(seq, seq) == 1.0


def test_record_similarity_is_zero_when_one_side_has_no_fields():
    from astra_agents.pattern_matcher import _record_similarity

    assert _record_similarity((), ()) == 1.0
    assert _record_similarity((("numeric", "integer"),), ()) == 0.0


def test_similarity_is_perfect_for_the_same_shape_with_a_different_name():
    a = _spec("a", details=[_position_detail()])
    b = _spec("b", details=[_position_detail()])
    assert similarity(a, b) == 1.0


def test_similarity_tolerates_one_extra_field_the_way_a_later_spec_version_would():
    a = _spec("a", details=[_position_detail()])
    b = _spec("a", version="2026-02-01", details=[_position_detail(extra=[_field("lot_id", "X(12)")])])
    assert 0.85 < similarity(a, b) < 1.0


def test_similarity_is_zero_across_formats():
    fixed = _spec("a", details=[_position_detail()])
    delimited = _spec("b", fmt="delimited", details=[_detail([_field("x", type_="string")])])
    assert similarity(fixed, delimited) == 0.0


def test_similarity_is_zero_across_file_types():
    position = _spec("a", file_type="position", details=[_position_detail()])
    transaction = _spec("b", file_type="transaction", details=[_position_detail()])
    assert similarity(position, transaction) == 0.0


def test_similarity_is_penalized_when_detail_record_counts_differ():
    one_detail = _spec("a", details=[_position_detail()])
    two_details = _spec(
        "b",
        details=[
            _detail([_field("record_type", "X(1)", "code"), _field("account_number", "X(10)"), _field("cusip", "X(9)"), _field("quantity", "9(10)V9(5)", "decimal")], name="holding"),
            _detail([_field("record_type", "X(1)", "code"), _field("account_number", "X(10)"), _field("cusip", "X(9)"), _field("price", "9(7)V9(6)", "decimal")], name="valuation"),
        ],
    )
    unpenalized = similarity(one_detail, one_detail)
    penalized = similarity(one_detail, two_details)
    assert penalized < unpenalized
    assert penalized < FAMILY_THRESHOLD  # the two shapes must not be mistaken for the same family


# ---------------------------------------------------------------- tier


def test_assign_tier_simple_for_a_small_flat_layout():
    spec = _spec("a", fmt="delimited", size=6, details=[_detail([_field("x", type_="string")])])
    assert tier_score(spec) == 0 and assign_tier(spec) == "simple"


def test_assign_tier_medium_with_a_merge_rule():
    merge = MergeRule(mode_field="flag", modes={"R": "refresh", "U": "update"}, business_date_field="file_date", keys=("account_number",))
    spec = _spec("a", details=[_position_detail()], merge=merge)
    assert tier_score(spec) == 1 and assign_tier(spec) == "medium"


def test_assign_tier_complex_with_several_structural_signals():
    merge = MergeRule(mode_field="flag", modes={"R": "refresh", "U": "update"}, business_date_field="file_date", keys=("account_number",))
    lifecycle = LifecycleRule(record="detail", action_field="action", actions={"X": "cancel"}, identity=("id",), reference=("orig_id",))
    two_details = [_detail(list(_position_detail().fields), name="a"), _detail(list(_position_detail().fields), name="b")]
    spec = _spec("a", details=two_details, merge=merge, lifecycle=lifecycle, size=400)
    assert tier_score(spec) >= 3 and assign_tier(spec) == "complex"


# ---------------------------------------------------------------- classify / best_match


def test_best_match_is_none_with_no_classified_specs():
    unclassified = _spec("a", details=[_position_detail()])
    other_unclassified = _spec("b", details=[_position_detail()])
    assert best_match(unclassified, [other_unclassified]) is None


def test_classify_keeps_an_already_declared_family_without_guessing():
    spec = _spec("a", family="acme_position", details=[_position_detail()])
    registry = Registry(root=Path("specs"), specs=[spec])
    assignment = classify(spec, registry)
    assert assignment.family == "acme_position" and assignment.new_pattern_proposal is False


def test_classify_reuses_a_confidently_matching_family():
    known = _spec("known", family="acme_position", details=[_position_detail()])
    unclassified = _spec("new", details=[_position_detail(extra=[_field("lot_id", "X(12)")])])
    registry = Registry(root=Path("specs"), specs=[known, unclassified])
    assignment = classify(unclassified, registry)
    assert assignment.family == "acme_position"
    assert assignment.new_pattern_proposal is False
    assert assignment.match.matched_spec_id == "known"
    assert ("known", "2026-01-01") in assignment.reuse_candidates


def test_classify_proposes_a_new_pattern_when_nothing_matches_closely_enough():
    known = _spec("known", family="acme_position", details=[_position_detail()])
    two_details = [
        _detail([_field("record_type", "X(1)", "code"), _field("a", "X(5)")], name="holding"),
        _detail([_field("record_type", "X(1)", "code"), _field("b", "X(5)")], name="valuation"),
    ]
    unclassified = _spec("shaped_differently", details=two_details)
    registry = Registry(root=Path("specs"), specs=[known, unclassified])
    assignment = classify(unclassified, registry)
    assert assignment.family is None
    assert assignment.new_pattern_proposal is True
    assert assignment.reuse_candidates == ()


def test_classify_reports_the_pattern_list_and_tier():
    spec = _spec("a", family="acme_position", details=[_position_detail()])
    registry = Registry(root=Path("specs"), specs=[spec])
    assignment = classify(spec, registry)
    assert assignment.patterns == ("fixed_width_multi_record",)
    assert assignment.tier == "simple"  # a single, modest detail record with no merge/split/lifecycle


# ---------------------------------------------------------------- run()


def test_run_classifies_every_unclassified_spec_by_default():
    known = _spec("known", family="acme_position", details=[_position_detail()])
    unclassified = _spec("new", details=[_position_detail()])
    registry = Registry(root=Path("specs"), specs=[known, unclassified])
    assignments = run(registry)
    assert [a.spec_id for a in assignments] == ["new"]


def test_run_classifies_one_named_spec_even_if_already_classified():
    known = _spec("known", family="acme_position", details=[_position_detail()])
    registry = Registry(root=Path("specs"), specs=[known])
    assignments = run(registry, spec_id="known", spec_version="2026-01-01")
    assert len(assignments) == 1 and assignments[0].spec_id == "known"


def test_run_raises_a_clear_error_for_an_unknown_spec():
    registry = Registry(root=Path("specs"), specs=[])
    with pytest.raises(PatternMatcherError, match="no spec"):
        run(registry, spec_id="missing")


# ---------------------------------------------------------------- report


def test_render_markdown_reports_the_architect_queue():
    known = _spec("known", family="acme_position", details=[_position_detail()])
    two_details = [
        _detail([_field("record_type", "X(1)", "code"), _field("a", "X(5)")], name="holding"),
        _detail([_field("record_type", "X(1)", "code"), _field("b", "X(5)")], name="valuation"),
    ]
    unclassified = _spec("shaped_differently", details=two_details)
    registry = Registry(root=Path("specs"), specs=[known, unclassified])
    assignments = (classify(known, registry), classify(unclassified, registry))
    text = render_markdown(assignments)
    assert "Architect queue" in text
    assert "shaped_differently" in text


def test_write_assignments_writes_report_and_json(tmp_path):
    spec = _spec("a", family="acme_position", details=[_position_detail()])
    registry = Registry(root=Path("specs"), specs=[spec])
    assignments = (classify(spec, registry),)
    report_path, data_path = write_assignments(assignments, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data[0]["family"] == "acme_position"


# ---------------------------------------------------------------- load_registry


def test_load_registry_reads_the_real_committed_specs_directory():
    registry = load_registry(REPO / "specs")
    assert {s.id for s in registry.specs} >= {"pershing_gcus", "csv_price_example"}


def test_load_registry_raises_a_clear_error_for_a_bad_directory(tmp_path):
    bad = tmp_path / "specs" / "x"
    bad.mkdir(parents=True)
    (bad / "v1.yaml").write_text("not: {valid", encoding="utf-8")
    with pytest.raises(PatternMatcherError):
        load_registry(tmp_path / "specs")


# ---------------------------------------------------------------- CLI


def test_cli_pattern_matcher_run_classifies_unclassified_specs(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["pattern-matcher", "run", "--registry", str(GOLD), "--out", str(out)])
    assert code == 1, capsys.readouterr()  # 3 of 4 gold-fixture specs correctly propose a new pattern
    assert (out / "report.md").exists()
    text = capsys.readouterr().out
    assert "new pattern proposal" in text


def test_cli_pattern_matcher_run_one_spec_that_reuses_a_family(tmp_path):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["pattern-matcher", "run", "--registry", str(GOLD), "--id", "pershing_gcus", "--version", "2017-07-25", "--out", str(out)])
    assert code == 0


def test_cli_pattern_matcher_run_reports_a_start_error_for_a_bad_registry(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["pattern-matcher", "run", "--registry", str(tmp_path / "missing"), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the committed gold fixture


def test_gold_fixture_matches_the_story_acceptance_criteria():
    """S5.3.1's own acceptance criteria, proven end to end against real, already-committed specs:
    a known custodian (pershing_gcus) is correctly reunited with its own family, and every genuinely
    new shape is routed to the architect queue instead of guessed into one."""
    registry = load_registry(GOLD)
    assignments = {a.spec_id: a for a in run(registry)}
    assert assignments["pershing_gcus"].family == "pershing_gcus"
    assert assignments["pershing_gcus"].new_pattern_proposal is False
    for new_family_id in ("split_position_example", "drip_transaction_example", "csv_price_example"):
        assert assignments[new_family_id].family is None
        assert assignments[new_family_id].new_pattern_proposal is True
