from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_agents.profiler import ProfilerError, load_spec, profile_lines, render_markdown, run, write_profile

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "agents" / "examples" / "profiler"

SPEC_YAML = """\
spec_version: 0
spec: {{ id: profiler_unit, version: v1, effective_from: 2026-01-01, file_type: position, custodians: [demo] }}
document: {{ title: Unit test layout, reference: illustrative }}
file: {{ format: {file_format}, record_length: 30 }}
records:
  - type: header
    match: {{ position: {{ start: 1, length: 3 }}, value: HDR }}
    fields:
      - {{ name: record_type, position: {{ start: 1, length: 3 }}, picture: X(3), type: code, citation: {{ page: 1 }}, codes: [{{ value: HDR, meaning: header }}] }}
      - {{ name: file_date, position: {{ start: 4, length: 8 }}, picture: 9(8), type: date, format: YYYYMMDD, citation: {{ page: 1 }} }}
      - {{ name: filler, position: {{ start: 12, length: 19 }}, picture: X(19), citation: {{ page: 1 }} }}
  - type: detail
    match: {{ position: {{ start: 1, length: 3 }}, value: DTL }}
    fields:
      - {{ name: record_type, position: {{ start: 1, length: 3 }}, picture: X(3), type: code, citation: {{ page: 2 }}, codes: [{{ value: DTL, meaning: detail }}] }}
      - {{ name: account, position: {{ start: 4, length: 10 }}, picture: X(10), required: true, citation: {{ page: 2 }} }}
      - {{ name: quantity, position: {{ start: 14, length: 8 }}, picture: 9(8), citation: {{ page: 2 }} }}
      - {{ name: status, position: {{ start: 22, length: 1 }}, picture: X(1), type: code, citation: {{ page: 2 }}, codes: [{{ value: A, meaning: active }}, {{ value: I, meaning: inactive }}] }}
      - {{ name: filler, position: {{ start: 23, length: 8 }}, picture: X(8), citation: {{ page: 2 }} }}
  - type: trailer
    match: {{ position: {{ start: 1, length: 3 }}, value: TRL }}
    fields:
      - {{ name: record_type, position: {{ start: 1, length: 3 }}, picture: X(3), type: code, citation: {{ page: 3 }}, codes: [{{ value: TRL, meaning: trailer }}] }}
      - {{ name: detail_count, position: {{ start: 4, length: 9 }}, picture: 9(9), citation: {{ page: 3 }} }}
      - {{ name: filler, position: {{ start: 13, length: 18 }}, picture: X(18), citation: {{ page: 3 }} }}
"""


def _write_spec(tmp_path: Path, *, file_format: str = "fixed_width") -> Path:
    folder = tmp_path / "specs" / "profiler_unit"
    folder.mkdir(parents=True)
    path = folder / "v1.yaml"
    path.write_text(SPEC_YAML.format(file_format=file_format), encoding="utf-8")
    return path


def hdr(file_date="20260101") -> str:
    return "HDR" + file_date + " " * 19


def dtl(account="ACC0000001", quantity="00012345", status="A") -> str:
    return "DTL" + account.ljust(10) + quantity.ljust(8) + status + " " * 8


def trl(count="000000002") -> str:
    return "TRL" + count + " " * 18


# ---------------------------------------------------------------- load_spec


