"""Atlan payload for one source: the Bronze tables as assets, their columns, lineage to Silver, glossary terms.

The payload is the entity list Atlan's bulk upsert takes (`POST
/api/meta/entity/bulk`). Qualified names carry the `{{ DATABASE }}`
placeholder and the connection prefix `{{ ATLAN_CONNECTION }}`, which
the governance integration (E8) fills when it pushes the payload; the
bundle itself stays environment-neutral.
"""

from __future__ import annotations

import json

from astra_data.compiler import CompiledConfig
from astra_data.render.names import custodian_folder, exceptions_table, file_metadata_table, files_table, logical_columns, parse_problems_table, raw_lines_table, record_table, silver_table, sql_type

CONNECTION = "{{ ATLAN_CONNECTION }}"
DATABASE = "{{ DATABASE }}"


def _table_qn(schema: str, table: str) -> str:
    return f"{CONNECTION}/{DATABASE}/{schema}/{table}"


def _table(schema: str, table: str, description: str, owner: str, certificate: str = "DRAFT") -> dict:
    return {
        "typeName": "Table",
        "attributes": {
            "qualifiedName": _table_qn(schema, table),
            "name": table,
            "schemaName": schema,
            "databaseName": DATABASE,
            "description": description,
            "ownerUsers": [owner],
            "certificateStatus": certificate,
        },
    }


def _column(schema: str, table: str, name: str, order: int, data_type: str, description: str, pii: str | None = None) -> dict:
    attributes = {
        "qualifiedName": f"{_table_qn(schema, table)}/{name}",
        "name": name,
        "order": order,
        "dataType": data_type,
        "description": description,
        "tableQualifiedName": _table_qn(schema, table),
    }
    entity = {"typeName": "Column", "attributes": attributes}
    if pii:
        entity["classifications"] = [{"typeName": "PII", "attributes": {"category": pii}}]
    return entity


def render_payload(compiled: CompiledConfig) -> dict:
    spec = compiled.spec
    owner = compiled.owner["email"]
    entities: list[dict] = []

    for record in spec.records:
        if record.type != "detail":
            continue
        label = record.label
        table = record_table(compiled, label)
        entities.append(_table("BRONZE", table, f"Source {compiled.id}: record {label} of spec {spec.label}, parsed by a dynamic table.", owner))
        for order, f in enumerate([f for f in record.fields if f.name != "filler"], start=1):
            entities.append(_column("BRONZE", table, f.name.upper(), order, sql_type(f), f.description or f"{f.name} of the {label} record"))
    entities.append(_table("BRONZE", raw_lines_table(compiled), f"Source {compiled.id}: raw lines as delivered, loaded by Snowpipe from {custodian_folder(compiled)}.", owner))
    entities.append(_column("BRONZE", raw_lines_table(compiled), "LINE", 3, "STRING", "The line as delivered, untouched.", "raw_record"))
    entities.append(_table("BRONZE", files_table(compiled), f"Source {compiled.id}: landed files and their pipeline status.", owner))
    entities.append(_table("BRONZE", parse_problems_table(compiled), f"Source {compiled.id}: parse problems with their rejection codes.", owner))
    entities.append(_table("BRONZE", file_metadata_table(compiled), f"Source {compiled.id}: per-file counts, excluded rows and header and trailer values.", owner))
    if spec.merge:
        label = spec.merge.record or spec.logical_records()[0]
        entities.append(_table("SILVER", silver_table(compiled), f"Source {compiled.id}: logical record {label} merged by mode ({spec.merge.mode_field}); one active row per scope and key.", owner))
        for order, column in enumerate(logical_columns(spec, label), start=1):
            f = column.field
            entities.append(_column("SILVER", silver_table(compiled), column.name, order, sql_type(f), f.description or f"{f.name} of the {column.record} record"))
        entities.append(_table("EXCEPTIONS", exceptions_table(compiled), f"Source {compiled.id}: exceptions raised by the pipeline stages, with rejection codes.", owner))
        entities.append(
            {
                "typeName": "Process",
                "attributes": {
                    "qualifiedName": f"{CONNECTION}/{DATABASE}/process/{compiled.id}/merge",
                    "name": f"{compiled.id}: merge {label} into Silver",
                    "description": f"Refresh replaces the scope ({', '.join(spec.merge.scope) or 'whole source'}), update merges on keys ({', '.join(spec.merge.keys)}); exceptions to EXCEPTIONS.{exceptions_table(compiled)}.",
                    "inputs": [{"typeName": "Table", "uniqueAttributes": {"qualifiedName": _table_qn("BRONZE", record_table(compiled, r))}} for r in (next((p.records for p in spec.pairings if p.name == label), None) or [label])],
                    "outputs": [{"typeName": "Table", "uniqueAttributes": {"qualifiedName": _table_qn("SILVER", silver_table(compiled))}}],
                    "sql": f"releases/{compiled.id.replace('_', '-')}/pipeline/{compiled.id}_merge.sql",
                    "ownerUsers": [owner],
                },
            }
        )

    # Lineage: each Bronze logical record feeds the Silver entities its mappings land in.
    targets = {m.entity.table: m.entity for m in compiled.mappings}
    for entity in targets.values():
        term = compiled.pack.glossary.term(entity.term)
        entities.append(
            {
                "typeName": "Table",
                "attributes": {
                    "qualifiedName": _table_qn(compiled.model.schema, entity.table),
                    "name": entity.table,
                    "schemaName": compiled.model.schema,
                    "databaseName": DATABASE,
                    "description": entity.definition,
                    "ownerUsers": [owner],
                    "certificateStatus": "VERIFIED",
                    "meanings": [{"termName": term.term, "definition": term.definition}] if term else [],
                },
            }
        )
        for order, column in enumerate(compiled.model.table_columns(entity), start=1):
            entities.append(_column(compiled.model.schema, entity.table, column.name, order, column.sql_type, column.description, column.pii))
    for label in spec.logical_records():
        for entity in targets.values():
            columns = [m for m in compiled.mappings if m.entity is entity and m.record == label]
            if not columns:
                continue
            entities.append(
                {
                    "typeName": "Process",
                    "attributes": {
                        "qualifiedName": f"{CONNECTION}/{DATABASE}/process/{compiled.id}/{label}/{entity.table}",
                        "name": f"{compiled.id}: {label} -> {entity.table}",
                        "description": "; ".join(f"{m.entity.table}.{m.column.name} <- {m.record}.{m.source.name}" + (f" via {m.transform.text}" if m.transform else "") for m in columns),
                        "inputs": [{"typeName": "Table", "uniqueAttributes": {"qualifiedName": _table_qn("BRONZE", record_table(compiled, label))}}],
                        "outputs": [{"typeName": "Table", "uniqueAttributes": {"qualifiedName": _table_qn(compiled.model.schema, entity.table)}}],
                        "sql": f"releases/{compiled.id.replace('_', '-')}/pipeline/",
                        "ownerUsers": [owner],
                    },
                }
            )
    return {"source": compiled.id, "entities": entities}


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"atlan/{compiled.id}.json": json.dumps(render_payload(compiled), indent=2, sort_keys=True) + "\n"}
