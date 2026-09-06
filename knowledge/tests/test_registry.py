import shutil
from datetime import date
from pathlib import Path

from astra_knowledge.registry import Registry, load_spec_file

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
V1 = SPECS / "pershing_gcus" / "2017-07-25.yaml"


def copy_registry(tmp_path: Path) -> Path:
    root = tmp_path / "specs"
    shutil.copytree(SPECS, root, ignore=shutil.ignore_patterns("README.md"))
    return root


def write_variant(root: Path, spec_id: str, version: str, transform) -> Path:
    folder = root / spec_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{version}.yaml"
    text = V1.read_text(encoding="utf-8")
    text = text.replace('id: pershing_gcus', f'id: {spec_id}').replace('version: "2017-07-25"', f'version: "{version}"')
    path.write_text(transform(text), encoding="utf-8")
    return path


def problems_for(tmp_path: Path, transform, version: str = "2017-07-25") -> list[str]:
    root = tmp_path / "specs"
    path = write_variant(root, "pershing_gcus", version, transform)
    _, problems = load_spec_file(path, tmp_path)
    return [p.message for p in problems]


# -- the shipped registry -------------------------------------------------------


def test_the_shipped_registry_is_valid_and_has_two_coexisting_versions():
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    assert [s.version for s in registry.versions("pershing_gcus")] == ["2017-07-25", "2026-01-01"]
    assert registry.ids() == ["csv_price_example", "drip_transaction_example", "pershing_gcus", "split_position_example"]


def test_every_field_carries_position_picture_and_citation():
    spec, problems = load_spec_file(V1, REPO)
    assert problems == [] and spec is not None
    for record, field in spec.fields():
        assert field.position is not None, f"{record.label}.{field.name} has no position"
        assert field.picture is not None, f"{record.label}.{field.name} has no picture"
        assert field.citation.page or field.citation.line, f"{record.label}.{field.name} has no citation"
    quantity = spec.record("detail").field("quantity")
    assert quantity.picture.text == "9(13)V9(5)" and quantity.type == "decimal" and quantity.sign_field == "quantity_sign"
    assert quantity.citation.text() == "page 13, line 2"
    assert spec.record("header").field("refresh_flag").codes == (("R", "full refresh; replaces all positions for the remote id"), ("U", "update; merge on keys"))


def test_resolve_returns_the_version_in_force_for_a_business_date():
    registry, _ = Registry.load(SPECS, REPO)
    assert registry.resolve("pershing", "position", date(2017, 7, 25)).version == "2017-07-25"
    assert registry.resolve("pershing", "position", date(2025, 12, 31)).version == "2017-07-25"
    assert registry.resolve("pershing", "position", date(2026, 1, 1)).version == "2026-01-01"
    assert registry.resolve("pershing", "position", date(2026, 9, 6)).version == "2026-01-01"
    assert registry.resolve("pershing", "position", date(2017, 7, 24)) is None
    assert registry.resolve("schwab", "position", date(2026, 9, 6)) is None
    assert registry.resolve("pershing", "transaction", date(2026, 9, 6)) is None


def test_get_and_versions():
    registry, _ = Registry.load(SPECS, REPO)
    assert registry.get("pershing_gcus", "2026-01-01").record("detail").field("lot_id").position == (67, 12)
    assert registry.get("pershing_gcus", "1999-01-01") is None
    assert registry.versions("unknown") == []


# -- per-file checks ------------------------------------------------------------


def test_file_and_directory_names_must_match_id_and_version(tmp_path):
    root = tmp_path / "specs"
    # File is v1.yaml but the spec says its version is 2017-07-25.
    path = write_variant(root, "pershing_gcus", "v1", lambda t: t.replace('version: "v1"', 'version: "2017-07-25"'))
    _, problems = load_spec_file(path, tmp_path)
    assert any("spec.version '2017-07-25' must match the file name; the file must be 2017-07-25.yaml" in p.message for p in problems)
    # Directory is wrong_dir but the spec says its id is pershing_gcus.
    path = write_variant(root, "wrong_dir", "2017-07-25", lambda t: t.replace("id: wrong_dir", "id: pershing_gcus"))
    _, problems = load_spec_file(path, tmp_path)
    assert any("spec.id 'pershing_gcus' must match the directory name; the file must be under specs/pershing_gcus/" in p.message for p in problems)


