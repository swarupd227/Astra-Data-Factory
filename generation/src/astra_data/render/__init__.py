"""Rendering a compiled config into a release bundle (S3.1.2).

One command renders every artifact for a config into `releases/<bundle>/`:

  manifest.yaml               the bundle contract (steps in order, tests)
  ddl/bronze_<source>.sql     typed Bronze tables for the source's logical
                              records, its file registry and its problems
  pipeline/<source>_*.sql     the lines view, the intake procedure, the
                              process procedure that runs the stages, tasks
  dq/dmf_<source>.sql         data metric functions on the Bronze tables
  tests/*.sql                 queries that return failing rows
  docs/<source>.md            the source, its layout, mappings and rules
  atlan/<source>.json         catalog assets and lineage for Atlan
  PROVENANCE.json             which inputs, at which digests, produced it

Renderers are per target profile and take only the compiled config, so
re-rendering an unchanged config gives byte-identical output: nothing
here reads a clock, the environment, or anything outside the compiled
model. Later stories add stages (parse, merge, resolution, DQ rules) to
the process procedure; the layout does not change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from astra_core.problems import Problem
from astra_data.compiler import CompiledConfig
from astra_data.render import atlan, bronze, docs, dq, tasks, tests
from astra_data.render.names import bundle_name

Renderer = Callable[[CompiledConfig], dict[str, str]]


def render_snowflake_iceberg(compiled: CompiledConfig) -> dict[str, str]:
    files: dict[str, str] = {}
    files.update(bronze.render(compiled))
    files.update(tasks.render(compiled))
    files.update(dq.render(compiled))
    files.update(tests.render(compiled))
    files.update(docs.render(compiled))
    files.update(atlan.render(compiled))
    return files


RENDERERS: dict[str, Renderer] = {"snowflake_iceberg": render_snowflake_iceberg}


class RenderError(ValueError):
    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.format() for p in problems))


def render_bundle(compiled: CompiledConfig) -> dict[str, str]:
    """Every file of the bundle, keyed by path relative to the bundle directory."""
    renderer = RENDERERS.get(compiled.profile.id)
    if renderer is None:
        raise RenderError([Problem(compiled.provenance["config"]["path"], None, f"no renderers for target profile '{compiled.profile.id}'; profiles with renderers are {', '.join(sorted(RENDERERS))}")])
    problems = bronze.problems(compiled)
    if problems:
        raise RenderError(problems)
    files = renderer(compiled)

    digests = {name: _sha(text) for name, text in sorted(files.items())}
    version = hashlib.sha256("".join(f"{name}\0{digest}\n" for name, digest in digests.items()).encode("utf-8")).hexdigest()[:12]
    steps = [name for name in files if name.startswith(("ddl/", "pipeline/", "dq/")) and name.endswith(".sql")]
    steps.sort(key=lambda name: (0 if name.startswith("ddl/") else 2 if name.startswith("dq/") else 1, tasks.STEP_ORDER.get(name.rsplit("_", 1)[-1], 9), name))
    manifest = "\n".join(
        [
            f"# Release bundle of source {compiled.id}, rendered by astra-data render from {compiled.provenance['config']['path']}.",
            "# The version is a digest of the rendered files. Do not edit; change the config and re-render.",
            f"bundle: {bundle_name(compiled)}",
            f'version: "{version}"',
            f"source: {compiled.id}",
            "steps:",
            *[f"  - {step}" for step in steps],
            "tests:",
            "  - tests/*.sql",
            "",
        ]
    )
    files["manifest.yaml"] = manifest
    digests["manifest.yaml"] = _sha(manifest)
    provenance = {
        "bundle": {"name": bundle_name(compiled), "version": version, "target_profile": compiled.profile.id},
        "inputs": compiled.provenance,
        "files": digests,
        "rendered_by": "astra-data render",
    }
    files["PROVENANCE.json"] = json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    return files


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_bundle(compiled: CompiledConfig, releases_dir: Path) -> Path:
    """Write the bundle, removing files it no longer produces. Returns the bundle directory."""
    root = Path(releases_dir) / bundle_name(compiled)
    files = render_bundle(compiled)
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                existing.unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def check_bundle(compiled: CompiledConfig, releases_dir: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every bundle file that is missing, stale or no longer produced."""
    root = Path(releases_dir) / bundle_name(compiled)
    files = render_bundle(compiled)
    problems: list[Problem] = []
    for name, text in files.items():
        path = root / name
        if not path.is_file():
            problems.append(Problem(_display(path, repo_root), None, f"not rendered for {compiled.id}; run astra-data render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the config or its inputs changed since it was rendered; run astra-data render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data render to remove it"))
    return problems


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = ["RENDERERS", "RenderError", "bundle_name", "check_bundle", "render_bundle", "write_bundle"]
