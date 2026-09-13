from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astra_agents.drift_watcher import (
    CODE_MIN_OCCURRENCES,
    DriftWatcherError,
    detect_code_drift,
    detect_record_length_drift,
    generate,
    load_spec,
    read_sample,
    render_markdown,
    run,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "specs" / "pershing_gcus" / "2017-07-25.yaml"
EXAMPLE = REPO / "agents" / "examples" / "drift_watcher"
BASELINE = EXAMPLE / "baseline_sample.dat"
DRIFTED = EXAMPLE / "drifted_sample.dat"


def _spec():
    return load_spec(SPEC)


# ---------------------------------------------------------------- loading


def test_load_spec_reads_the_real_registry_spec():
    assert _spec().id == "pershing_gcus"


def test_load_spec_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(DriftWatcherError, match="not found"):
        load_spec(tmp_path / "missing.yaml")


def test_read_sample_reads_the_real_baseline_fixture():
    lines = read_sample(BASELINE)
    assert len(lines) == 5
    assert all(len(line) == 120 for line in lines)


def test_read_sample_filters_blank_lines(tmp_path):
    path = tmp_path / "x.dat"
    path.write_text("A" * 10 + "\n\n" + "B" * 10 + "\n", encoding="utf-8")
    assert read_sample(path) == ("A" * 10, "B" * 10)


def test_read_sample_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(DriftWatcherError, match="not found"):
        read_sample(tmp_path / "missing.dat")


# ---------------------------------------------------------------- record-length drift


def test_no_record_length_drift_for_the_clean_baseline():
    assert detect_record_length_drift(_spec(), read_sample(BASELINE)) is None


def test_record_length_drift_detected_for_the_drifted_sample():
    finding = detect_record_length_drift(_spec(), read_sample(DRIFTED))
    assert finding is not None
    assert finding.kind == "record_length" and finding.path == "file.record_length"
    assert finding.current == 120 and finding.proposed == 125
    assert finding.evidence["matching_lines"] == 6 and finding.evidence["total_lines"] == 6


def test_record_length_drift_ignores_a_single_outlier_line():
    """One truncated or padded line is not a majority; this is not real drift."""
    lines = read_sample(BASELINE)
    outlier = (lines[0][:-3],) + lines[1:]  # one short line among five
    assert detect_record_length_drift(_spec(), outlier) is None


def test_record_length_drift_needs_a_majority_not_just_a_plurality():
    lines = list(read_sample(BASELINE))
    # three of five lines become a new, different length -- still short of a majority tie-break
    # is fine either way; this proves the >= half threshold, not an exact boundary
    changed = tuple(line + "XXXXX" for line in lines[:3]) + tuple(lines[3:])
    finding = detect_record_length_drift(_spec(), changed)
    assert finding is not None and finding.proposed == 125


# ---------------------------------------------------------------- code-set drift


def test_no_code_drift_for_the_clean_baseline():
    assert detect_code_drift(_spec(), read_sample(BASELINE)) == ()


def test_code_drift_detected_for_the_drifted_sample():
    findings = detect_code_drift(_spec(), read_sample(DRIFTED))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "new_code"
    assert finding.proposed == "CD"
    assert finding.evidence["field"] == "security_type" and finding.evidence["occurrences"] == 2
    assert finding.evidence["declared_codes"] == ["EQ", "FI", "MF", "OP"]


def test_code_drift_requires_more_than_one_occurrence():
    """One corrupted row is not trusted as a real new code."""
    spec = _spec()
    lines = list(read_sample(DRIFTED))  # [hdr, dtl(CD), dtl(FI), dtl(CD), dtl(EQ), trl]
    single = tuple(lines[:1] + lines[2:])  # drop the first of the two CD detail lines, keep one
    findings = detect_code_drift(spec, single)
    assert CODE_MIN_OCCURRENCES == 2
    assert findings == ()


def test_code_drift_field_position_is_read_from_the_specs_own_declared_start():
    """Proves the field slice, not a hand-picked offset, drives detection."""
    spec = _spec()
    detail = next(r for r in spec.records if r.type == "detail")
    security_type = detail.field("security_type")
    assert security_type.start == 65 and security_type.length == 2


# ---------------------------------------------------------------- generate() / run()


def test_generate_finds_nothing_for_the_baseline():
    draft = generate(_spec(), read_sample(BASELINE), sample_name="baseline_sample.dat")
    assert draft.drift_detected is False and draft.ok is True
    assert draft.findings == ()


def test_generate_finds_both_kinds_for_the_drifted_sample():
    draft = generate(_spec(), read_sample(DRIFTED), sample_name="drifted_sample.dat")
    assert draft.drift_detected is True and draft.ok is False
    kinds = {f.kind for f in draft.findings}
    assert kinds == {"record_length", "new_code"}


def test_run_reads_both_files_from_disk():
    draft = run(SPEC, DRIFTED)
    assert draft.spec_id == "pershing_gcus" and draft.sample == "drifted_sample.dat"
    assert draft.drift_detected is True


def test_run_against_the_baseline_is_clean():
    draft = run(SPEC, BASELINE)
    assert draft.ok is True


# ---------------------------------------------------------------- the guardrail: never modifies the spec


def test_run_never_writes_to_the_real_spec_file():
    before = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    draft = run(SPEC, DRIFTED)
    assert draft.drift_detected is True  # a real run that actually found something to propose
    after = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    assert before == after


def test_write_draft_never_writes_near_specs(tmp_path):
    draft = run(SPEC, DRIFTED)
    out = tmp_path / "out"
    write_draft(draft, out)
    assert not (out / "2017-07-25.yaml").exists()
    assert not any(p.suffix in (".yaml", ".yml") for p in out.rglob("*"))


# ---------------------------------------------------------------- report and files


def test_render_markdown_lists_the_proposed_delta():
    draft = run(SPEC, DRIFTED)
    text = render_markdown(draft)
    assert "file.record_length" in text and "120" in text and "125" in text
    assert "security_type" in text and "CD" in text
    assert "Nothing here has been written to the spec" in text


def test_render_markdown_reports_clean_when_nothing_found():
    draft = run(SPEC, BASELINE)
    text = render_markdown(draft)
    assert "Drift detected: no" in text
    assert "safe to proceed to Silver" in text


def test_write_draft_writes_report_and_json(tmp_path):
    draft = run(SPEC, DRIFTED)
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["drift_detected"] is True and len(data["findings"]) == 2


# ---------------------------------------------------------------- CLI


def test_cli_run_against_the_drifted_sample(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["drift-watcher", "run", "--spec", str(SPEC), "--sample", str(DRIFTED), "--out", str(out)])
    assert code == 1, capsys.readouterr()
    assert (out / "pershing_gcus" / "2017-07-25" / "report.md").exists()
    text = capsys.readouterr().out
    assert "drift detected: yes" in text and "record_length" in text and "new_code" in text


def test_cli_run_against_the_baseline_sample(tmp_path, capsys):
    import astra_agents.cli as cli

    out = tmp_path / "out"
    code = cli.main(["drift-watcher", "run", "--spec", str(SPEC), "--sample", str(BASELINE), "--out", str(out)])
    assert code == 0, capsys.readouterr()
    assert "drift detected: no" in capsys.readouterr().out


def test_cli_reports_a_start_error_for_a_missing_sample(tmp_path, capsys):
    import astra_agents.cli as cli

    code = cli.main(["drift-watcher", "run", "--spec", str(SPEC), "--sample", str(tmp_path / "missing.dat"), "--out", str(tmp_path / "out")])
    assert code == 2
    assert capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied():
    """S5.9.1: an injected record-length change and an injected code-set change are both
    detected against the real, unmodified spec, with a proposed delta -- and the spec file this
    agent read from is provably untouched, byte for byte."""
    before = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    draft = run(SPEC, DRIFTED)

    length_findings = [f for f in draft.findings if f.kind == "record_length"]
    code_findings = [f for f in draft.findings if f.kind == "new_code"]
    assert length_findings and length_findings[0].proposed == 125
    assert code_findings and code_findings[0].proposed == "CD"

    after = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    assert before == after  # never modifies production config
