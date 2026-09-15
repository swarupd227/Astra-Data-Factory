from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_knowledge.registry import Citation

from astra_control.spec_viewer import (
    SpecViewerError,
    citation_link,
    compare,
    field_list,
    load_registry,
    load_spec,
    render_compare,
    render_field_list,
)

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
SPEC_ID = "pershing_gcus"
OLD_VERSION = "2017-07-25"
NEW_VERSION = "2026-01-01"


def _registry():
    return load_registry(SPECS)


def _old():
    return load_spec(_registry(), SPEC_ID, OLD_VERSION)


def _new():
    return load_spec(_registry(), SPEC_ID, NEW_VERSION)


# ---------------------------------------------------------------- loading


def test_load_registry_rejects_a_bad_specs_dir(tmp_path):
    with pytest.raises(SpecViewerError):
        load_registry(tmp_path / "missing")


def test_load_spec_rejects_an_unknown_version():
    with pytest.raises(SpecViewerError, match="no spec"):
        load_spec(_registry(), SPEC_ID, "1999-01-01")


def test_load_spec_reads_the_real_registry():
    spec = _old()
    assert spec.id == SPEC_ID and spec.version == OLD_VERSION


# ---------------------------------------------------------------- citation_link


def test_citation_link_with_a_page_and_line():
    c = Citation(page=4, line=3, document="GCUS.pdf")
    assert citation_link(c, fallback_document="fallback") == "GCUS.pdf#page=4&line=3"


def test_citation_link_with_a_page_only():
    c = Citation(page=4)
    assert citation_link(c, fallback_document="fallback") == "fallback#page=4"


def test_citation_link_with_no_page_is_just_the_document():
    c = Citation(document="GCUS.pdf")
    assert citation_link(c, fallback_document="fallback") == "GCUS.pdf"


def test_citation_link_falls_back_when_the_citation_names_no_document():
    c = Citation(page=4, line=3)
    assert citation_link(c, fallback_document="pershing_gcus 2017-07-25 layout document") == "pershing_gcus 2017-07-25 layout document#page=4&line=3"


# ---------------------------------------------------------------- AC1: field list


def test_field_list_covers_every_field_of_the_real_spec():
    spec = _old()
    entries = field_list(spec)
    assert len(entries) == sum(len(r.fields) for r in spec.records)
    assert entries  # non-empty


def test_field_list_entries_carry_position_type_and_citation():
    entries = field_list(_old())
    record_type = next(e for e in entries if e.name == "record_type")
    assert record_type.start == 1 and record_type.length == 3 and record_type.end == 3
    assert record_type.type == "code"
    assert "page" in record_type.citation_text
    assert record_type.citation_link  # a real, non-empty reference


def test_field_list_citation_link_uses_a_real_page_and_line():
    entries = field_list(_old())
    e = next(x for x in entries if x.name == "record_type")
    assert "#page=" in e.citation_link


def test_to_dict_round_trips_through_json():
    entries = field_list(_old())
    data = json.loads(json.dumps([e.to_dict() for e in entries]))
    assert data[0]["name"] == entries[0].name


# ---------------------------------------------------------------- AC2: version compare, against real committed drift


def test_compare_finds_the_real_added_lot_id_field():
    diffs = compare(_old(), _new())
    added = [d for d in diffs if d.kind == "added"]
    assert any(d.field == "lot_id" and d.record == "detail" for d in added)


def test_compare_finds_the_real_shifted_filler_field():
    diffs = compare(_old(), _new())
    shifted = [d for d in diffs if d.kind == "shifted"]
    assert len(shifted) == 1
    d = shifted[0]
    assert d.field == "filler" and d.record == "detail"
    assert d.before["start"] == 67 and d.after["start"] == 79


def test_compare_finds_exactly_these_two_differences_in_the_real_specs():
    diffs = compare(_old(), _new())
    assert len(diffs) == 2
    assert {d.kind for d in diffs} == {"added", "shifted"}


def test_compare_of_a_spec_against_itself_is_empty():
    spec = _old()
    assert compare(spec, spec) == ()


