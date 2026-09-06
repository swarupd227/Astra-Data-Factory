"""The canonical data model of a domain pack (product spec Section 4).

A domain pack lives under `domains/<name>/`. Its glossary defines the
vocabulary; its `cdm/<major>.<minor>.yaml` files define the canonical
model version by version. An entity takes its definition from the glossary
term it names, so the model, the glossary and the rendered DDL say the same
thing.

The versioning rule: a breaking change (an entity or column removed or
renamed, a key changed, a column made required, a type narrowed, the
lineage or the target schema changed) needs a new major version and a
migration note; an additive change needs a new minor version. `diff`
classifies the changes between two versions and `load_pack` enforces the
rule between consecutive versions.

`render_ddl` and `render_tests` produce Snowflake managed Iceberg DDL and
the key and reference tests for one version, with `{{ DATABASE }}` as the
placeholder the deploy pipeline fills (ADR 0005). Identifiers are quoted
so entity names that are reserved words (ACCOUNT) are safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_knowledge.rejections import LOADER_REFERENCE_FILE, REJECTIONS_FILE, LoaderReference, Taxonomy, load_loader_reference, load_taxonomy, parity, parity_problems

SCHEMA = "cdm-v0.schema.json"
GLOSSARY_SCHEMA = "glossary-v0.schema.json"
GLOSSARY_FILE = "glossary.yaml"
CDM_DIR = "cdm"
RENDERED_DIR = "rendered"
VERSION_FILE = re.compile(r"^([1-9][0-9]*)\.(0|[1-9][0-9]*)\.ya?ml$")
DATABASE_PLACEHOLDER = "{{ DATABASE }}"

SQL_TYPES = {
    "string": "STRING",
    "integer": "NUMBER(18,0)",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ(6)",
    "boolean": "BOOLEAN",
}


# -- model -------------------------------------------------------------------


@dataclass(frozen=True)
class Code:
    value: str
    meaning: str


@dataclass(frozen=True)
class Lookup:
    """A control table the column's values must exist in."""

    schema: str
    table: str
    column: str

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    required: bool = False
    precision: int | None = None
    scale: int | None = None
    pii: str | None = None
    codes: tuple[Code, ...] = ()
    lookup: Lookup | None = None

    @property
    def sql_type(self) -> str:
        if self.type == "decimal":
            return f"NUMBER({self.precision},{self.scale})"
        return SQL_TYPES[self.type]

    def widens(self, other: "Column") -> bool:
        """True when a value of `other`'s type always fits this column's type."""
        if self.type == other.type:
            if self.type != "decimal":
                return True
            return self.precision >= other.precision and self.scale >= other.scale and (self.precision - self.scale) >= (other.precision - other.scale)
        if other.type == "integer" and self.type == "decimal":
            return (self.precision - self.scale) >= 18
        return False


@dataclass(frozen=True)
class Reference:
    entity: str
    columns: tuple[str, ...]
    optional: bool = False


@dataclass(frozen=True)
class Entity:
    name: str
    term: str
    table: str
    key: tuple[str, ...]
    columns: tuple[Column, ...]
    references: tuple[Reference, ...] = ()
    definition: str = ""

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class Model:
    domain: str
    version: str
    schema: str
    description: str
    lineage: tuple[Column, ...]
    entities: tuple[Entity, ...]
    path: Path
    migration: str | None = None

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])

    @property
    def minor(self) -> int:
        return int(self.version.split(".")[1])

    @property
    def label(self) -> str:
        return f"{self.domain} CDM {self.version}"

    def entity(self, name: str) -> Entity | None:
        return next((e for e in self.entities if e.name == name), None)

    def table_columns(self, entity: Entity) -> tuple[Column, ...]:
        """The entity's own columns followed by the lineage columns."""
        return entity.columns + self.lineage


@dataclass(frozen=True)
class Term:
    term: str
    kind: str
    definition: str
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class Glossary:
    domain: str
    terms: tuple[Term, ...]
    path: Path

    def term(self, name: str) -> Term | None:
        return next((t for t in self.terms if t.term == name), None)

    def entity_terms(self) -> list[Term]:
        return [t for t in self.terms if t.kind == "entity"]