def test_field_beyond_record_length_and_overlap_are_reported(tmp_path):
    beyond = problems_for(tmp_path, lambda t: t.replace("position: { start: 13, length: 108 }", "position: { start: 13, length: 120 }").replace("picture: X(108)", "picture: X(120)"))
    assert any("'filler' ends at 132, beyond the record length 120" in m for m in beyond)
    overlap = problems_for(tmp_path, lambda t: t.replace("position: { start: 14, length: 9 }", "position: { start: 12, length: 9 }"))
    assert any("'cusip' (12-20) overlaps 'account_number' (4-13)" in m for m in overlap)


def test_picture_must_fit_position_and_type_must_fit_picture(tmp_path):
    width = problems_for(tmp_path, lambda t: t.replace("picture: 9(13)V9(5)", "picture: 9(13)V9(4)"))
    assert any("'quantity' has length 18 but picture 9(13)V9(4) occupies 17" in m for m in width)
    kind = problems_for(tmp_path, lambda t: t.replace("picture: X(10)\n        required: true\n        citation: { page: 12, line: 5 }", "picture: X(10)\n        type: decimal\n        required: true\n        citation: { page: 12, line: 5 }"))
    assert any("type 'decimal' does not fit picture X(10)" in m for m in kind)


def test_dates_need_a_format_and_codes_must_be_unique(tmp_path):
    no_format = problems_for(tmp_path, lambda t: t.replace("        format: YYYYMMDD\n        citation: { page: 4, line: 5 }", "        citation: { page: 4, line: 5 }"))
    assert any("is a date and needs a format" in m for m in no_format)
    dup = problems_for(tmp_path, lambda t: t.replace("- { value: MF, meaning: mutual fund }", "- { value: EQ, meaning: again }"))
    assert any("'security_type' lists code 'EQ' more than once" in m for m in dup)


def test_sign_field_must_exist_and_citation_must_have_page_or_line(tmp_path):
    sign = problems_for(tmp_path, lambda t: t.replace("sign_field: quantity_sign", "sign_field: qty_sign"))
    assert any("sign_field 'qty_sign' is not a field of this record" in m for m in sign)
    cite = problems_for(tmp_path, lambda t: t.replace("citation: { page: 13, line: 9 }", "citation: { document: other.pdf }"))
    assert any("citation: must have page or line" in m for m in cite)


def test_multiple_detail_records_need_names_and_match_rules(tmp_path):
    def add_detail(text: str) -> str:
        second = (
            "  - type: detail\n"
            "    fields:\n"
            "      - name: record_type\n"
            "        position: { start: 1, length: 3 }\n"
            "        picture: X(3)\n"
            "        citation: { page: 20 }\n"
        )
        return text.replace("  - type: trailer", second + "  - type: trailer")

    messages = problems_for(tmp_path, add_detail)
    assert any("needs a name because the file has more than one detail record type" in m for m in messages)
    assert any("needs a match rule because the file has more than one detail record type" in m for m in messages)


def test_schema_errors_carry_lines_and_plain_words(tmp_path):
    messages = problems_for(tmp_path, lambda t: t.replace("format: fixed_width", "format: fixed"))
    assert messages == ["file.format: 'fixed' is not one of fixed_width, delimited"]


# -- registry-wide checks -------------------------------------------------------


def test_two_versions_in_force_on_the_same_date_for_one_custodian_is_an_error(tmp_path):
    root = copy_registry(tmp_path)
    write_variant(root, "pershing_alt", "2026-01-01", lambda t: t.replace("effective_from: 2017-07-25", "effective_from: 2026-01-01"))
    _, problems = Registry.load(root, tmp_path)
    assert any("both come into force for custodian 'pershing' position files on 2026-01-01" in p.message for p in problems)


