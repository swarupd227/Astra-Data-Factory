"""The rejection taxonomy, from the domain pack to CONTROL.REJECTION_CODES.

`astra-data rejections sync` reads `domains/<pack>/rejections.yaml` through
the knowledge plane and brings CONTROL.REJECTION_CODES in line with it in
one transaction: codes are inserted or updated, and codes that left the
taxonomy are retired (ACTIVE = FALSE), never deleted, so exception rows
keep a referent. What pipelines route exceptions with is therefore exactly
what was reviewed in Git.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from astra_knowledge.cdm import DomainPack, load_packs
from astra_knowledge.rejections import Taxonomy

from astra_core.problems import Problem
from astra_data.bundle import Executor, Target


def taxonomies_from_packs(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[Taxonomy], list[Problem]]:
    """The taxonomy of every domain pack (or one), validated as the packs are."""
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    if domain is not None:
        packs = [p for p in packs if p.name == domain]
        if not packs:
            return [], [Problem(Path(domains_dir).as_posix(), None, f"no domain pack named '{domain}'")]
    return [p.rejections for p in packs], []


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


COLUMNS = ("CODE", "NAME", "DESCRIPTION", "LEVEL", "SEVERITY", "CATEGORY", "OWNER", "ENTITY", "RESOLUTION", "AUTO_RESOLVE", "LOADER_CODES", "DOMAIN", "ACTIVE", "SOURCE")


def sync_statements(
    taxonomies: list[Taxonomy],
    target: Target,
    *,
    root: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> list[str]:
    """One transaction that makes CONTROL.REJECTION_CODES match the taxonomies."""
    table = f'"{target.environment_database}"."CONTROL"."REJECTION_CODES"'
    updated_at = _lit(clock().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")) + "::TIMESTAMP_NTZ"
    statements = ["BEGIN TRANSACTION"]

    rows: list[str] = []
    for taxonomy in taxonomies:
        source = _display(taxonomy.path, root)
        for c in taxonomy.codes:
            rows.append(
                "("
                + ", ".join(
                    [
                        _lit(c.code),
                        _lit(c.name),
                        _lit(c.description),
                        _lit(c.level),
                        _lit(c.severity),
                        _lit(c.category),
                        _lit(c.owner),
                        _lit(c.entity),
                        _lit(c.resolution),
                        "TRUE" if c.auto_resolve else "FALSE",
                        _lit(",".join(c.loader_codes)) if c.loader_codes else "NULL",
                        _lit(taxonomy.domain),
                        "TRUE" if c.active else "FALSE",
                        _lit(source),
                    ]
                )
                + ")"
            )
    if rows:
        columns = ", ".join(COLUMNS)
        updates = ", ".join(f"{name} = s.{name}" for name in COLUMNS if name not in ("CODE", "DOMAIN"))
        statements.append(
            f"MERGE INTO {table} t "
            f"USING (SELECT * FROM VALUES {', '.join(rows)} AS v ({columns})) s "
            f"ON t.CODE = s.CODE AND t.DOMAIN = s.DOMAIN "
            f"WHEN MATCHED THEN UPDATE SET {updates}, UPDATED_AT = {updated_at} "
            f"WHEN NOT MATCHED THEN INSERT ({columns}, UPDATED_AT) VALUES ({', '.join('s.' + name for name in COLUMNS)}, {updated_at})"
        )
    for taxonomy in taxonomies:
        keep = ", ".join(_lit(c.code) for c in taxonomy.codes)
        statements.append(f"UPDATE {table} SET ACTIVE = FALSE, UPDATED_AT = {updated_at} WHERE ACTIVE AND DOMAIN = {_lit(taxonomy.domain)} AND CODE NOT IN ({keep})")
    statements.append("COMMIT")
    return statements


def sync(executor: Executor, taxonomies: list[Taxonomy], target: Target, root: Path | None = None) -> int:
    """Apply the sync. Returns the number of codes now in the table from the taxonomies."""
    executor.execute_script(";\n".join(sync_statements(taxonomies, target, root=root)) + ";")
    return sum(len(t.codes) for t in taxonomies)


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = ["DomainPack", "Taxonomy", "sync", "sync_statements", "taxonomies_from_packs"]
