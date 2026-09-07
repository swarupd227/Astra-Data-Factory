"""Gold read models of a domain pack (product spec Section 4; S3.2.8).

`domains/<name>/read-models.yaml` declares the consumer-shaped Gold tables
the pack publishes from its canonical model: for each, the entity it
reads, the columns it exposes under the consumer's names (a column of the
entity, or an expression over the entity's columns), and which Gold
columns name the custodian and the business date. The generation plane
renders the tables, the publish procedure that fills them per custodian
and business date and writes the watermark last, and the views that read
only complete days.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_knowledge.columns import Column, column_from

SCHEMA = "read-models-v0.schema.json"
READ_MODELS_FILE = "read-models.yaml"
GOLD_SCHEMA = "GOLD"


@dataclass(frozen=True)
class ReadModelColumn:
    column: Column  # the Gold column: consumer name, type, description, pii
    source: str | None  # entity column it carries, or None for an expression
    expression: str | None = None

    @property
    def name(self) -> str:
        return self.column.name


@dataclass(frozen=True)
class ReadModel:
    id: str
    table: str
    entity: str
    description: str
    custodian: str  # Gold column naming the custodian
    business_date: str | None  # Gold column naming the business date; None for a snapshot published whole
    columns: tuple[ReadModelColumn, ...]

    def column(self, name: str) -> ReadModelColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def dated(self) -> bool:
        return self.business_date is not None


@dataclass(frozen=True)
class ReadModels:
    domain: str
    model_version: str  # the canonical model version the read models read
    models: tuple[ReadModel, ...]
    path: Path

    def model(self, model_id: str) -> ReadModel | None:
        return next((m for m in self.models if m.id == model_id), None)


def load_read_models(path: Path, root: Path | None = None) -> tuple[dict | None, list[Problem]]:
    """Parse the file and check its shape. The entity references are resolved by `resolve_read_models` against a model."""
    path = Path(path)
    display = display_path(path, root)
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    if data.get("read_models_version") != 0:
        line = line_of(data, ["read_models_version"]) if "read_models_version" in data else 1
        return None, [Problem(display, line, f"read_models_version must be 0; found {data.get('read_models_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)
    ids: set[str] = set()
    tables: dict[str, str] = {}
    for i, m in enumerate(data["read_models"]):
        where = ["read_models", i]
        if m["id"] in ids:
            problems.append(Problem(display, line_of(data, where + ["id"]), f"read model '{m['id']}' is defined twice"))
        ids.add(m["id"])
        if m["table"] in tables:
            problems.append(Problem(display, line_of(data, where + ["table"]), f"table {m['table']} is used by both '{tables[m['table']]}' and '{m['id']}'"))
        tables[m["table"]] = m["id"]
        names: set[str] = set()
        for ci, c in enumerate(m["columns"]):
            if c["name"] in names:
                problems.append(Problem(display, line_of(data, where + ["columns", ci, "name"]), f"column '{c['name']}' of read model '{m['id']}' is defined twice"))
            names.add(c["name"])
            if c.get("type") == "decimal" and c["scale"] >= c["precision"]:
                problems.append(Problem(display, line_of(data, where + ["columns", ci]), f"column '{c['name']}': scale {c['scale']} must be less than precision {c['precision']}"))
        for key in ("custodian", "business_date"):
            if m.get(key) and m[key] not in names:
                problems.append(Problem(display, line_of(data, where + [key]), f"{key} column '{m[key]}' of read model '{m['id']}' is not one of its columns"))
    if problems:
        return None, problems
    data["_path"] = path
    return data, []


def resolve_read_models(data: dict, model, root: Path | None = None) -> tuple[ReadModels | None, list[Problem]]:
    """Resolve the loaded file against the canonical model version it pins: entities, source columns, expression columns, roles."""
    path: Path = data["_path"]
    display = display_path(path, root)
    problems: list[Problem] = []
    models: list[ReadModel] = []
    entities = {e.name: e for e in model.entities}
    for i, m in enumerate(data["read_models"]):
        where = ["read_models", i]
        entity = entities.get(m["entity"])
        if entity is None:
            problems.append(Problem(display, line_of(data, where + ["entity"]), f"read model '{m['id']}' reads entity '{m['entity']}', which model {model.version} does not define; entities are {', '.join(entities)}"))
            continue
        entity_columns = {c.name: c for c in entity.columns}
        columns: list[ReadModelColumn] = []
        for ci, c in enumerate(m["columns"]):
            cwhere = where + ["columns", ci]
            if c.get("expression"):
                expression = " ".join(c["expression"].split())
                used = [n for n in entity_columns if re.search(rf"(?<![A-Z0-9_\"]){re.escape(n)}(?![A-Z0-9_\"])", expression.upper())]
                if not used:
                    problems.append(Problem(display, line_of(data, cwhere + ["expression"]), f"column '{c['name']}' of read model '{m['id']}': the expression names no column of {entity.name}; its columns are {', '.join(entity_columns)}"))
                    continue
                columns.append(ReadModelColumn(column_from({k: v for k, v in c.items() if k in ("name", "type", "precision", "scale", "description")}), None, expression))
                continue
            source = c.get("source", c["name"])
            src = entity_columns.get(source)
            if src is None:
                problems.append(Problem(display, line_of(data, cwhere), f"column '{c['name']}' of read model '{m['id']}' carries '{source}', which is not a column of {entity.name}; its columns are {', '.join(entity_columns)}"))
                continue
            for key in ("type", "precision", "scale"):
                if key in c:
                    problems.append(Problem(display, line_of(data, cwhere + [key]), f"column '{c['name']}' of read model '{m['id']}' carries {entity.name}.{source} and takes its {key}; {key} is only for an expression column"))
            gold = replace(src, name=c["name"], description=" ".join(c["description"].split()) if c.get("description") else src.description, codes=src.codes, lookup=None)
            columns.append(ReadModelColumn(gold, source))
        for key, wanted in (("custodian", "string"), ("business_date", "date")):
            name = m.get(key)
            if not name:
                continue
            col = next((x for x in columns if x.name == name), None)
            if col is not None and col.column.type != wanted:
                problems.append(Problem(display, line_of(data, where + [key]), f"{key} column '{name}' of read model '{m['id']}' is {col.column.type}; it must be a {wanted} column"))
        models.append(ReadModel(m["id"], m["table"], entity.name, " ".join(m["description"].split()), m["custodian"], m.get("business_date"), tuple(columns)))
    if problems:
        return None, problems
    return ReadModels(data["domain"], model.version, tuple(models), path), []
