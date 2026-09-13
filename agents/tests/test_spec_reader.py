"""Spec Reader (S5.1.1): a layout document turned into a Source Spec draft, with page citations, never guessing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from astra_agents.spec_reader import (
    AnthropicClient,
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


def test_picture_zero_padded_lengths_are_normalized():
    """A real finding from the first live run: Pershing's own layout documents print X(02), not X(2) — a valid, common notation the schema's own pattern still rejects unless it is normalized."""
    from astra_agents.spec_reader import _normalize_picture

    assert _normalize_picture("X(02)") == "X(2)"
    assert _normalize_picture("9(08)") == "9(8)"
    assert _normalize_picture("X(10)") == "X(10)"  # already no leading zero: unchanged
    assert _normalize_picture("9(13)v9(05)") == "9(13)V9(5)"  # lower-case decimal marker uppercased too
    assert _normalize_picture("9(16)V9(02)") == "9(16)V9(2)"  # already upper-case: still normalizes the length


def test_build_draft_normalizes_a_zero_padded_picture_into_a_valid_draft():
    record = {
        "type": "detail",
        "match": {"start": 1, "length": 3, "value": "DTL"},
        "fields": [{"name": "account_number", "start": 1, "length": 10, "picture": "X(10)", "citation": {"page": 3}}, {"name": "quantity", "start": 11, "length": 18, "picture": "9(13)v9(05)", "citation": {"page": 3}}],
    }
    extraction = Extraction(file=GOOD_EXTRACTION.file, records=[record], unparsed=[])
    draft = _draft(extraction=extraction)
    assert draft.valid is True, draft.problems
    assert draft.data["records"][0]["fields"][1]["picture"] == "9(13)V9(5)"


def test_shorten_cuts_at_a_word_boundary_within_the_identifier_limit():
    from astra_agents.spec_reader import _MAX_IDENTIFIER, _shorten

    long_name = "corporate_executive_services_collateral_pledge_liquidating_value"
    assert len(long_name) > _MAX_IDENTIFIER
    short_name = _shorten(long_name, set())
    assert len(short_name) <= _MAX_IDENTIFIER
    assert long_name.startswith(short_name.rstrip("_"))
    assert not short_name.endswith("_")  # cut at the boundary itself, not mid-word


def test_shorten_disambiguates_a_collision_against_taken_names():
    from astra_agents.spec_reader import _shorten

    taken = {"corporate_executive_services_collateral_pledge_liquidating"}
    short_name = _shorten("corporate_executive_services_collateral_pledge_liquidating_value", taken)
    assert short_name not in taken
    assert short_name.endswith("_2")


def test_fit_identifiers_renames_a_too_long_field_and_remaps_its_sign_field_reference():
    """A real finding from the second live run: a 68-character column label produced both a too-long field name and a
    too-long sign_field naming that same field — fixing the name alone would leave sign_field pointing at nothing."""
    from astra_agents.spec_reader import _MAX_IDENTIFIER, _fit_identifiers

    long_name = "corporate_executive_services_collateral_pledge_liquidating_value"
    sign_name = long_name + "_sign"
    fields = [
        {"name": long_name, "sign_field": sign_name, "citation": {"page": 11}},
        {"name": sign_name, "citation": {"page": 11}},
    ]
    fitted = _fit_identifiers(fields)
    assert all(len(f["name"]) <= _MAX_IDENTIFIER for f in fitted)
    assert fitted[0]["sign_field"] == fitted[1]["name"]
    assert fitted[0]["name"] != fitted[1]["name"]


def test_build_draft_fits_two_colliding_over_long_names_with_a_cross_reference():
    long_name = "corporate_executive_services_collateral_pledge_liquidating_value"
    sign_name = long_name + "_sign"
    record = {
        "type": "detail",
        "match": {"start": 1, "length": 3, "value": "DTL"},
        "fields": [
            {"name": "account_number", "start": 1, "length": 10, "picture": "X(10)", "citation": {"page": 3}},
            {"name": long_name, "start": 11, "length": 18, "picture": "9(13)V9(5)", "sign_field": sign_name, "citation": {"page": 11}},
            {"name": sign_name, "start": 29, "length": 1, "picture": "X(1)", "type": "code", "citation": {"page": 11}},
        ],
    }
    extraction = Extraction(file=GOOD_EXTRACTION.file, records=[record], unparsed=[])
    draft = _draft(extraction=extraction)
    assert draft.valid is True, draft.problems
    names = [f["name"] for f in draft.data["records"][0]["fields"]]
    assert len(names) == len(set(names))  # no collision
    renamed_value_field = next(f for f in draft.data["records"][0]["fields"] if f["name"] != "account_number" and "sign_field" in f)
    assert renamed_value_field["sign_field"] == names[2]


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


# ---------------------------------------------------------------- AnthropicClient.extract


def _fake_message(*, tool_input: dict | None, stop_reason: str = "tool_use", tool_name: str = "extract_source_spec"):
    reason = stop_reason  # class bodies don't see an enclosing name being reassigned to itself

    class FakeToolUseBlock:
        type = "tool_use"
        name = tool_name
        input = tool_input or {}

    class FakeMessage:
        content = [FakeToolUseBlock()] if tool_input is not None else []
        stop_reason = reason

    return FakeMessage()


def _fake_anthropic_for_extract(message):
    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return message

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream()

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    return type("anthropic", (), {"Anthropic": FakeAnthropic})


def test_extract_returns_the_extraction_from_a_complete_tool_call(monkeypatch):
    message = _fake_message(tool_input={"file": {"format": "fixed_width"}, "records": [HEADER_RECORD], "unparsed": []})
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_for_extract(message))
    extraction = AnthropicClient().extract(system="sys", pages=[Page(1, "text")])
    assert extraction.records == [HEADER_RECORD]


def test_extract_raises_a_clear_error_when_truncated_at_the_token_limit(monkeypatch):
    """A real finding from the first live run: a large document's response can be cut off mid-call before `records` is written; this must not surface as a raw KeyError."""
    message = _fake_message(tool_input={"file": {"format": "fixed_width"}}, stop_reason="max_tokens")
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_for_extract(message))
    with pytest.raises(SpecReaderError, match="cut off at the token limit"):
        AnthropicClient().extract(system="sys", pages=[Page(1, "text")])


