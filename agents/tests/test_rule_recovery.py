from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.rules import Owner
from astra_agents.rule_recovery import (
    EXTRACTION_TOOL_NAME,
    Extraction,
    RuleRecoveryError,
    SourceFile,
    _numbered_text,
    build_draft,
    find_rejection_codes,
    find_tsql_lines,
    read_sources,
    render_markdown,
    run,
    system_prompt,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
JAVA_DIR = REPO / "agents" / "examples" / "rule_recovery" / "java"
SPLITTER = JAVA_DIR / "Splitter.java"
LOADER = JAVA_DIR / "Loader.java"
OWNER = Owner("Data steward, custodial", "steward@example.com")
CLOCK = lambda: datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


class FakeClient:
    def __init__(self, extraction: Extraction | None = None, error: Exception | None = None):
        self.extraction = extraction
        self.error = error
        self.calls: list[dict] = []

    def extract(self, *, system: str, sources):
        self.calls.append({"system": system, "sources": sources})
        if self.error:
            raise self.error
        return self.extraction


GOOD_EXTRACTION = Extraction(
    entries=[
        {
            "name": "unknown_record_type_rejected",
            "text": "A line whose first three characters are not HDR, DTL or TRL is rejected and never reaches the Loader.",
            "class": "ingestion",
            "citation": {"file": "Splitter.java", "line": 33, "end_line": 47},
            "rejection_codes": ["L001"],
            "candidate_tests": [{"description": "a line starting with XXX is rejected", "given": "a line 'XXXfoo...'", "expect": "rejected with L001, never added to details"}],
        },
        {
            "name": "missing_account_rejected",
            "text": "A detail record with a blank account number is rejected before quantity or security type are checked.",
            "class": "ingestion",
            "citation": {"file": "Loader.java", "line": 31, "end_line": 34},
            "rejection_codes": ["L002"],
        },
        {
            "name": "quantity_must_be_numeric",
            "text": "A detail record whose quantity field is not all digits is rejected rather than loaded as zero.",
            "class": "ingestion",
            "citation": {"file": "Loader.java", "line": 36, "end_line": 39},
            "rejection_codes": ["L003"],
        },
        {
            "name": "security_type_must_be_known",
            "text": "A detail record whose security type is not EQ, FI, MF or OP is rejected rather than loaded unrecognized.",
            "class": "ingestion",
            "citation": {"file": "Loader.java", "line": 42, "end_line": 46},
            "rejection_codes": ["L004"],
        },
        {
            "name": "option_quantity_is_contracts",
            "text": "An option position's stored quantity is the file's raw quantity divided by 100 and truncated, since options settle in contracts of 100 underlying shares.",
            "class": "business",
            "citation": {"file": "Loader.java", "line": 48, "end_line": 53},
            "candidate_tests": [{"description": "an OP position with quantity 250 stores as 2 contracts", "given": "security_type=OP, quantity=250", "expect": "stored quantity is 2"}],
        },
    ],
    embedded_sql=[
        {
            "citation": {"file": "Loader.java", "line": 65, "end_line": 68},
            "dialect": "tsql",
            "snippet": "INSERT INTO [dbo].[LoadAudit] ... SELECT TOP 1 ... GETDATE() ...",
            "reason": "SQL Server T-SQL audit insert; routed to SnowConvert AI, not parsed.",
        }
    ],
    unrecovered=[],
)


def _draft(extraction: Extraction = GOOD_EXTRACTION, sources=None):
    sources = sources if sources is not None else read_sources([SPLITTER, LOADER])
    return build_draft(extraction, sources, group="pershing_loader", owner=OWNER, clock=CLOCK)


# ---------------------------------------------------------------- reading source


def test_read_sources_reads_each_file_by_name():
    sources = read_sources([SPLITTER, LOADER])
    assert [s.path for s in sources] == ["Splitter.java", "Loader.java"]
    assert "class Splitter" in sources[0].text


def test_numbered_text_prefixes_every_line_with_its_number():
    source = SourceFile(path="X.java", text="a\nb\nc")
    text = _numbered_text(source)
    assert text.splitlines() == ["--- X.java ---", "1: a", "2: b", "3: c"]


def test_system_prompt_names_the_group_and_the_guardrails():
    text = system_prompt("pershing_loader")
    assert "pershing_loader" in text
    assert "rejection_codes" in text
    assert "embedded_sql" in text
    assert "never" in text.lower() and "confirmed" in text.lower()


# ---------------------------------------------------------------- deterministic backstops


def test_find_rejection_codes_finds_every_code_in_both_files():
    sources = read_sources([SPLITTER, LOADER])
    assert find_rejection_codes(sources) == ("L001", "L002", "L003", "L004")


def test_find_tsql_lines_finds_the_audit_insert_and_nothing_else():
    sources = read_sources([SPLITTER, LOADER])
    hits = find_tsql_lines(sources)
    lines = {(h["file"], h["line"]) for h in hits}
    assert ("Loader.java", 65) in lines and ("Loader.java", 66) in lines
    assert all(h["file"] == "Loader.java" for h in hits)  # Splitter.java has no SQL at all


def test_find_tsql_lines_is_empty_for_source_with_no_sql_idioms():
    sources = (SourceFile(path="X.java", text="public class X { int a = 1; }"),)
    assert find_tsql_lines(sources) == ()


# ---------------------------------------------------------------- build_draft / translation


def test_build_draft_translates_entries_with_code_citations_and_recovered_status():
    draft = _draft()
    assert len(draft.entries) == 5
    entry = draft.entries[0]
    assert entry.rule.id == "pershing_loader.unknown_record_type_rejected"
    assert entry.rule.status == "recovered"
    assert entry.rule.citation.kind == "code" and entry.rule.citation.file == "Splitter.java" and entry.rule.citation.line == 33
    assert entry.rule.history[-1].status == "recovered" and entry.rule.history[-1].by == "rule-recovery"
    assert draft.valid is True, [p.format() for p in entry.problems]


def test_build_draft_never_marks_an_entry_confirmed_even_if_the_raw_extraction_tries_to():
    """The tool schema has no status field at all, so a real model call cannot supply one --
    but this proves the guardrail is enforced in code, not merely absent from the schema."""
    sneaky = Extraction(
        entries=[{"name": "x", "text": "a rule text that is at least twenty characters long", "class": "business", "citation": {"file": "Loader.java", "line": 1}, "status": "confirmed"}],
        embedded_sql=[],
        unrecovered=[],
    )
    draft = _draft(sneaky)
    assert draft.entries[0].rule.status == "recovered"


def test_build_draft_dedupes_a_repeated_entry_name_within_one_group():
    extraction = Extraction(
        entries=[
            {"name": "dup", "text": "a rule text that is at least twenty characters long", "class": "business", "citation": {"file": "Loader.java", "line": 1}},
            {"name": "dup", "text": "a different rule text that is at least twenty characters", "class": "business", "citation": {"file": "Loader.java", "line": 2}},
        ],
        embedded_sql=[],
        unrecovered=[],
    )
    draft = _draft(extraction)
    ids = [e.rule.id for e in draft.entries]
    assert ids == ["pershing_loader.dup", "pershing_loader.dup_2"]


def test_build_draft_tags_rejection_codes_lowercased():
    draft = _draft()
    entry = next(e for e in draft.entries if e.rule.id.endswith("missing_account_rejected"))
    assert "rejection-l002" in entry.rule.tags


def test_build_draft_flags_a_schema_invalid_entry_never_silently_accepted():
    """A rule text under 20 characters is invalid against rule-v0; this proves the independent
    re-validation catches it even though the tool schema also enforces minLength."""
    extraction = Extraction(entries=[{"name": "too_short", "text": "too short", "class": "business", "citation": {"file": "Loader.java", "line": 1}}], embedded_sql=[], unrecovered=[])
    draft = _draft(extraction)
    assert draft.valid is False
    assert any("text" in p.message for p in draft.entries[0].problems), [p.format() for p in draft.entries[0].problems]


# ---------------------------------------------------------------- traceability and routing


def test_rejection_codes_traced_and_untraced_for_a_complete_extraction():
    draft = _draft()
    assert draft.rejection_codes_found == ("L001", "L002", "L003", "L004")
    assert draft.rejection_codes_traced == ("L001", "L002", "L003", "L004")
    assert draft.rejection_codes_untraced == ()


def test_rejection_codes_untraced_when_an_entry_is_missing():
    incomplete = Extraction(entries=GOOD_EXTRACTION.entries[:3], embedded_sql=GOOD_EXTRACTION.embedded_sql, unrecovered=[])
    draft = _draft(incomplete)
    assert draft.rejection_codes_untraced == ("L004",)
    assert draft.ok is False


def test_tsql_lines_unrouted_when_embedded_sql_is_missing():
    no_sql = Extraction(entries=GOOD_EXTRACTION.entries, embedded_sql=[], unrecovered=[])
    draft = _draft(no_sql)
    assert len(draft.tsql_lines_unrouted) > 0
    assert draft.ok is False


def test_ok_is_true_only_when_valid_and_fully_traced_and_fully_routed():
    assert _draft().ok is True


# ---------------------------------------------------------------- run()


def test_run_reads_the_sources_and_calls_the_client():
    client = FakeClient(extraction=GOOD_EXTRACTION)
    draft = run([SPLITTER, LOADER], client, group="pershing_loader", owner_name=OWNER.name, owner_email=OWNER.email)
    assert draft.ok is True
    assert len(client.calls) == 1
    assert [s.path for s in client.calls[0]["sources"]] == ["Splitter.java", "Loader.java"]


def test_run_raises_the_clients_error():
    client = FakeClient(error=RuleRecoveryError("boom"))
    with pytest.raises(RuleRecoveryError, match="boom"):
        run([SPLITTER, LOADER], client, group="pershing_loader", owner_name=OWNER.name, owner_email=OWNER.email)


# ---------------------------------------------------------------- report and files


def test_render_markdown_reports_traceability_and_routing():
    text = render_markdown(_draft())
    assert "4/4 traced" in text
    assert "None found." not in text  # embedded SQL section has one entry
    assert "Nothing was left unrecovered." in text


def test_render_markdown_calls_out_an_untraced_code_and_unrouted_sql():
    incomplete = Extraction(entries=GOOD_EXTRACTION.entries[:3], embedded_sql=[], unrecovered=[])
    text = render_markdown(_draft(incomplete))
    assert "Not traced to any entry: L004" in text
    assert "Not covered by any embedded_sql citation" in text


def test_write_draft_writes_one_rule_file_per_entry_and_a_report(tmp_path):
    draft = _draft()
    out_dir, report_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    rule_path = out_dir / "rules" / "pershing_loader" / "unknown_record_type_rejected.yaml"
    assert rule_path.exists()
    text = rule_path.read_text(encoding="utf-8")
    assert "status: recovered" in text and "id: pershing_loader.unknown_record_type_rejected" in text
    tests_path = out_dir / "candidate_tests.yaml"
    assert tests_path.exists() and "unknown_record_type_rejected" in tests_path.read_text(encoding="utf-8")


def test_write_draft_rule_files_load_clean_against_the_real_schema(tmp_path):
    """The draft files this agent writes are valid rule-v0 files, not just internally consistent."""
    from astra_knowledge.rules import load_rule_file

    draft = _draft()
    out_dir, _ = write_draft(draft, tmp_path / "out")
    rule_path = out_dir / "rules" / "pershing_loader" / "unknown_record_type_rejected.yaml"
    rule, problems = load_rule_file(rule_path, tmp_path)
    assert problems == [], [p.format() for p in problems]
    assert rule.status == "recovered"


# ---------------------------------------------------------------- CLI


def test_cli_runs_with_a_fake_client(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr(cli, "RuleRecoveryClient", lambda model=None, max_tokens=None: FakeClient(extraction=GOOD_EXTRACTION))
    out = tmp_path / "out"
    code = cli.main(["rule-recovery", "run", str(SPLITTER), str(LOADER), "--group", "pershing_loader", "--owner-name", OWNER.name, "--owner-email", OWNER.email, "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing_loader" / "report.md").exists()
    assert "ready to review: yes" in capsys.readouterr().out


def test_cli_exits_nonzero_when_a_rejection_code_is_untraced(tmp_path, monkeypatch):
    import astra_agents.cli as cli

    incomplete = Extraction(entries=GOOD_EXTRACTION.entries[:3], embedded_sql=GOOD_EXTRACTION.embedded_sql, unrecovered=[])
    monkeypatch.setattr(cli, "RuleRecoveryClient", lambda model=None, max_tokens=None: FakeClient(extraction=incomplete))
    code = cli.main(["rule-recovery", "run", str(SPLITTER), str(LOADER), "--group", "pershing_loader", "--owner-name", OWNER.name, "--owner-email", OWNER.email, "--out", str(tmp_path / "out")])
    assert code == 1


def test_cli_reports_a_start_error(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr(cli, "RuleRecoveryClient", lambda model=None, max_tokens=None: FakeClient(error=RuleRecoveryError("boom")))
    code = cli.main(["rule-recovery", "run", str(SPLITTER), str(LOADER), "--group", "pershing_loader", "--owner-name", OWNER.name, "--owner-email", OWNER.email, "--out", str(tmp_path / "out")])
    assert code == 2
    assert "boom" in capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_committed_example_satisfies_the_story_acceptance_criteria():
    """S5.4.1's three acceptance criteria, proven against the committed illustrative Splitter/
    Loader fixture with a complete, realistic extraction: every Loader rejection code traced,
    no entry confirmed, the embedded T-SQL routed rather than turned into a rule."""
    draft = _draft()
    assert draft.rejection_codes_untraced == ()  # every Loader rejection code traced to at least one entry
    assert all(e.rule.status == "recovered" for e in draft.entries)  # no entry marked confirmed by the agent
    assert draft.tsql_lines_unrouted == ()  # the T-SQL found is routed (embedded_sql), not parsed into a rule
    assert not any("GETDATE" in e.rule.text or "SELECT" in e.rule.text.upper() for e in draft.entries)
