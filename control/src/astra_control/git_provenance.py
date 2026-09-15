"""Git as system of record: every approved change committed with a real `PROVENANCE.json`, so no
artifact exists only in the factory's own local file stores (S6.2.2, ADR 0071, product spec's own
"Git is the system of record for everything generated... A client can leave with a repository
that runs without the factory," and the README's own "Every generated artifact and every approval
lands here").

**This is the first module in this whole codebase that actually mutates git history.** Every
prior "this creates a change" story (`astra_control.config_studio.request_promotion`,
`astra_control.drift_review.approve`, `astra_control.autonomy_admin.set_level`, `astra_control.
approvals.approve`) deliberately stopped at a local log file — each of *their own* changes still
needed a human's review before landing in git, so none of them could honestly commit anything.
This module is different: it runs only *after* an `astra_control.approvals.Approval` record
already exists — the human decision is already made (S6.2.1's own job); committing it is a
mechanical, deterministic recording step, not a second judgment call. That is the one thing that
makes actually invoking `git add`/`git commit` appropriate here, where it was not appropriate
anywhere else in this plane. Nothing here ever runs against this repository's own real git history
in a test or a runbook example — every one targets a disposable, scratch repository, created and
destroyed for that one check, the same discipline this session applies to every other action with
a real, external effect.

`PROVENANCE.json` unifies the two real, independently hand-rolled shapes already in this codebase
(`astra_data.render.write_bundle`, `astra_data.migration._write_run`) rather than inventing a
third one: both already use a `{"files": {relative_path: sha256}}` digest map and
`json.dumps(..., indent=2, sort_keys=True) + "\n"` — this module's own `PROVENANCE.json` keeps
that shape, with `"approval"` carrying the real `Approval` record (who, when, evidence, agent
version, autonomy level) in place of either existing module's own free-form `"inputs"`.

**"One commit per approval" only ever stages the exact artifact paths given, never `git add -A`
or `.`, and refuses outright if the repository already has anything else staged** — a caller's own
in-progress work is never swept into this commit, and `git commit`'s own pathspec is given a
second time as a belt-and-suspenders measure, so the commit it produces can never include more
than the artifacts and the provenance file this call itself just staged.

**"No artifact exists only in the factory database" is `verify_committed` — a real check, not an
aspiration**: given a list of artifact paths, it names exactly which ones git does not track at
all (`git ls-files`), the concrete, testable meaning of the product spec's own claim.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from astra_control.approvals import Approval


class GitProvenanceError(RuntimeError):
    pass


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        raise GitProvenanceError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve())).replace("\\", "/")
    except ValueError:
        raise GitProvenanceError(f"{path} is not inside the repo {repo}") from None


# -- PROVENANCE.json: real files, real digests, the real approval record -------------------------


@dataclass(frozen=True)
class ProvenanceRecord:
    approval: Approval
    files: dict[str, str]  # relative path -> sha256

    def to_dict(self) -> dict:
        return {"approval": self.approval.to_dict(), "files": dict(self.files)}


def build_provenance(approval: Approval, artifact_paths: tuple[Path, ...], repo: Path) -> ProvenanceRecord:
    """Every artifact path must be a real file inside `repo` — refuses outright, nothing written,
    if any is missing or outside it (never a provenance record for something that does not
    exist)."""
    files: dict[str, str] = {}
    for p in artifact_paths:
        p = Path(p)
        if not p.is_file():
            raise GitProvenanceError(f"artifact not found: {p}")
        files[_relative(p, repo)] = _digest(p)
    return ProvenanceRecord(approval=approval, files=files)


def write_provenance(record: ProvenanceRecord, path: Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


# -- AC1: one commit per approval, with PROVENANCE.json -------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    files: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"sha": self.sha, "message": self.message, "files": list(self.files)}


def commit_approval(
    repo: Path,
    approval: Approval,
    artifact_paths: tuple[Path, ...],
    *,
    provenance_path: Path,
    message: str | None = None,
) -> Commit:
    """Writes `PROVENANCE.json`, stages exactly the given artifact paths plus it, and commits —
    refuses outright, nothing staged or committed, when an artifact is missing or outside the
    repo, or the repo already has something else staged (module docstring)."""
    repo = Path(repo)
    if not (repo / ".git").is_dir():
        raise GitProvenanceError(f"not a git repository: {repo}")

    already_staged = _run_git(repo, "diff", "--cached", "--name-only")
    if already_staged:
        raise GitProvenanceError(f"{repo} already has staged changes not from this call, refusing to commit: {already_staged}")

    record = build_provenance(approval, artifact_paths, repo)
    write_provenance(record, provenance_path)

    paths_to_add = tuple(_relative(p, repo) for p in artifact_paths) + (_relative(provenance_path, repo),)
    _run_git(repo, "add", "--", *paths_to_add)

    message = message or f"Approve {approval.subject} ({approval.agent}.{approval.task_class})\n\nApproved by {approval.approved_by} at {approval.at}."
    _run_git(repo, "commit", "-m", message, "--", *paths_to_add)
    sha = _run_git(repo, "rev-parse", "HEAD")
    return Commit(sha=sha, message=message, files=paths_to_add)


# -- AC2: no artifact exists only in the factory database ------------------------------------------


def verify_committed(repo: Path, artifact_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Every one of `artifact_paths` git does not actually track — empty means every one already
    has a home in git, not just in a local factory store."""
    repo = Path(repo)
    tracked = set(_run_git(repo, "ls-files").splitlines())
    untracked = []
    for p in artifact_paths:
        p = Path(p)
        try:
            rel = _relative(p, repo)
        except GitProvenanceError:
            untracked.append(p)
            continue
        if rel not in tracked:
            untracked.append(p)
    return tuple(untracked)


# -- the view --------------------------------------------------------------------------


def render_commit_markdown(commit: Commit) -> str:
    out = [f"# Commit {commit.sha[:12]}", ""]
    out.append(commit.message)
    out.append("")
    out.append("Files:")
    for f in commit.files:
        out.append(f"- `{f}`")
    out.append("")
    return "\n".join(out)


def render_verify_markdown(untracked: tuple[Path, ...]) -> str:
    out = ["# Git as system of record: verification", ""]
    if not untracked:
        out += ["Every artifact given is tracked by git. Nothing exists only in the factory database.", ""]
        return "\n".join(out)
    out.append("Exists only outside git (not tracked):")
    for p in untracked:
        out.append(f"- `{p}`")
    out.append("")
    return "\n".join(out)