def test_different_custodians_may_share_a_layout_and_resolve_independently(tmp_path):
    root = copy_registry(tmp_path)
    for version in ("2017-07-25", "2026-01-01"):
        path = root / "pershing_gcus" / f"{version}.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("custodians: [pershing]", "custodians: [pershing, acme_custody]"), encoding="utf-8")
    registry, problems = Registry.load(root, tmp_path)
    assert problems == []
    assert registry.resolve("acme_custody", "position", date(2026, 3, 1)).version == "2026-01-01"


# -- search ----------------------------------------------------------------------


def search_registry(tmp_path: Path) -> Registry:
    """The shipped registry plus a layout shared by another custodian and one unclassified layout."""
    root = copy_registry(tmp_path)
    write_variant(root, "acme_positions", "2024-01-01", lambda t: t.replace("custodians: [pershing]", "custodians: [acme]").replace("effective_from: 2017-07-25", "effective_from: 2024-01-01"))
    write_variant(root, "other_positions", "2020-05-01", lambda t: t.replace("custodians: [pershing]", "custodians: [other]").replace("effective_from: 2017-07-25", "effective_from: 2020-05-01").replace("  family: pershing_gcus\n", ""))
    write_variant(root, "acme_trades", "2024-01-01", lambda t: t.replace("custodians: [pershing]", "custodians: [acme]").replace("file_type: position", "file_type: transaction").replace("effective_from: 2017-07-25", "effective_from: 2024-01-01").replace("  family: pershing_gcus\n", "  family: acme_trades\n"))
    registry, problems = Registry.load(root, tmp_path)
    assert problems == []
    return registry


def test_search_ranks_exact_custodian_above_family(tmp_path):
    registry = search_registry(tmp_path)
    hits = registry.search(custodian="acme", family="pershing_gcus", file_type="position")
    assert [(h.spec.id, h.match, h.rank) for h in hits] == [("acme_positions", "custodian", 1), ("pershing_gcus", "family", 2)]


def test_search_by_family_alone_finds_reuse_candidates_for_an_unknown_custodian(tmp_path):
    registry = search_registry(tmp_path)
    hits = registry.search(custodian="schwab", family="pershing_gcus")
    # Both are family matches; within a rank the newest version comes first.
    assert [(h.spec.id, h.match) for h in hits] == [("pershing_gcus", "family"), ("acme_positions", "family")]


def test_search_by_file_type_returns_everything_and_flags_unclassified_specs(tmp_path):
    registry = search_registry(tmp_path)
    hits = registry.search(file_type="position")
    # Newest first within the rank; equal dates fall back to the id.
    assert [h.spec.id for h in hits] == ["pershing_gcus", "split_position_example", "acme_positions", "other_positions"]
    assert all(h.match == "file_type" for h in hits)
    flagged = [h for h in hits if h.needs_classification]
    assert [h.spec.id for h in flagged] == ["other_positions"] and flagged[0].flags == ("no family; needs classification",)
    assert [s.id for s in registry.unclassified()] == ["other_positions"]


def test_search_with_a_date_keeps_the_version_in_force(tmp_path):
    registry = search_registry(tmp_path)
    hits = registry.search(custodian="pershing", business_date=date(2025, 6, 1))
    assert [(h.spec.id, h.spec.version) for h in hits] == [("pershing_gcus", "2017-07-25")]
    assert registry.search(custodian="pershing", business_date=date(2016, 1, 1)) == []
    both = registry.search(custodian="pershing", all_versions=True)
    assert [h.spec.version for h in both] == ["2026-01-01", "2017-07-25"]


def test_search_needs_at_least_one_criterion(tmp_path):
    registry = search_registry(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        registry.search()
    assert registry.search(custodian="acme", file_type="transaction")[0].spec.id == "acme_trades"


def test_missing_registry_directory_is_a_problem(tmp_path):
    registry, problems = Registry.load(tmp_path / "nowhere", tmp_path)
    assert registry.specs == [] and problems[0].message == "spec registry directory does not exist"
