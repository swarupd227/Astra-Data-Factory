from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_control.diff_review import DiffReview
from astra_control.drift_review import (
    ChangeRequest,
    DriftReview,
    DriftReviewError,
    apply_drift_findings,
    approve,
    load_change_requests,
    render_change_requests_markdown,
    render_markdown,
    review,
)
from astra_control.spec_viewer import load_registry, load_spec

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"
RULES = REPO / "rules"
DOMAINS = REPO / "domains"
DRIFT_REPORT = REPO / "control" / "examples" / "queue" / "drift-watcher" / "pershing_gcus" / "2017-07-25" / "report.json"
OLD_CONFIG = REPO / "control" / "examples" / "diff_review" / "before" / "pershing_position.yaml"
NEW_CONFIG = REPO / "configs" / "examples" / "pershing_position.yaml"

SPEC_ID = "pershing_gcus"
SPEC_VERSION = "2017-07-25"


def _old_spec():
    registry = load_registry(SPECS, REPO)
    return load_spec(registry, SPEC_ID, SPEC_VERSION)


def _findings():
    return tuple(json.loads(DRIFT_REPORT.read_text(encoding="utf-8"))["findings"])


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- apply_drift_findings


def test_apply_record_length_updates_file_record_length():
    old = _old_spec()
    assert old.record_length == 120
    finding = ({"kind": "record_length", "path": "file.record_length", "current": 120, "proposed": 125},)
    new = apply_drift_findings(old, finding)
    assert new.record_length == 125
    assert old.record_length == 120  # the original is never mutated


def test_apply_new_code_adds_the_code_to_the_named_field():
    old = _old_spec()
    detail = old.record("detail")
    assert detail.field("security_type").codes == (("EQ", "equity"), ("FI", "fixed income"), ("MF", "mutual fund"), ("OP", "option"))
    finding = ({"kind": "new_code", "path": "records[detail].fields[security_type].codes", "current": ["EQ", "FI", "MF", "OP"], "proposed": "CD"},)
    new = apply_drift_findings(old, finding)
    new_codes = new.record("detail").field("security_type").codes
    assert ("CD", "(drift review: new code observed, not yet described)") in new_codes
    assert len(new_codes) == 5


def test_apply_both_real_findings_together():
    new = apply_drift_findings(_old_spec(), _findings())
    assert new.record_length == 125
    assert any(c[0] == "CD" for c in new.record("detail").field("security_type").codes)


def test_apply_unknown_kind_is_refused():
    with pytest.raises(DriftReviewError, match="not a drift finding kind"):
        apply_drift_findings(_old_spec(), ({"kind": "reordered_fields", "path": "x", "current": None, "proposed": None},))


def test_apply_record_length_with_wrong_path_is_refused():
    with pytest.raises(DriftReviewError, match="file.record_length"):
        apply_drift_findings(_old_spec(), ({"kind": "record_length", "path": "somewhere.else", "current": 120, "proposed": 125},))


def test_apply_new_code_with_malformed_path_is_refused():
    with pytest.raises(DriftReviewError, match="records\\[<record>\\]"):
        apply_drift_findings(_old_spec(), ({"kind": "new_code", "path": "detail.security_type.codes", "current": [], "proposed": "CD"},))


def test_apply_new_code_unknown_record_is_refused():
    with pytest.raises(DriftReviewError, match="no record"):
        apply_drift_findings(_old_spec(), ({"kind": "new_code", "path": "records[nope].fields[security_type].codes", "current": [], "proposed": "CD"},))


def test_apply_new_code_unknown_field_is_refused():
    with pytest.raises(DriftReviewError, match="no field"):
        apply_drift_findings(_old_spec(), ({"kind": "new_code", "path": "records[detail].fields[nope].codes", "current": [], "proposed": "CD"},))


# ---------------------------------------------------------------- review(): AC1 spec diff


def test_review_against_the_real_drift_report_and_registry():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert result.spec_id == SPEC_ID
    assert result.spec_version == SPEC_VERSION
    assert result.sample == "drifted_sample.dat"
    assert len(result.findings) == 2


def test_review_spec_diff_shows_the_new_code_as_a_field_change():
    """compare() only diffs fields, so only the new_code finding shows here -- record_length
    (a file-level attribute) never appears in spec_diff, only under findings (module docstring)."""
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert len(result.spec_diff) == 1
    d = result.spec_diff[0]
    assert d.record == "detail" and d.field == "security_type" and d.kind == "changed"


def test_review_missing_drift_report_is_a_clear_error(tmp_path):
    with pytest.raises(DriftReviewError, match="not found"):
        review(tmp_path / "missing.json", specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


def test_review_unknown_spec_in_the_report_is_a_clear_error(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"spec_id": "nope", "spec_version": "1999-01-01", "sample": "x", "findings": []}), encoding="utf-8")
    with pytest.raises(DriftReviewError, match="no spec"):
        review(path, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)


# ---------------------------------------------------------------- review(): AC1 config diff (optional)


def test_review_with_no_config_paths_has_no_config_diff():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert result.config_diff is None


