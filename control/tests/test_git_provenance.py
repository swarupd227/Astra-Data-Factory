from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from astra_control.approvals import Approval
from astra_control.git_provenance import (
    Commit,
    GitProvenanceError,
    build_provenance,
    commit_approval,
    render_commit_markdown,
    render_verify_markdown,
    verify_committed,
    write_provenance,
)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


def _scratch_repo(tmp_path: Path) -> Path:
    """A real, disposable git repository -- created and destroyed for this one test, never this
    session's own project repository (module docstring)."""
    repo = tmp_path / "scratch_repo"
    repo.mkdir()
    assert _run(repo, "init", "-q").returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test").returncode == 0
    (repo / "README.md").write_text("scratch\n", encoding="utf-8")
    assert _run(repo, "add", "README.md").returncode == 0
    assert _run(repo, "commit", "-q", "-m", "initial commit").returncode == 0
    return repo


def _approval(**overrides) -> Approval:
    fields = dict(
        subject="pershing_gcus.quantity_sign",
        agent="rule_recovery",
        task_class="rule_status",
        autonomy_level="L2",
        approved_by="steward@example.com",
        at="2026-09-15T09:00:00Z",
        evidence_path=None,
        agent_version="claude-sonnet-5",
        comment="Matches the sample.",
    )
    fields.update(overrides)
    return Approval(**fields)


# ---------------------------------------------------------------- build_provenance / write_provenance