def test_compare_removed_when_a_field_only_exists_in_the_old_version():
    diffs = compare(_new(), _old())  # reversed: lot_id now looks removed
    removed = [d for d in diffs if d.kind == "removed"]
    assert any(d.field == "lot_id" for d in removed)


def test_compare_detects_a_type_change_as_changed_not_shifted():
    from dataclasses import replace

    old = _old()
    new = _old()
    # construct a same-position, different-type variant directly, without touching committed files
    record = new.record("detail")
    field = record.field("record_type")
    changed_field = replace(field, type="string")
    changed_record = replace(record, fields=tuple(changed_field if f.name == "record_type" else f for f in record.fields))
    new = replace(new, records=tuple(changed_record if r.label == "detail" else r for r in new.records))

    diffs = compare(old, new)
    changed = [d for d in diffs if d.field == "record_type"]
    assert len(changed) == 1 and changed[0].kind == "changed"


# ---------------------------------------------------------------- render_*


def test_render_field_list_includes_every_column():
    text = render_field_list(_old())
    assert "record_type" in text and "Citation" in text and "Open" in text


def test_render_compare_shows_counts_and_both_kinds():
    text = render_compare(_old(), _new(), compare(_old(), _new()))
    assert "1 added, 1 shifted" in text
    assert "lot_id" in text and "filler" in text


def test_render_compare_with_no_differences_says_so():
    spec = _old()
    assert "No differences." in render_compare(spec, spec, compare(spec, spec))


# ---------------------------------------------------------------- CLI


def test_cli_show_against_the_real_spec(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "show", "--specs", str(SPECS), "--id", SPEC_ID, "--version", OLD_VERSION])
    assert code == 0, capsys.readouterr()
    text = capsys.readouterr().out
    assert "record_type" in text and "filler" in text


def test_cli_show_json(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "show", "--specs", str(SPECS), "--id", SPEC_ID, "--version", OLD_VERSION, "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert any(e["name"] == "lot_id" for e in data) is False  # 2017 version has no lot_id yet


def test_cli_show_unknown_version_is_a_start_error(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "show", "--specs", str(SPECS), "--id", SPEC_ID, "--version", "1999-01-01"])
    assert code == 2
    assert capsys.readouterr().err


def test_cli_compare_against_the_real_two_versions(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "compare", "--specs", str(SPECS), "--id", SPEC_ID, "--old", OLD_VERSION, "--new", NEW_VERSION])
    assert code == 1  # real differences exist
    text = capsys.readouterr().out
    assert "lot_id" in text and "filler" in text and "shifted" in text


def test_cli_compare_json(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "compare", "--specs", str(SPECS), "--id", SPEC_ID, "--old", OLD_VERSION, "--new", NEW_VERSION, "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2


def test_cli_compare_identical_versions_exits_zero(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "compare", "--specs", str(SPECS), "--id", SPEC_ID, "--old", OLD_VERSION, "--new", OLD_VERSION])
    assert code == 0
    assert "No differences." in capsys.readouterr().out


def test_cli_show_as_auditor_succeeds_reads_always_allowed(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "show", "--specs", str(SPECS), "--id", SPEC_ID, "--version", OLD_VERSION, "--role", "auditor"])
    assert code == 0, capsys.readouterr()


def test_cli_show_invalid_role_is_refused(capsys):
    import astra_control.cli as cli

    code = cli.main(["spec-viewer", "show", "--specs", str(SPECS), "--id", SPEC_ID, "--version", OLD_VERSION, "--role", "wizard"])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S6.3.4: the field list carries position, type and a citation link (AC1), and comparing two
    real versions highlights added, removed and shifted fields (AC2) -- against genuine committed
    drift between specs/pershing_gcus's two versions, not a synthetic example."""
    old, new = _old(), _new()

    entries = field_list(old)
    assert all(e.type for e in entries)
    assert all(e.citation_link for e in entries)
    assert any(e.start is not None and e.length is not None for e in entries)

    diffs = compare(old, new)
    kinds = {d.kind for d in diffs}
    assert "added" in kinds
    assert "shifted" in kinds