def test_review_with_config_paths_delegates_to_diff_review_directly():
    """Not causally related to this specific drift -- proves the mechanism: when a caller already
    has two real config files, astra_control.diff_review.review is called directly, unmodified."""
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, old_config=OLD_CONFIG, new_config=NEW_CONFIG, root=REPO)
    assert isinstance(result.config_diff, DiffReview)
    assert result.config_diff.changed


def test_review_config_diff_error_is_wrapped(tmp_path):
    with pytest.raises(DriftReviewError):
        review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, old_config=tmp_path / "missing.yaml", new_config=NEW_CONFIG, root=REPO)


# ---------------------------------------------------------------- AC2: impact list


def test_affected_custodians_is_the_spec_s_own_field():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert result.affected_custodians == ("pershing",)


def test_consumers_default_to_empty_not_tracked():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert result.consumers == ()


def test_consumers_when_given():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO, consumers=("tamarac", "reporting-warehouse"))
    assert result.consumers == ("tamarac", "reporting-warehouse")


# ---------------------------------------------------------------- AC3: approve, never git, never prod


def test_approve_appends_a_change_request(tmp_path):
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    path = tmp_path / "drift-change-requests.yaml"

    updated = approve(result, path, approved_by="engineer@example.com", note="Widened record length and added CD.", at=_at("2026-09-15T09:00:00Z"))

    assert len(updated) == 1
    assert updated[0] == ChangeRequest(SPEC_ID, SPEC_VERSION, "engineer@example.com", "Widened record length and added CD.", "2026-09-15T09:00:00Z")
    reloaded = load_change_requests(path)
    assert reloaded == updated


def test_approve_appends_to_existing_requests(tmp_path):
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    path = tmp_path / "drift-change-requests.yaml"
    approve(result, path, approved_by="engineer@example.com", at=_at("2026-09-15T09:00:00Z"))
    updated = approve(result, path, approved_by="steward@example.com", at=_at("2026-09-15T10:00:00Z"))
    assert len(updated) == 2
    assert [r.approved_by for r in updated] == ["engineer@example.com", "steward@example.com"]


def test_approve_refuses_blank_approved_by(tmp_path):
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    path = tmp_path / "drift-change-requests.yaml"
    with pytest.raises(DriftReviewError, match="approved_by"):
        approve(result, path, approved_by="   ")
    assert not path.exists()  # refused outright, nothing written


def test_approve_never_writes_to_specs_or_configs(tmp_path):
    """AC3's own "never touches prod" -- structurally, approve only ever writes the one path it
    is given; the real specs/ tree is untouched, byte for byte."""
    spec_path = SPECS / SPEC_ID / f"{SPEC_VERSION}.yaml"
    before = spec_path.read_bytes()
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    approve(result, tmp_path / "drift-change-requests.yaml", approved_by="engineer@example.com", at=_at("2026-09-15T09:00:00Z"))
    assert spec_path.read_bytes() == before


def test_load_change_requests_with_no_path_is_empty():
    assert load_change_requests(None) == ()


def test_load_change_requests_missing_file_is_empty(tmp_path):
    assert load_change_requests(tmp_path / "missing.yaml") == ()


# ---------------------------------------------------------------- render_*


def test_render_markdown_shows_findings_diff_and_impact():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO, consumers=("tamarac",))
    text = render_markdown(result)
    assert "record_length" in text and "new_code" in text
    assert "security_type" in text
    assert "pershing" in text and "tamarac" in text


def test_render_markdown_no_config_diff_says_so():
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    assert "No config diff given" in render_markdown(result)


def test_render_change_requests_markdown_with_none_says_so():
    assert "No approvals recorded yet." in render_change_requests_markdown(())


def test_render_change_requests_markdown_shows_every_entry(tmp_path):
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, root=REPO)
    path = tmp_path / "drift-change-requests.yaml"
    updated = approve(result, path, approved_by="engineer@example.com", at=_at("2026-09-15T09:00:00Z"))
    text = render_change_requests_markdown(updated)
    assert "engineer@example.com" in text and SPEC_ID in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: a real spec diff (and, when given, a config diff via diff_review.review directly).
    AC2: affected custodians (the spec's own field) and consumers (caller-supplied, honestly
    empty otherwise). AC3: approve appends a non-prod change request; specs/ and configs/ are
    never touched, and nothing here calls git."""
    result = review(DRIFT_REPORT, specs_dir=SPECS, rules_dir=RULES, domains_dir=DOMAINS, old_config=OLD_CONFIG, new_config=NEW_CONFIG, root=REPO, consumers=("tamarac",))

    assert result.spec_diff  # AC1: spec diff
    assert result.config_diff is not None  # AC1: config diff, when given
    assert result.affected_custodians == ("pershing",)  # AC2
    assert result.consumers == ("tamarac",)  # AC2

    path = tmp_path / "drift-change-requests.yaml"
    updated = approve(result, path, approved_by="engineer@example.com", at=_at("2026-09-15T09:00:00Z"))
    assert len(updated) == 1  # AC3: a real, loadable change request
    assert path.is_file()