def test_extract_raises_a_clear_error_when_incomplete_for_another_reason(monkeypatch):
    message = _fake_message(tool_input={"file": {"format": "fixed_width"}}, stop_reason="end_turn")
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_for_extract(message))
    with pytest.raises(SpecReaderError, match="missing records; stop reason end_turn"):
        AnthropicClient().extract(system="sys", pages=[Page(1, "text")])


def test_extract_raises_when_the_model_never_calls_the_tool(monkeypatch):
    message = _fake_message(tool_input=None, stop_reason="end_turn")
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_for_extract(message))
    with pytest.raises(SpecReaderError, match="did not call extract_source_spec"):
        AnthropicClient().extract(system="sys", pages=[Page(1, "text")])


# ---------------------------------------------------------------- AnthropicClient.test_connection

# Module-level so every _fake_anthropic_module() call shares the same class objects — an
# exception instance built from one call's classes must still match `except anthropic.X`
# when a *different* fake module is the one installed as `anthropic` at call time.


class FakeAuthenticationError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


class FakeAPIStatusError(Exception):
    pass


def _fake_anthropic_module(*, models=(), raises=None):
    """A stand-in for the `anthropic` package: its own exception classes (so `except anthropic.X` matches), and an Anthropic() whose models.list() returns `models` or raises `raises`."""

    class FakeModelInfo:
        def __init__(self, id_):
            self.id = id_

    class FakePage:
        def __init__(self, ids):
            self.data = [FakeModelInfo(i) for i in ids]

    class FakeModels:
        def list(self, limit=None):
            if raises is not None:
                raise raises
            return FakePage(models)

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    module = type(
        "anthropic",
        (),
        {
            "AuthenticationError": FakeAuthenticationError,
            "APIConnectionError": FakeAPIConnectionError,
            "APIStatusError": FakeAPIStatusError,
            "Anthropic": FakeAnthropic,
        },
    )
    return module


