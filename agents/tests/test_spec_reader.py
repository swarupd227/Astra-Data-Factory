"""Spec Reader (S5.1.1): a layout document turned into a Source Spec draft, with page citations, never guessing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from astra_agents.spec_reader import (
    DraftSpec,
    Extraction,
    Page,
    SpecReaderError,
    build_draft,
    extract_pages,
    render_markdown,
    run,
    sha256_of,
    system_prompt,
    validate_draft,
    write_draft,
)


class FakeClient:
    """Answers with a canned Extraction (or raises), and records every call it was given."""

    def __init__(self, extraction: Extraction | None = None, error: Exception | None = None) -> None:
        self.extraction = extraction
        self.error = error
        self.calls: list[dict] = []

    def extract(self, *, system: str, pages: list[Page]) -> Extraction:
        self.calls.append({"system": system, "pages": pages})
        if self.error:
            raise self.error
        return self.extraction


HEADER_RECORD = {
    "type": "header",
    "match": {"start": 1, "length": 3, "value": "HDR"},
    "fields": [
        {"name": "record_type", "start": 1, "length": 3, "picture": "X(3)", "type": "code", "citation": {"page": 2}, "codes": [{"value": "HDR", "meaning": "header record"}]},
        {"name": "file_date", "start": 4, "length": 8, "picture": "9(8)", "type": "date", "format": "YYYYMMDD", "citation": {"page": 2, "line": 5}},
    ],
}
DETAIL_RECORD = {
    "type": "detail",
    "match": {"start": 1, "length": 3, "value": "DTL"},
    "fields": [
        {"name": "account_number", "start": 1, "length": 10, "picture": "X(10)", "required": True, "citation": {"page": 3, "line": 5}},
        {
            "name": "quantity",
            "start": 11,
            "length": 18,
            "picture": "9(13)V9(5)",
            "sign_field": "quantity_sign",
            "citation": {"page": 3, "line": 9},
        },
        {
            "name": "quantity_sign",
            "start": 29,
            "length": 1,
            "picture": "X(1)",
            "type": "code",
            "citation": {"page": 3, "line": 10},
            "codes": [{"value": "+", "meaning": "long", "sign": "positive"}, {"value": "-", "meaning": "short", "sign": "negative"}],
        },
    ],
}
GOOD_EXTRACTION = Extraction(file={"format": "fixed_width", "record_length": 30}, records=[HEADER_RECORD, DETAIL_RECORD], unparsed=[])


def _draft(extraction: Extraction = GOOD_EXTRACTION, **kwargs) -> DraftSpec:
    defaults = dict(
        spec_id="pershing_gcus_full", version="2017-07-25", effective_from="2017-07-25", file_type="position",
        custodians=["pershing"], document_title="Global Customer Position", document_reference="GCUS.pdf",
    )
    defaults.update(kwargs)
    return build_draft(extraction, **defaults)


# ---------------------------------------------------------------- document reading


def test_extract_pages_dispatches_pdf(monkeypatch, tmp_path):
    called = {}

    def fake_pdf(path):
        called["path"] = path
        return [Page(1, "text")]

    monkeypatch.setattr("astra_agents.spec_reader.extract_pdf_pages", fake_pdf)
    path = tmp_path / "layout.pdf"
    path.write_bytes(b"%PDF-1.4")
    pages = extract_pages(path)
    assert pages == [Page(1, "text")] and called["path"] == path


def test_extract_pages_dispatches_docx(monkeypatch, tmp_path):
    monkeypatch.setattr("astra_agents.spec_reader.extract_docx_pages", lambda path: [Page(None, "text")])
    path = tmp_path / "layout.docx"
    path.write_bytes(b"PK")
    assert extract_pages(path) == [Page(None, "text")]


def test_extract_pages_refuses_an_unsupported_type(tmp_path):
    path = tmp_path / "layout.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(SpecReaderError, match="unsupported document type"):
        extract_pages(path)


def test_extract_pdf_pages_maps_pdfplumber_pages_to_page_objects(monkeypatch, tmp_path):
    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakePdf:
        pages = [FakePage("page one text"), FakePage(None)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_module = type("m", (), {"open": staticmethod(lambda path: FakePdf())})
    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", fake_module)
    from astra_agents.spec_reader import extract_pdf_pages

    pages = extract_pdf_pages(tmp_path / "x.pdf")
    assert pages == [Page(1, "page one text"), Page(2, "")]


def test_sha256_of_matches_hashlib(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(b"hello world")
    assert sha256_of(path) == hashlib.sha256(b"hello world").hexdigest()


# ---------------------------------------------------------------- prompt


def test_system_prompt_names_the_file_type_and_custodians():
    text = system_prompt("position", ["pershing", "schwab"])
    assert "position" in text and "pershing, schwab" in text
    assert "never guess" not in text.lower() or "citation" in text.lower()  # the guardrail is stated one way or another
    assert "unparsed" in text


# ---------------------------------------------------------------- build_draft / translation


def test_build_draft_translates_positions_citations_and_sign_fields():
    draft = _draft()
    header, detail = draft.data["records"]
    assert header["match"] == {"value": "HDR", "position": {"start": 1, "length": 3}}
    file_date = header["fields"][1]
    assert file_date["position"] == {"start": 4, "length": 8} and file_date["citation"] == {"page": 2, "line": 5}
    quantity = detail["fields"][1]
    assert quantity["sign_field"] == "quantity_sign"
    assert detail["fields"][2]["codes"][0] == {"value": "+", "meaning": "long", "sign": "positive"}


def test_build_draft_is_valid_against_the_registry_schema():
    draft = _draft()
    assert draft.valid is True and draft.problems == ()
    assert draft.field_count == 5


def test_build_draft_carries_unparsed_sections_through():
    extraction = Extraction(file=GOOD_EXTRACTION.file, records=[HEADER_RECORD], unparsed=[{"citation": {"page": 9}, "reason": "table spans a page break; could not confirm column widths"}])
    draft = _draft(extraction=extraction)
    assert draft.unparsed == ({"citation": {"page": 9}, "reason": "table spans a page break; could not confirm column widths"},)


def test_build_draft_flags_a_field_with_no_citation_never_silently_accepted():
    """The tool schema requires a citation on every real call; this proves the *separate* registry-schema check also catches it, in case a client ever bypasses the tool."""
    bad_field = {"name": "mystery_field"}  # no citation
    bad_record = {"type": "detail", "match": {"value": "DTL"}, "fields": [bad_field]}
    extraction = Extraction(file=GOOD_EXTRACTION.file, records=[bad_record], unparsed=[])
    draft = _draft(extraction=extraction)
    assert draft.valid is False
    assert any("citation" in p.message for p in draft.problems)


def test_build_draft_includes_optional_spec_and_document_fields():
    draft = _draft(family="pershing_gcus", provider="Pershing", description="Global customer position", document_sha256="a" * 64, document_pages=13)
    assert draft.data["spec"]["family"] == "pershing_gcus"
    assert draft.data["spec"]["provider"] == "Pershing"
    assert draft.data["document"]["sha256"] == "a" * 64
    assert draft.data["document"]["pages"] == 13


def test_validate_draft_directly_on_a_hand_built_dict():
    minimal = {
        "spec_version": 0,
        "spec": {"id": "x", "version": "1", "effective_from": "2026-01-01", "file_type": "position", "custodians": ["pershing"]},
        "document": {"title": "t", "reference": "r.pdf"},
        "file": {"format": "fixed_width", "record_length": 10},
        "records": [{"type": "header", "fields": [{"name": "a", "position": {"start": 1, "length": 3}, "picture": "X(3)", "citation": {"page": 1}}]}],
    }
    assert validate_draft(minimal) == []


# ---------------------------------------------------------------- run()


def test_run_reads_the_document_and_calls_the_client(tmp_path, monkeypatch):
    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "HEADER..."), Page(2, "DETAIL...")])
    client = FakeClient(extraction=GOOD_EXTRACTION)
    doc = tmp_path / "GCUS.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    draft = run(doc, client, spec_id="pershing_gcus_full", version="2017-07-25", effective_from="2017-07-25", file_type="position", custodians=["pershing"])
    assert draft.valid is True
    assert len(client.calls) == 1
    assert len(client.calls[0]["pages"]) == 2
    assert draft.data["document"]["reference"] == "GCUS.pdf"
    assert draft.data["document"]["sha256"] == hashlib.sha256(b"%PDF-1.4 fake").hexdigest()
    assert draft.data["document"]["pages"] == 2  # both pages numbered, so a real page count is known


def test_run_leaves_document_pages_unset_for_a_page_less_format(tmp_path, monkeypatch):
    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(None, "all the text")])
    client = FakeClient(extraction=GOOD_EXTRACTION)
    doc = tmp_path / "layout.docx"
    doc.write_bytes(b"PK fake")
    draft = run(doc, client, spec_id="x", version="1", effective_from="2026-01-01", file_type="position", custodians=["pershing"])
    assert draft.data["document"].get("pages") is None


def test_run_propagates_a_client_error(tmp_path, monkeypatch):
    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "text")])
    client = FakeClient(error=SpecReaderError("the model did not call extract_source_spec; stop reason max_tokens"))
    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"x")
    with pytest.raises(SpecReaderError, match="did not call"):
        run(doc, client, spec_id="x", version="1", effective_from="2026-01-01", file_type="position", custodians=["pershing"])


# ---------------------------------------------------------------- the report


def test_render_markdown_names_records_citations_and_unparsed():
    extraction = Extraction(file=GOOD_EXTRACTION.file, records=[HEADER_RECORD, DETAIL_RECORD], unparsed=[{"citation": {"page": 9}, "reason": "unclear"}])
    draft = _draft(extraction=extraction)
    text = render_markdown(draft)
    assert "# Spec Reader draft: pershing_gcus_full 2017-07-25" in text
    assert "5 field(s) extracted" in text
    assert "| p9 | unclear |" in text


def test_render_markdown_says_nothing_unparsed_when_clean():
    draft = _draft()
    assert "Nothing was left unparsed." in render_markdown(draft)


def test_write_draft_writes_a_reloadable_yaml_spec_and_reports(tmp_path):
    draft = _draft()
    spec_path, report_path, data_path = write_draft(draft, tmp_path / "out")
    reloaded = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    assert reloaded == draft.data
    assert report_path.read_text(encoding="utf-8") == render_markdown(draft)
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    assert payload["valid"] is True and payload["field_count"] == 5


# ---------------------------------------------------------------- CLI


def test_cli_runs_with_a_fake_client(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "text")])
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None: FakeClient(extraction=GOOD_EXTRACTION))
    doc = tmp_path / "GCUS.pdf"
    doc.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out"
    code = cli.main([
        "spec-reader", "run", str(doc),
        "--id", "pershing_gcus_full", "--version", "2017-07-25", "--effective-from", "2017-07-25",
        "--file-type", "position", "--custodian", "pershing",
        "--out", str(out),
    ])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing_gcus_full" / "2017-07-25" / "spec.yaml").exists()
    text = capsys.readouterr().out
    assert "valid: yes" in text


def test_cli_exits_nonzero_on_an_invalid_draft(tmp_path, monkeypatch):
    import astra_agents.cli as cli

    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "text")])
    bad_field = {"name": "mystery_field"}
    bad_record = {"type": "detail", "match": {"value": "DTL"}, "fields": [bad_field]}
    bad_extraction = Extraction(file=GOOD_EXTRACTION.file, records=[bad_record], unparsed=[])
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None: FakeClient(extraction=bad_extraction))
    doc = tmp_path / "GCUS.pdf"
    doc.write_bytes(b"%PDF-1.4")
    code = cli.main([
        "spec-reader", "run", str(doc),
        "--id", "x", "--version", "1", "--effective-from", "2026-01-01",
        "--file-type", "position", "--custodian", "pershing",
        "--out", str(tmp_path / "out"),
    ])
    assert code == 1


def test_cli_reports_a_start_error(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "text")])
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None: FakeClient(error=SpecReaderError("boom")))
    doc = tmp_path / "GCUS.pdf"
    doc.write_bytes(b"%PDF-1.4")
    code = cli.main([
        "spec-reader", "run", str(doc),
        "--id", "x", "--version", "1", "--effective-from", "2026-01-01",
        "--file-type", "position", "--custodian", "pershing",
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert "boom" in capsys.readouterr().err