def test_build_provenance_digests_a_real_file(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "rules" / "rule.yaml"
    artifact.parent.mkdir()
    artifact.write_bytes(b"rule_version: 0\n")  # exact bytes, so the asserted digest matches on every platform

    record = build_provenance(_approval(), (artifact,), repo)
    assert record.files == {"rules/rule.yaml": hashlib.sha256(b"rule_version: 0\n").hexdigest()}
    assert record.approval.subject == "pershing_gcus.quantity_sign"


def test_build_provenance_refuses_a_missing_artifact(tmp_path):
    repo = _scratch_repo(tmp_path)
    with pytest.raises(GitProvenanceError, match="not found"):
        build_provenance(_approval(), (repo / "nope.yaml",), repo)


def test_build_provenance_refuses_an_artifact_outside_the_repo(tmp_path):
    repo = _scratch_repo(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(GitProvenanceError, match="not inside the repo"):
        build_provenance(_approval(), (outside,), repo)


def test_write_provenance_writes_real_json(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "a.txt"
    artifact.write_text("x", encoding="utf-8")
    record = build_provenance(_approval(), (artifact,), repo)
    path = write_provenance(record, tmp_path / "PROVENANCE.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["approval"]["subject"] == "pershing_gcus.quantity_sign"
    assert "a.txt" in data["files"]


# ---------------------------------------------------------------- AC1: one commit per approval, with PROVENANCE.json


def test_commit_approval_creates_a_real_commit(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "rules" / "pershing.yaml"
    artifact.parent.mkdir()
    artifact.write_text("rule_version: 0\n", encoding="utf-8")

    before_head = _run(repo, "rev-parse", "HEAD").stdout.strip()
    commit = commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")

    assert commit.sha != before_head
    assert _run(repo, "rev-parse", "HEAD").stdout.strip() == commit.sha
    assert "rules/pershing.yaml" in commit.files
    assert "PROVENANCE.json" in commit.files


def test_commit_approval_actually_stages_and_commits_both_files(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "config.yaml"
    artifact.write_text("x: 1\n", encoding="utf-8")
    commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")

    tracked = _run(repo, "ls-files").stdout.splitlines()
    assert "config.yaml" in tracked and "PROVENANCE.json" in tracked
    show = _run(repo, "show", "--stat", "HEAD").stdout
    assert "config.yaml" in show and "PROVENANCE.json" in show


def test_commit_approval_message_names_the_subject_and_approver(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    commit = commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")
    assert "pershing_gcus.quantity_sign" in commit.message
    assert "steward@example.com" in commit.message


def test_commit_approval_with_a_custom_message(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    commit = commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json", message="Custom message")
    assert commit.message == "Custom message"
    log = _run(repo, "log", "-1", "--pretty=%B").stdout
    assert "Custom message" in log


def test_commit_approval_refuses_when_something_else_is_already_staged(tmp_path):
    repo = _scratch_repo(tmp_path)
    (repo / "unrelated.txt").write_text("unrelated", encoding="utf-8")
    _run(repo, "add", "unrelated.txt")

    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    with pytest.raises(GitProvenanceError, match="already has staged changes"):
        commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")

    # refused outright: the pre-existing staged file is untouched, no new commit made
    assert _run(repo, "diff", "--cached", "--name-only").stdout.strip() == "unrelated.txt"


def test_commit_approval_never_commits_unrelated_dirty_files(tmp_path):
    """Only the given artifact paths are ever staged or committed -- an unrelated dirty
    (unstaged) file in the working tree is never swept in."""
    repo = _scratch_repo(tmp_path)
    (repo / "unrelated.txt").write_text("dirty, never staged", encoding="utf-8")

    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    commit = commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")

    assert "unrelated.txt" not in commit.files
    show = _run(repo, "show", "--stat", "HEAD").stdout
    assert "unrelated.txt" not in show
    # still dirty, untouched, in the working tree
    assert "unrelated.txt" in _run(repo, "status", "--porcelain").stdout


def test_commit_approval_refuses_a_missing_artifact_and_writes_nothing(tmp_path):
    repo = _scratch_repo(tmp_path)
    before = _run(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(GitProvenanceError, match="not found"):
        commit_approval(repo, _approval(), (repo / "nope.yaml",), provenance_path=repo / "PROVENANCE.json")
    assert _run(repo, "rev-parse", "HEAD").stdout.strip() == before  # no new commit
    assert not (repo / "PROVENANCE.json").exists()


def test_commit_approval_refuses_a_non_git_directory(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    artifact = not_a_repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    with pytest.raises(GitProvenanceError, match="not a git repository"):
        commit_approval(not_a_repo, _approval(), (artifact,), provenance_path=not_a_repo / "PROVENANCE.json")


def test_commit_approval_provenance_json_references_the_real_approval(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    commit_approval(repo, _approval(agent_version="claude-sonnet-5"), (artifact,), provenance_path=repo / "PROVENANCE.json")

    data = json.loads((repo / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert data["approval"]["agent_version"] == "claude-sonnet-5"
    assert data["approval"]["autonomy_level"] == "L2"
    assert "x.yaml" in data["files"]


def test_two_approvals_are_two_separate_commits(tmp_path):
    repo = _scratch_repo(tmp_path)
    a1 = repo / "a1.yaml"
    a1.write_text("1", encoding="utf-8")
    c1 = commit_approval(repo, _approval(subject="s1"), (a1,), provenance_path=repo / "PROVENANCE.json")

    a2 = repo / "a2.yaml"
    a2.write_text("2", encoding="utf-8")
    c2 = commit_approval(repo, _approval(subject="s2"), (a2,), provenance_path=repo / "PROVENANCE.json")

    assert c1.sha != c2.sha
    log = _run(repo, "log", "--oneline").stdout
    assert log.count("\n") >= 2  # initial + 2 approval commits


# ---------------------------------------------------------------- AC2: no artifact exists only in the factory database


def test_verify_committed_with_everything_tracked_is_empty(tmp_path):
    repo = _scratch_repo(tmp_path)
    artifact = repo / "x.yaml"
    artifact.write_text("x", encoding="utf-8")
    commit_approval(repo, _approval(), (artifact,), provenance_path=repo / "PROVENANCE.json")
    assert verify_committed(repo, (artifact, repo / "PROVENANCE.json")) == ()


def test_verify_committed_names_an_untracked_artifact(tmp_path):
    repo = _scratch_repo(tmp_path)
    only_local = repo / "local_only.yaml"
    only_local.write_text("never committed", encoding="utf-8")
    untracked = verify_committed(repo, (only_local,))
    assert untracked == (only_local,)


def test_verify_committed_mix_of_tracked_and_untracked(tmp_path):
    repo = _scratch_repo(tmp_path)
    committed = repo / "committed.yaml"
    committed.write_text("x", encoding="utf-8")
    commit_approval(repo, _approval(), (committed,), provenance_path=repo / "PROVENANCE.json")

    local_only = repo / "local_only.yaml"
    local_only.write_text("y", encoding="utf-8")

    untracked = verify_committed(repo, (committed, local_only))
    assert untracked == (local_only,)


def test_verify_committed_a_path_outside_the_repo_is_reported_untracked(tmp_path):
    repo = _scratch_repo(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("x", encoding="utf-8")
    assert verify_committed(repo, (outside,)) == (outside,)


# ---------------------------------------------------------------- render_*


def test_render_commit_markdown_shows_the_message_and_files():
    commit = Commit(sha="abc123def456", message="Approve x\n\nApproved by steward@example.com.", files=("rules/x.yaml", "PROVENANCE.json"))
    text = render_commit_markdown(commit)
    assert "abc123def456"[:12] in text
    assert "rules/x.yaml" in text and "PROVENANCE.json" in text


def test_render_verify_markdown_with_nothing_untracked_says_so():
    assert "Nothing exists only in the factory database." in render_verify_markdown(()) or "tracked by git" in render_verify_markdown(())


def test_render_verify_markdown_lists_untracked_paths(tmp_path):
    text = render_verify_markdown((tmp_path / "a.yaml", tmp_path / "b.yaml"))
    assert "a.yaml" in text and "b.yaml" in text


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_story_acceptance_criteria_are_satisfied(tmp_path):
    """AC1: approving a change produces exactly one real git commit with a real PROVENANCE.json
    referencing the real approval. AC2: verify_committed proves an artifact is not only in the
    factory's own local store, against a real disposable git repository."""
    repo = _scratch_repo(tmp_path)
    artifact = repo / "rules" / "pershing_gcus" / "quantity_sign.yaml"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("rule_version: 0\n", encoding="utf-8")

    approval = _approval()
    before_head = _run(repo, "rev-parse", "HEAD").stdout.strip()
    commit = commit_approval(repo, approval, (artifact,), provenance_path=repo / "PROVENANCE.json")

    assert commit.sha != before_head  # AC1: a real, new commit
    provenance = json.loads((repo / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["approval"]["approved_by"] == "steward@example.com"  # AC1: real provenance

    assert verify_committed(repo, (artifact, repo / "PROVENANCE.json")) == ()  # AC2: nothing only local

    local_only = repo / "not_committed.yaml"
    local_only.write_text("x", encoding="utf-8")
    assert verify_committed(repo, (local_only,)) == (local_only,)  # AC2: caught when it IS only local