def test_connection_ok_when_the_configured_model_is_in_the_account(monkeypatch):
    fake = _fake_anthropic_module(models=("claude-sonnet-5", "claude-opus-5"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    result = AnthropicClient(model="claude-sonnet-5").test_connection()
    assert result.ok is True
    assert "available" in result.detail
    assert result.models == ("claude-sonnet-5", "claude-opus-5")


def test_connection_ok_but_notes_when_the_configured_model_is_not_listed(monkeypatch):
    fake = _fake_anthropic_module(models=("claude-opus-5",))
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    result = AnthropicClient(model="claude-sonnet-5").test_connection()
    assert result.ok is True
    assert "NOT in this account's model list" in result.detail


def test_connection_reports_authentication_failure(monkeypatch):
    fake = _fake_anthropic_module(raises=FakeAuthenticationError("invalid x-api-key"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    result = AnthropicClient().test_connection()
    assert result.ok is False and "authentication failed" in result.detail


def test_connection_reports_a_connection_failure(monkeypatch):
    fake = _fake_anthropic_module(raises=FakeAPIConnectionError("could not connect"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    result = AnthropicClient().test_connection()
    assert result.ok is False and "could not reach the API" in result.detail


def test_connection_reports_an_api_status_error(monkeypatch):
    fake = _fake_anthropic_module(raises=FakeAPIStatusError("rate limited"))
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    result = AnthropicClient().test_connection()
    assert result.ok is False and "the API returned an error" in result.detail


def test_connection_raises_a_clear_error_when_the_package_is_not_installed(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)  # import anthropic -> ModuleNotFoundError
    with pytest.raises(SpecReaderError, match="pip install"):
        AnthropicClient().test_connection()


# ---------------------------------------------------------------- CLI


def test_cli_test_connection_ok(monkeypatch, capsys):
    import astra_agents.cli as cli

    class FakeConnectionClient:
        def __init__(self, model=None):
            self.model = model

        def test_connection(self):
            from astra_agents.spec_reader import ConnectionTestResult

            return ConnectionTestResult(True, "connected; configured model 'claude-sonnet-5' is available", ("claude-sonnet-5",))

    monkeypatch.setattr(cli, "AnthropicClient", FakeConnectionClient)
    code = cli.main(["spec-reader", "test-connection"])
    assert code == 0
    assert "connected" in capsys.readouterr().out


def test_cli_test_connection_fails_clearly(monkeypatch, capsys):
    import astra_agents.cli as cli

    class FakeConnectionClient:
        def __init__(self, model=None):
            pass

        def test_connection(self):
            from astra_agents.spec_reader import ConnectionTestResult

            return ConnectionTestResult(False, "authentication failed: invalid x-api-key")

    monkeypatch.setattr(cli, "AnthropicClient", FakeConnectionClient)
    code = cli.main(["spec-reader", "test-connection"])
    assert code == 1
    assert "authentication failed" in capsys.readouterr().out


def test_cli_test_connection_reports_a_start_error(monkeypatch, capsys):
    import astra_agents.cli as cli

    class FakeConnectionClient:
        def __init__(self, model=None):
            pass

        def test_connection(self):
            raise SpecReaderError("the anthropic package is not installed; pip install 'astra-agents[llm]'")

    monkeypatch.setattr(cli, "AnthropicClient", FakeConnectionClient)
    code = cli.main(["spec-reader", "test-connection"])
    assert code == 2
    assert "pip install" in capsys.readouterr().err


def test_cli_runs_with_a_fake_client(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr("astra_agents.spec_reader.extract_pages", lambda path: [Page(1, "text")])
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None, max_tokens=None: FakeClient(extraction=GOOD_EXTRACTION))
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
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None, max_tokens=None: FakeClient(extraction=bad_extraction))
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
    monkeypatch.setattr(cli, "AnthropicClient", lambda model=None, max_tokens=None: FakeClient(error=SpecReaderError("boom")))
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
