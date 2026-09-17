"""Silver CDM on Iceberg, rendered as a release bundle per domain pack (S7.1.1, ADR 0075).

The canonical model's own DDL and key/reference/lookup tests (`astra_knowledge.cdm.render_ddl`,
`render_tests`) already exist, are already correct for every entity of the domain pack (all nine
of the custodial pack's own entities, each with a declared key), and are already checked fresh in
CI (`astra-spec cdm render --check`) — this module renders nothing new and reimplements no DDL or
test logic. What was missing: nothing wired that already-correct DDL into the real deploy pipeline
that stands up the actual Envestnet instance. `astra-data deploy`/`astra-data test` already walk
every bundle under `releases/` (`astra_data.bundle.check_bundles`) and run there for real on every
merge to `main` (`.github/actions/deploy-environment/action.yml`) — wrapping the canonical model's
own already-rendered DDL and tests in the same release-bundle shape `astra-data gold render` and
`astra-data reference render` already established (`manifest.yaml`, `ddl/`, `tests/`) makes the
real Silver schema, and "keys enforced by tests," an executed part of every real deploy, with zero
new deploy-time code — `astra-data deploy`/`test`/`bundles check`/`bundles lint` need no change at
all to pick this bundle up.

  ddl/silver_tables.sql   every CREATE ICEBERG TABLE of the domain pack's current (latest, per
                          DomainPack.latest) CDM model version — astra_knowledge.cdm.render_ddl's
                          own output, unchanged, including its own "Rendered by astra-spec cdm
                          render" header, since this bundle carries that renderer's real output
                          verbatim rather than re-rendering it under a second name
  tests/*.sql             every uniqueness, reference and lookup test astra_knowledge.cdm.
                          render_tests already generates for that version, unchanged

The bundle is committed under releases/<pack>-silver and checked in CI, like the Gold and
reference-data bundles.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from astra_knowledge.cdm import DomainPack, Model, load_packs, render_ddl, render_tests

from astra_core.problems import Problem

BUNDLE_SUFFIX = "-silver"


def packs_with_cdm(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[DomainPack], list[Problem]]:
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    return [p for p in packs if p.models and (domain is None or p.name == domain)], []


def bundle_name(pack: DomainPack) -> str:
    return f"{pack.name.replace('_', '-')}{BUNDLE_SUFFIX}"


def render_bundle(pack: DomainPack) -> dict[str, str]:
    """Every file of the pack's Silver bundle, keyed by path relative to the bundle directory —
    the current (latest) CDM model version's own DDL and tests, unchanged from astra_knowledge.cdm."""
    model: Model = pack.latest
    files: dict[str, str] = {"ddl/silver_tables.sql": render_ddl(model)}
    files.update({f"tests/{name}": text for name, text in render_tests(model).items()})
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    manifest = [
        f"# Silver CDM tables of the {pack.name} domain pack, model version {model.version}. Rendered by astra-data silver",
        f"# render from domains/{pack.name}/cdm/{model.version}.yaml -- the same DDL and tests astra-spec cdm render already",
        f"# writes under cdm/rendered/{model.version}/, wrapped as a release bundle; the version is a digest of the rendered",
        "# files. Do not edit.",
        f"bundle: {bundle_name(pack)}",
        f'version: "{digest.hexdigest()[:12]}"',
        f"source: {pack.name}_silver",
        "steps:",
        "  - ddl/silver_tables.sql",
        "tests:",
        "  - tests/*.sql",
        "",
    ]
    files["manifest.yaml"] = "\n".join(manifest)
    return files


def write_bundle(pack: DomainPack, releases_dir: Path) -> Path:
    """Write the bundle, removing files it no longer produces. Returns the bundle directory."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                existing.unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def check_bundle(pack: DomainPack, releases_dir: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every bundle file that is missing, stale or no longer produced."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    problems: list[Problem] = []
    for name, text in files.items():
        path = root / name
        if not path.is_file():
            problems.append(Problem(_display(path, repo_root), None, "not rendered; run astra-data silver render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the domain pack's CDM changed since it was rendered; run astra-data silver render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data silver render to remove it"))
    return problems


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