@dataclass(frozen=True)
class DomainPack:
    name: str
    root: Path
    glossary: Glossary
    models: tuple[Model, ...]  # in version order
    rejections: Taxonomy
    loader_reference: LoaderReference | None = None

    @property
    def latest(self) -> Model:
        return self.models[-1]

    def model(self, version: str) -> Model | None:
        return next((m for m in self.models if m.version == version), None)


# -- loading -----------------------------------------------------------------


def _read(path: Path, display: str) -> tuple[Any, list[Problem]]:
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    return data, []


def load_glossary(path: Path, root: Path | None = None) -> tuple[Glossary | None, list[Problem]]:
    path = Path(path)
    display = display_path(path, root)
    data, problems = _read(path, display)
    if problems:
        return None, problems
    if data.get("glossary_version") != 0:
        line = line_of(data, ["glossary_version"]) if "glossary_version" in data else 1
        return None, [Problem(display, line, f"glossary_version must be 0; found {data.get('glossary_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", GLOSSARY_SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    seen: dict[str, int] = {}
    for i, term in enumerate(data["terms"]):
        name = term["term"]
        if name in seen:
            problems.append(Problem(display, line_of(data, ["terms", i, "term"]), f"term '{name}' is defined twice; the first is at line {seen[name]}"))
        else:
            seen[name] = line_of(data, ["terms", i, "term"]) or 0
    if problems:
        return None, problems

    terms = tuple(Term(t["term"], t["kind"], " ".join(t["definition"].split()), tuple(t.get("also") or ())) for t in data["terms"])
    return Glossary(data["domain"], terms, path), []


def _column(data: dict) -> Column:
    return Column(
        name=data["name"],
        type=data["type"],
        description=" ".join(str(data["description"]).split()),
        required=bool(data.get("required", False)),
        precision=data.get("precision"),
        scale=data.get("scale"),
        pii=data.get("pii"),
        codes=tuple(Code(str(c["value"]), c["meaning"]) for c in data.get("codes") or ()),
        lookup=Lookup(data["lookup"]["schema"], data["lookup"]["table"], data["lookup"]["column"]) if data.get("lookup") else None,
    )


def load_model_file(path: Path, glossary: Glossary | None, root: Path | None = None) -> tuple[Model | None, list[Problem]]:
    """Parse and validate one model version. Definitions come from the glossary; without one, they are blank."""
    path = Path(path)
    display = display_path(path, root)
    data, problems = _read(path, display)
    if problems:
        return None, problems
    if data.get("cdm_version") != 0:
        line = line_of(data, ["cdm_version"]) if "cdm_version" in data else 1
        return None, [Problem(display, line, f"cdm_version must be 0; found {data.get('cdm_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    problems = _model_problems(data, path, display, glossary)
    if problems:
        return None, problems

    entities = tuple(
        Entity(
            name=e["name"],
            term=e["term"],
            table=e["table"],
            key=tuple(e["key"]),
            columns=tuple(_column(c) for c in e["columns"]),
            references=tuple(Reference(r["entity"], tuple(r["columns"]), bool(r.get("optional", False))) for r in e.get("references") or ()),
            definition=glossary.term(e["term"]).definition if glossary and glossary.term(e["term"]) else "",
        )
        for e in data["entities"]
    )
    model = data["model"]
    return Model(
        domain=model["domain"],
        version=str(model["version"]),
        schema=model["schema"],
        description=" ".join(model["description"].split()),
        lineage=tuple(_column(c) for c in data["lineage"]),
        entities=entities,
        path=path,
        migration=model.get("migration"),
    ), []


def _model_problems(data: dict, path: Path, display: str, glossary: Glossary | None) -> list[Problem]:
    problems: list[Problem] = []
    model = data["model"]

    match = VERSION_FILE.match(path.name)
    if not match or f"{match.group(1)}.{match.group(2)}" != str(model["version"]):
        problems.append(Problem(display, line_of(data, ["model", "version"]), f"model.version '{model['version']}' must match the file name; the file is {path.name}"))
    if glossary is not None and glossary.domain != model["domain"]:
        problems.append(Problem(display, line_of(data, ["model", "domain"]), f"model.domain '{model['domain']}' is not the glossary's domain '{glossary.domain}'"))
    if model.get("migration"):
        note = path.parent / model["migration"]
        if not note.is_file():
            problems.append(Problem(display, line_of(data, ["model", "migration"]), f"migration note '{model['migration']}' does not exist next to the model"))
        elif not note.read_text(encoding="utf-8").strip():
            problems.append(Problem(display, line_of(data, ["model", "migration"]), f"migration note '{model['migration']}' is empty"))

    lineage_names: dict[str, int] = {}
    for i, column in enumerate(data["lineage"]):
        _column_problems(column, ["lineage", i], data, display, problems)
        if column["name"] in lineage_names:
            problems.append(Problem(display, line_of(data, ["lineage", i, "name"]), f"lineage column '{column['name']}' is defined twice"))
        lineage_names[column["name"]] = i

    names: dict[str, int] = {}
    tables: dict[str, str] = {}
    terms: dict[str, str] = {}
    for i, entity in enumerate(data["entities"]):
        where = ["entities", i]
        if entity["name"] in names:
            problems.append(Problem(display, line_of(data, where + ["name"]), f"entity '{entity['name']}' is defined twice"))
        names[entity["name"]] = i
        if entity["table"] in tables:
            problems.append(Problem(display, line_of(data, where + ["table"]), f"table '{entity['table']}' is used by both '{tables[entity['table']]}' and '{entity['name']}'"))
        tables[entity["table"]] = entity["name"]
        if entity["term"] in terms:
            problems.append(Problem(display, line_of(data, where + ["term"]), f"glossary term '{entity['term']}' defines both '{terms[entity['term']]}' and '{entity['name']}'"))
        terms[entity["term"]] = entity["name"]

        if glossary is not None:
            term = glossary.term(entity["term"])
            if term is None:
                problems.append(Problem(display, line_of(data, where + ["term"]), f"entity '{entity['name']}' names glossary term '{entity['term']}', which is not in {display_path(glossary.path, None) if glossary.path else 'the glossary'}"))
            elif term.kind != "entity":
                problems.append(Problem(display, line_of(data, where + ["term"]), f"entity '{entity['name']}' names glossary term '{entity['term']}', which is a {term.kind} term, not an entity term"))
            elif term.term != entity["name"]:
                problems.append(Problem(display, line_of(data, where + ["term"]), f"entity '{entity['name']}' must be named after its glossary term '{term.term}'"))

        columns: dict[str, int] = {}
        for ci, column in enumerate(entity["columns"]):
            _column_problems(column, where + ["columns", ci], data, display, problems)
            if column["name"] in columns:
                problems.append(Problem(display, line_of(data, where + ["columns", ci, "name"]), f"column '{column['name']}' of '{entity['name']}' is defined twice"))
            if column["name"] in lineage_names:
                problems.append(Problem(display, line_of(data, where + ["columns", ci, "name"]), f"column '{column['name']}' of '{entity['name']}' has the name of a lineage column; lineage columns are added to every table"))
            columns[column["name"]] = ci
        for ki, key in enumerate(entity["key"]):
            if key not in columns:
                problems.append(Problem(display, line_of(data, where + ["key"]), f"key column '{key}' is not a column of '{entity['name']}'"))
            elif not entity["columns"][columns[key]].get("required", False):
                problems.append(Problem(display, line_of(data, where + ["columns", columns[key]]), f"key column '{key}' of '{entity['name']}' must be required"))

    for i, entity in enumerate(data["entities"]):
        where = ["entities", i]
        entity_columns = {c["name"] for c in entity["columns"]}
        for ri, ref in enumerate(entity.get("references") or ()):
            target = next((e for e in data["entities"] if e["name"] == ref["entity"]), None)
            if target is None:
                problems.append(Problem(display, line_of(data, where + ["references", ri, "entity"]), f"'{entity['name']}' references entity '{ref['entity']}', which is not in the model"))
                continue
            if len(ref["columns"]) != len(target["key"]):
                problems.append(Problem(display, line_of(data, where + ["references", ri, "columns"]), f"'{entity['name']}' references '{ref['entity']}' with {len(ref['columns'])} column(s), but its key has {len(target['key'])}: {', '.join(target['key'])}"))
            for name in ref["columns"]:
                if name not in entity_columns:
                    problems.append(Problem(display, line_of(data, where + ["references", ri, "columns"]), f"'{entity['name']}' references '{ref['entity']}' through '{name}', which is not a column of '{entity['name']}'"))
    return problems


def _column_problems(column: dict, where: list, data: dict, display: str, problems: list[Problem]) -> None:
    if column["type"] == "decimal" and column["scale"] >= column["precision"]:
        problems.append(Problem(display, line_of(data, where), f"column '{column['name']}': scale {column['scale']} must be less than precision {column['precision']}"))
    codes = column.get("codes") or ()
    values = [c["value"] for c in codes]
    for value in set(values):
        if values.count(value) > 1:
            problems.append(Problem(display, line_of(data, where + ["codes"]), f"column '{column['name']}': code '{value}' is listed twice"))


def load_pack(root: Path, repo_root: Path | None = None) -> tuple[DomainPack | None, list[Problem]]:
    """Load one domain pack: the glossary, every model version and the checks between them."""
    root = Path(root)
    display = display_path(root, repo_root)
    glossary_path = root / GLOSSARY_FILE
    if not glossary_path.is_file():
        return None, [Problem(display, None, f"domain pack has no {GLOSSARY_FILE}")]
    glossary, problems = load_glossary(glossary_path, repo_root)
    if glossary is None:
        return None, problems

    cdm_dir = root / CDM_DIR
    files = sorted((p for p in cdm_dir.glob("*.y*ml") if VERSION_FILE.match(p.name)), key=lambda p: _version_key(p.name)) if cdm_dir.is_dir() else []
    if not files:
        return None, [Problem(display, None, f"domain pack has no model versions under {CDM_DIR}/ (expected files like {CDM_DIR}/1.0.yaml)")]
    for stray in sorted(cdm_dir.glob("*.y*ml")):
        if not VERSION_FILE.match(stray.name):
            problems.append(Problem(display_path(stray, repo_root), None, "model files are named <major>.<minor>.yaml"))

    models: list[Model] = []
    for file in files:
        model, model_problems = load_model_file(file, glossary, repo_root)
        problems.extend(model_problems)
        if model is not None:
            models.append(model)
    if problems:
        return None, problems

    rejections_path = root / REJECTIONS_FILE
    if not rejections_path.is_file():
        return None, [Problem(display, None, f"domain pack has no {REJECTIONS_FILE}; every pack carries its rejection taxonomy")]
    taxonomy, problems = load_taxonomy(rejections_path, repo_root)
    if taxonomy is None:
        return None, problems
    reference: LoaderReference | None = None
    reference_path = root / LOADER_REFERENCE_FILE
    if reference_path.is_file():
        reference, problems = load_loader_reference(reference_path, repo_root)
        if reference is None:
            return None, problems

    problems.extend(_pack_problems(root, glossary, models, taxonomy, reference, repo_root))
    if problems:
        return None, problems
    return DomainPack(root.name, root, glossary, tuple(models), taxonomy, reference), []


def _version_key(name: str) -> tuple[int, int]:
    match = VERSION_FILE.match(name)
    return int(match.group(1)), int(match.group(2))


def _pack_problems(root: Path, glossary: Glossary, models: list[Model], taxonomy: Taxonomy, reference: LoaderReference | None, repo_root: Path | None) -> list[Problem]:
    problems: list[Problem] = []
    glossary_display = display_path(glossary.path, repo_root)
    taxonomy_display = display_path(taxonomy.path, repo_root)

    if glossary.domain != root.name:
        problems.append(Problem(glossary_display, None, f"glossary domain '{glossary.domain}' must match the pack directory '{root.name}'"))
    if taxonomy.domain != root.name:
        problems.append(Problem(taxonomy_display, None, f"taxonomy domain '{taxonomy.domain}' must match the pack directory '{root.name}'"))
    entity_names = {e.name for model in models for e in model.entities}
    for code in taxonomy.codes:
        if code.entity is not None and code.entity not in entity_names:
            problems.append(Problem(taxonomy_display, None, f"code {code.code} names entity '{code.entity}', which is in no model version"))
    if reference is not None:
        problems.extend(parity_problems(taxonomy, reference, repo_root))

    # A term that defines an entity stays an entity term for as long as any
    # version has the entity, so older versions remain loadable after a
    # removal; a term no version uses has drifted from the model.
    defined = {e.term for model in models for e in model.entities}
    for term in glossary.entity_terms():
        if term.term not in defined:
            problems.append(Problem(glossary_display, None, f"entity term '{term.term}' has no entity in any model version; make it a concept term or add the entity"))

    first = models[0]
    if (first.major, first.minor) != (1, 0):
        problems.append(Problem(display_path(first.path, repo_root), None, f"the first model version must be 1.0; found {first.version}"))
    for previous, current in zip(models, models[1:]):
        display = display_path(current.path, repo_root)
        changes = diff(previous, current)
        breaking = [c for c in changes if c.breaking]
        if not changes:
            problems.append(Problem(display, None, f"version {current.version} is identical to {previous.version}; a new version must change something"))
            continue
        if breaking:
            if (current.major, current.minor) != (previous.major + 1, 0):
                detail = "; ".join(c.message for c in breaking[:3]) + ("; ..." if len(breaking) > 3 else "")
                problems.append(Problem(display, None, f"version {current.version} makes {len(breaking)} breaking change{'s' if len(breaking) > 1 else ''} to {previous.version}, so it must be {previous.major + 1}.0 with a migration note: {detail}"))
            if not current.migration:
                problems.append(Problem(display, None, f"version {current.version} breaks {previous.version} and must name a migration note (model.migration: migrations/{current.version}.md)"))
        elif (current.major, current.minor) != (previous.major, previous.minor + 1):
            expected = f"{previous.major}.{previous.minor + 1}"
            problems.append(Problem(display, None, f"version {current.version} only adds to {previous.version}, so it must be {expected}; a new major version is for breaking changes"))
    return problems


def load_packs(domains_dir: Path, repo_root: Path | None = None) -> tuple[list[DomainPack], list[Problem]]:
    """Every domain pack under the directory, in name order."""
    domains_dir = Path(domains_dir)
    if not domains_dir.is_dir():
        return [], [Problem(display_path(domains_dir, repo_root), None, "domains directory does not exist")]
    packs: list[DomainPack] = []
    problems: list[Problem] = []
    for root in sorted(p for p in domains_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        pack, pack_problems = load_pack(root, repo_root)
        problems.extend(pack_problems)
        if pack is not None:
            packs.append(pack)
    return packs, problems


# -- versions ----------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    breaking: bool
    message: str
    entity: str | None = None

    @property
    def kind(self) -> str:
        return "breaking" if self.breaking else "additive"


def diff(old: Model, new: Model) -> list[Change]:
    """The changes from `old` to `new`, each classified as breaking or additive."""
    changes: list[Change] = []
    if old.schema != new.schema:
        changes.append(Change(True, f"target schema changed from {old.schema} to {new.schema}"))
    changes.extend(_column_changes(old.lineage, new.lineage, "lineage", None))

    old_by_name = {e.name: e for e in old.entities}
    new_by_name = {e.name: e for e in new.entities}
    for name, entity in old_by_name.items():
        if name not in new_by_name:
            changes.append(Change(True, f"entity {name} removed", name))
    for name, entity in new_by_name.items():
        before = old_by_name.get(name)
        if before is None:
            changes.append(Change(False, f"entity {name} added", name))
            continue
        if before.table != entity.table:
            changes.append(Change(True, f"{name}: table renamed from {before.table} to {entity.table}", name))
        if before.key != entity.key:
            changes.append(Change(True, f"{name}: key changed from ({', '.join(before.key)}) to ({', '.join(entity.key)})", name))
        if before.term != entity.term:
            changes.append(Change(False, f"{name}: glossary term changed from {before.term} to {entity.term}", name))
        elif before.definition != entity.definition:
            changes.append(Change(False, f"{name}: definition changed", name))
        changes.extend(_column_changes(before.columns, entity.columns, name, name))
        old_refs = {(r.entity, r.columns): r for r in before.references}
        new_refs = {(r.entity, r.columns): r for r in entity.references}
        for key, ref in old_refs.items():
            if key not in new_refs:
                changes.append(Change(False, f"{name}: reference to {ref.entity} through ({', '.join(ref.columns)}) removed", name))
        for key, ref in new_refs.items():
            if key not in old_refs:
                changes.append(Change(False, f"{name}: reference to {ref.entity} through ({', '.join(ref.columns)}) added", name))
            elif old_refs[key].optional != ref.optional:
                changes.append(Change(False, f"{name}: reference to {ref.entity} made {'optional' if ref.optional else 'required'}", name))
    return changes


def _column_changes(old: tuple[Column, ...], new: tuple[Column, ...], label: str, entity: str | None) -> list[Change]:
    changes: list[Change] = []
    old_by_name = {c.name: c for c in old}
    new_by_name = {c.name: c for c in new}
    for name in old_by_name:
        if name not in new_by_name:
            changes.append(Change(True, f"{label}: column {name} removed", entity))
    for name, column in new_by_name.items():
        before = old_by_name.get(name)
        if before is None:
            if column.required:
                changes.append(Change(True, f"{label}: required column {name} added; existing rows have no value for it", entity))
            else:
                changes.append(Change(False, f"{label}: optional column {name} added", entity))
            continue
        if before.sql_type != column.sql_type:
            if column.widens(before):
                changes.append(Change(False, f"{label}: column {name} widened from {before.sql_type} to {column.sql_type}", entity))
            else:
                changes.append(Change(True, f"{label}: column {name} changed from {before.sql_type} to {column.sql_type}", entity))
        if not before.required and column.required:
            changes.append(Change(True, f"{label}: column {name} made required", entity))
        elif before.required and not column.required:
            changes.append(Change(False, f"{label}: column {name} made optional", entity))
        if before.pii != column.pii:
            changes.append(Change(False, f"{label}: column {name} PII category changed from {before.pii or 'none'} to {column.pii or 'none'}", entity))
        if before.lookup != column.lookup:
            changes.append(Change(False, f"{label}: column {name} lookup changed from {before.lookup.label if before.lookup else 'none'} to {column.lookup.label if column.lookup else 'none'}", entity))
        if before.codes != column.codes:
            changes.append(Change(False, f"{label}: column {name} code list changed", entity))
        elif before.description != column.description:
            changes.append(Change(False, f"{label}: column {name} description changed", entity))
    return changes


# -- rendering ---------------------------------------------------------------


def _quote(identifier: str) -> str:
    return f'"{identifier}"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _table(model: Model, entity: Entity) -> str:
    return f"{DATABASE_PLACEHOLDER}.{_quote(model.schema)}.{_quote(entity.table)}"


def _column_comment(column: Column) -> str:
    parts = [column.description]
    if column.codes:
        parts.append("Codes: " + "; ".join(f"{c.value} = {c.meaning}" for c in column.codes) + ".")
    if column.lookup:
        parts.append(f"Lookup: {column.lookup.label}.")
    if column.pii:
        parts.append(f"PII: {column.pii}.")
    return " ".join(parts)


def render_ddl(model: Model) -> str:
    """CREATE ICEBERG TABLE IF NOT EXISTS for every entity, in model order."""
    source = model.path.as_posix()
    lines = [
        f"-- {model.label}: DDL for Snowflake managed Iceberg tables in schema {model.schema}.",
        f"-- Rendered by astra-spec cdm render from {source.split('/domains/')[-1] if '/domains/' in source else model.path.name}. Do not edit; change the model and re-render.",
        f"-- {DATABASE_PLACEHOLDER} is filled at deploy time. Tables inherit the database's external volume and catalog (ADR 0002).",
        "",
    ]
    for entity in model.entities:
        columns = model.table_columns(entity)
        width = max(len(c.name) for c in columns) + 2
        lines.append(f"-- {entity.name}: key ({', '.join(entity.key)})")
        for ref in entity.references:
            lines.append(f"-- references {ref.entity} through ({', '.join(ref.columns)}){' when present' if ref.optional else ''}")
        lines.append(f"CREATE ICEBERG TABLE IF NOT EXISTS {_table(model, entity)} (")
        body = []
        for column in columns:
            declaration = f"  {_quote(column.name).ljust(width)} {column.sql_type}"
            if column.required:
                declaration += " NOT NULL"
            declaration += f" COMMENT {_literal(_column_comment(column))}"
            body.append(declaration)
        lines.append(",\n".join(body))
        lines.append(")")
        lines.append(f"BASE_LOCATION = '{model.schema.lower()}/{entity.table.lower()}/'")
        lines.append(f"COMMENT = {_literal(f'{entity.name}: {entity.definition} [{model.label}]')};")
        lines.append("")
    return "\n".join(lines)


def render_tests(model: Model) -> dict[str, str]:
    """Key uniqueness and reference tests, keyed by file name. A test returns failing rows."""
    tests: dict[str, str] = {}
    for entity in model.entities:
        key = ", ".join(_quote(k) for k in entity.key)
        tests[f"{entity.table.lower()}_key.sql"] = "\n".join(
            [
                f"-- {entity.name}: the key ({', '.join(entity.key)}) identifies one row. Returns keys with more than one row.",
                f"SELECT {key}, COUNT(*) AS ROW_COUNT",
                f"FROM {_table(model, entity)}",
                f"GROUP BY {key}",
                "HAVING COUNT(*) > 1;",
                "",
            ]
        )
        for ref in entity.references:
            target = model.entity(ref.entity)
            join = " AND ".join(f"r.{_quote(t)} = e.{_quote(s)}" for s, t in zip(ref.columns, target.key))
            select = ", ".join(f"e.{_quote(c)}" for c in dict.fromkeys(entity.key + ref.columns))
            where = [f"r.{_quote(target.key[0])} IS NULL"]
            if ref.optional:
                where.append("(" + " OR ".join(f"e.{_quote(c)} IS NOT NULL" for c in ref.columns) + ")")
            tests[f"{entity.table.lower()}_{target.table.lower()}_reference.sql"] = "\n".join(
                [
                    f"-- {entity.name} -> {target.name}: ({', '.join(ref.columns)}) must exist in {target.table}{', when present' if ref.optional else ''}. Returns rows with no match.",
                    f"SELECT {select}",
                    f"FROM {_table(model, entity)} AS e",
                    f"LEFT JOIN {_table(model, target)} AS r ON {join}",
                    f"WHERE {' AND '.join(where)};",
                    "",
                ]
            )
        for column in entity.columns:
            if column.lookup is None:
                continue
            lookup = column.lookup
            lookup_table = f"{DATABASE_PLACEHOLDER}.{_quote(lookup.schema)}.{_quote(lookup.table)}"
            select = ", ".join(f"e.{_quote(c)}" for c in dict.fromkeys(entity.key + (column.name,)))
            where = [f"r.{_quote(lookup.column)} IS NULL"]
            if not column.required:
                where.append(f"e.{_quote(column.name)} IS NOT NULL")
            tests[f"{entity.table.lower()}_{column.name.lower()}_lookup.sql"] = "\n".join(
                [
                    f"-- {entity.name}: {column.name} must be a value of {lookup.label}{'' if column.required else ', when present'}. Returns rows whose value is not.",
                    f"SELECT {select}",
                    f"FROM {_table(model, entity)} AS e",
                    f"LEFT JOIN {lookup_table} AS r ON r.{_quote(lookup.column)} = e.{_quote(column.name)}",
                    f"WHERE {' AND '.join(where)};",
                    "",
                ]
            )
    return tests


def rendered_files(model: Model) -> dict[str, str]:
    """Every rendered file of a version, keyed by path relative to `cdm/rendered/<version>/`."""
    files = {"ddl.sql": render_ddl(model)}
    files.update({f"tests/{name}": text for name, text in render_tests(model).items()})
    return files


def rendered_dir(pack: DomainPack, model: Model) -> Path:
    return pack.root / CDM_DIR / RENDERED_DIR / model.version


def write_rendered(pack: DomainPack) -> list[Path]:
    """Write the rendered files of every version, removing stale files. Returns the paths written."""
    written: list[Path] = []
    for model in pack.models:
        target = rendered_dir(pack, model)
        files = rendered_files(model)
        for existing in sorted(p for p in target.rglob("*") if p.is_file()):
            if existing.relative_to(target).as_posix() not in files:
                existing.unlink()
        for name, text in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
    return written


def check_rendered(pack: DomainPack, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every rendered file that is missing, stale or no longer produced."""
    problems: list[Problem] = []
    for model in pack.models:
        target = rendered_dir(pack, model)
        files = rendered_files(model)
        for name, text in files.items():
            path = target / name
            if not path.is_file():
                problems.append(Problem(display_path(path, repo_root), None, f"not rendered for {model.label}; run astra-spec cdm render"))
            elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
                problems.append(Problem(display_path(path, repo_root), None, f"stale: the model changed since it was rendered; run astra-spec cdm render"))
        if target.is_dir():
            for existing in sorted(p for p in target.rglob("*") if p.is_file()):
                if existing.relative_to(target).as_posix() not in files:
                    problems.append(Problem(display_path(existing, repo_root), None, f"no longer produced by {model.label}; run astra-spec cdm render to remove it"))
    return problems