def test_load_spec_reads_a_valid_fixed_width_spec(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    assert spec.id == "profiler_unit" and spec.format == "fixed_width"
    assert [r.label for r in spec.records] == ["header", "detail", "trailer"]


def test_load_spec_raises_a_clear_error_for_an_invalid_spec(tmp_path):
    path = _write_spec(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("id: profiler_unit", "id: something_else"), encoding="utf-8")
    with pytest.raises(ProfilerError, match="profiler_unit"):
        load_spec(path)


# ---------------------------------------------------------------- profile_lines


def test_profile_lines_reports_record_type_distribution(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    lines = [hdr(), dtl(), dtl(account="ACC0000002"), trl()]
    profile = profile_lines(spec, lines, document_reference="sample.dat")
    counts = {r.label: r.count for r in profile.records}
    assert counts == {"header": 1, "detail": 2, "trailer": 1}
    assert profile.lines == 4


def test_profile_lines_computes_null_rate_and_distinct_and_top_values(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    lines = [hdr(), dtl(status="A"), dtl(account="ACC0000002", status="A"), dtl(account="ACC0000003", status="I"), dtl(account="ACC0000004", status=" "), trl()]
    profile = profile_lines(spec, lines, document_reference="sample.dat")
    detail = next(r for r in profile.records if r.label == "detail")
    status = next(f for f in detail.fields if f.name == "status")
    assert status.observed == 4
    assert status.nulls == 1 and status.null_rate == 0.25
    assert status.distinct == 2
    assert dict(status.top_values) == {"A": 2, "I": 1}


def test_profile_lines_flags_a_field_whose_observed_type_disagrees_with_the_spec(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    lines = [hdr(), dtl(), dtl(account="ACC0000002", quantity="ABCDEFGH"), trl()]
    profile = profile_lines(spec, lines, document_reference="sample.dat")
    detail = next(r for r in profile.records if r.label == "detail")
    quantity = next(f for f in detail.fields if f.name == "quantity")
    assert quantity.type_ok is False
    assert quantity.type_disagreements == 1
    assert quantity.disagreement_examples and "not all digits" in quantity.disagreement_examples[0]
    assert ("detail", "quantity") in profile.flagged
    assert profile.ok is False


def test_profile_lines_flags_an_undeclared_code(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    lines = [hdr(), dtl(status="X"), trl()]
    profile = profile_lines(spec, lines, document_reference="sample.dat")
    detail = next(r for r in profile.records if r.label == "detail")
    status = next(f for f in detail.fields if f.name == "status")
    assert status.type_ok is False and "not a declared code" in status.disagreement_examples[0]


def test_profile_lines_does_not_count_a_required_blank_as_a_type_disagreement(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    lines = [hdr(), dtl(account=" " * 10), trl()]
    profile = profile_lines(spec, lines, document_reference="sample.dat")
    detail = next(r for r in profile.records if r.label == "detail")
    account = next(f for f in detail.fields if f.name == "account")
    assert account.nulls == 1
    assert account.type_ok is True  # blank-required is a completeness problem, not a type disagreement
    assert ("detail", "account") not in profile.flagged


def test_profile_lines_surfaces_a_missing_header_as_a_file_problem(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    profile = profile_lines(spec, [dtl(), trl()], document_reference="sample.dat")
    assert profile.problems and "no header record" in profile.problems[0]
    assert profile.ok is False


def test_profile_lines_surfaces_an_unmatched_line_as_a_record_problem(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    profile = profile_lines(spec, [hdr(), "XXX" + " " * 27, dtl(), trl()], document_reference="sample.dat")
    assert any("no record type matches" in p for p in profile.problems)
    assert profile.ok is False


def test_profile_lines_refuses_a_non_fixed_width_spec(tmp_path):
    path = tmp_path / "specs" / "delimited" / "v1.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "spec_version: 0\n"
        "spec: { id: delimited, version: v1, effective_from: 2026-01-01, file_type: position, custodians: [demo] }\n"
        "document: { title: t, reference: r }\n"
        "file: { format: delimited, delimiter: ',', column_count: 1 }\n"
        "records:\n"
        "  - type: detail\n"
        "    fields:\n"
        "      - { name: a, column: 1, type: string, citation: { page: 1 } }\n",
        encoding="utf-8",
    )
    spec = load_spec(path)
    with pytest.raises(ProfilerError, match="delimited"):
        profile_lines(spec, ["x"], document_reference="sample.csv")


# ---------------------------------------------------------------- run()


def test_run_reads_a_sample_file_from_disk(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    sample = tmp_path / "sample.dat"
    sample.write_text("\n".join([hdr(), dtl(), trl()]) + "\n", encoding="utf-8")
    profile = run(sample, spec)
    assert profile.document_reference == "sample.dat"
    assert profile.lines == 3


def test_run_raises_a_clear_error_for_a_missing_sample_file(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    with pytest.raises(ProfilerError, match="not found"):
        run(tmp_path / "missing.dat", spec)


# ---------------------------------------------------------------- report


def test_render_markdown_reports_record_types_and_flags(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    profile = profile_lines(spec, [hdr(), dtl(quantity="ABCDEFGH"), trl()], document_reference="sample.dat")
    text = render_markdown(profile)
    assert "Drift found: yes" in text
    assert "| detail | 1 |" in text
    assert "**MISMATCH**" in text
    assert "Flagged:" in text and "quantity" in text


def test_write_profile_writes_report_and_json(tmp_path):
    spec = load_spec(_write_spec(tmp_path))
    profile = profile_lines(spec, [hdr(), dtl(), trl()], document_reference="sample.dat")
    report_path, data_path = write_profile(profile, tmp_path / "out")
    assert report_path.exists() and data_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["spec_id"] == "profiler_unit" and data["ok"] is True


# ---------------------------------------------------------------- CLI


def test_cli_profiler_run_reports_no_drift_for_a_clean_sample(tmp_path, capsys):
    import astra_agents.cli as cli

    spec_path = _write_spec(tmp_path)
    sample = tmp_path / "sample.dat"
    sample.write_text("\n".join([hdr(), dtl(), trl()]) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["profiler", "run", str(sample), "--spec", str(spec_path), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert (out / "profiler_unit" / "v1" / "report.md").exists()
    assert "drift: no" in capsys.readouterr().out


def test_cli_profiler_run_exits_nonzero_when_drift_is_found(tmp_path):
    import astra_agents.cli as cli

    spec_path = _write_spec(tmp_path)
    sample = tmp_path / "sample.dat"
    sample.write_text("\n".join([hdr(), dtl(quantity="ABCDEFGH"), trl()]) + "\n", encoding="utf-8")
    code = cli.main(["profiler", "run", str(sample), "--spec", str(spec_path), "--out", str(tmp_path / "out")])
    assert code == 1


def test_cli_profiler_run_reports_a_start_error_for_a_bad_spec_path(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["profiler", "run", str(tmp_path / "sample.dat"), "--spec", str(tmp_path / "missing.yaml"), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the committed 750-char example


def test_example_spec_and_sample_satisfy_the_story_acceptance_criteria():
    """S5.2.1's own acceptance criteria, proven against the committed example fixture rather than a
    private document: a profile is produced for a 750-char fixed-width sample, and the field with a
    deliberately corrupted value (and the undeclared security_type code) are flagged."""
    spec = load_spec(EXAMPLE / "specs" / "profiler_demo" / "2026-01-01.yaml")
    assert spec.record_length == 750
    profile = run(EXAMPLE / "sample.dat", spec)
    assert profile.lines == 7
    assert profile.ok is False
    assert ("detail", "quantity") in profile.flagged
    assert ("detail", "security_type") in profile.flagged
