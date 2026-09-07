"""DQ rules of a config, resolved against the spec: what each measures, on which table, over which columns.

A dq_rule has a kind, and the kind says what the renderer makes of it
(ADR 0025): a system data metric function attached to a column, or a
custom data metric function rendered for the rule and attached to the
table. Every rule is one DMF or one DMF association.

  control_total   file level: a trailer field against the count of a record
                  or the sum of one of its fields, per file; the DMF returns
                  the gap
  not_null        a field that must be present: SNOWFLAKE.CORE.NULL_COUNT
  unique          one field: SNOWFLAKE.CORE.DUPLICATE_COUNT; several: a DMF
                  counting the extra rows of duplicate groups
  accepted_values a field whose values must be in a list
  range           a field that must lie within min and max
  condition       a SQL condition over the table's columns that must hold

Record-level rules measure the Bronze table of a physical record; pair-
and business-level rules measure the Silver table of the logical record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from astra_knowledge.registry import Field, SourceSpec

from astra_core.problems import Problem
from astra_core.yamlsource import LineDict, line_of
from astra_data.layout import logical_columns, sql_type, total_type

KINDS = ("control_total", "not_null", "unique", "accepted_values", "range", "condition")
ROW_LEVELS = ("record", "pair", "business")
NUMERIC_TYPES = ("integer", "decimal")
ORDERED_TYPES = ("integer", "decimal", "date", "time")


@dataclass(frozen=True)
class DqColumn:
    name: str
    sql_type: str


@dataclass(frozen=True)
class CompiledDqRule:
    id: str
    kind: str
    level: str
    check: str
    severity: str
    owner: str | None
    table: str  # metadata (per-file counts and trailer values), record (a physical record's Bronze table) or silver
    record: str | None  # the record measured, or the logical record for the Silver table
    columns: tuple[DqColumn, ...]  # what the metric takes, in order
    aggregate: str | None = None  # control_total: count or sum
    trailer_field: str | None = None
    total_field: str | None = None  # control_total sum: the summed field
    values: tuple[Any, ...] = ()
    minimum: Any = None
    maximum: Any = None
    condition: str | None = None

    @property
    def system_function(self) -> str | None:
        """The SNOWFLAKE.CORE function that measures the rule, or None when the rule needs one of its own."""
        if self.kind == "not_null":
            return "NULL_COUNT"
        if self.kind == "unique" and len(self.columns) == 1:
            return "DUPLICATE_COUNT"
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "level": self.level,
            "check": self.check,
            "severity": self.severity,
            "owner": self.owner,
            "table": self.table,
            "record": self.record,
            "columns": [{"name": c.name, "type": c.sql_type} for c in self.columns],
            "system_function": self.system_function,
            "aggregate": self.aggregate,
            "trailer_field": self.trailer_field,
            "total_field": self.total_field,
            "values": list(self.values),
            "min": self.minimum,
            "max": self.maximum,
            "condition": self.condition,
        }


def compile_dq_rules(data: LineDict, display: str, spec: SourceSpec, problems: list[Problem]) -> tuple[CompiledDqRule, ...]:
    """Resolve every dq_rule against the spec; each problem names the rule and what is wrong."""
    rules: list[CompiledDqRule] = []
    for i, raw in enumerate(data.get("dq_rules") or []):
        rule = _compile_rule(raw, i, data, display, spec, problems)
        if rule is not None:
            rules.append(rule)
    return tuple(rules)


def _problem(data: LineDict, display: str, i: int, key: str, message: str) -> Problem:
    line = line_of(data, ["dq_rules", i, key]) or line_of(data, ["dq_rules", i, "id"])
    return Problem(display, line, f"dq_rules[{i}] ({data['dq_rules'][i]['id']}): {message}")


def _compile_rule(raw: dict, i: int, data: LineDict, display: str, spec: SourceSpec, problems: list[Problem]) -> CompiledDqRule | None:
    kind = raw["kind"]
    level = raw["level"]
    common = dict(id=raw["id"], kind=kind, level=level, check=raw["check"], severity=raw.get("severity", "error"), owner=raw.get("owner"))
    before = len(problems)

    if kind == "control_total":
        if level != "file":
            problems.append(_problem(data, display, i, "level", f"a control_total rule is file level, not {level}: it compares a trailer value with the file's records"))
        trailer = next((r for r in spec.records if r.type == "trailer"), None)
        if trailer is None:
            problems.append(_problem(data, display, i, "kind", f"spec {spec.label} has no trailer record, so there is no control total to check"))
            return None
        trailer_field = _named(raw, "trailer_field")
        if trailer_field is None:
            problems.append(_problem(data, display, i, "kind", "a control_total rule needs trailer_field: the trailer field carrying the total"))
        tf = trailer.field(trailer_field) if trailer_field else None
        if trailer_field and (tf is None or tf.name in ("filler", "record_type")):
            problems.append(_problem(data, display, i, "trailer_field", f"trailer_field '{trailer_field}' is not a field of the trailer record; its fields are {_names(trailer)}"))
        elif tf is not None and tf.type not in NUMERIC_TYPES:
            problems.append(_problem(data, display, i, "trailer_field", f"trailer_field '{trailer_field}' is {tf.type}, not a number; a control total must be integer or decimal"))
        aggregate = raw.get("aggregate", "count")
        record = _record_for(raw, i, data, display, spec, problems, physical=True)
        total_field: Field | None = None
        if aggregate == "sum":
            name = _named(raw, "field")
            if name is None:
                problems.append(_problem(data, display, i, "aggregate", "aggregate sum needs field: the record field whose values the trailer totals"))
            elif record is not None:
                total_field = record.field(name)
                if total_field is None or total_field.name == "filler":
                    problems.append(_problem(data, display, i, "field", f"field '{name}' is not a field of record '{record.label}'; its fields are {_names(record)}"))
                    total_field = None
                elif total_field.type not in NUMERIC_TYPES:
                    problems.append(_problem(data, display, i, "field", f"field '{name}' is {total_field.type}, not a number; a control total sums integer or decimal fields"))
                    total_field = None
        elif raw.get("field"):
            problems.append(_problem(data, display, i, "field", "field is only used with aggregate sum; a count control total needs none"))
        if len(problems) > before or record is None or tf is None:
            return None
        if aggregate == "sum":
            columns = (DqColumn(f"TRAILER_{tf.name.upper()}", sql_type(tf)), DqColumn(f"{record.label.upper()}_{total_field.name.upper()}_TOTAL", total_type(total_field)))
        else:
            columns = (DqColumn(f"TRAILER_{tf.name.upper()}", sql_type(tf)), DqColumn(f"{record.label.upper()}_COUNT", "NUMBER(18,0)"))
        return CompiledDqRule(**common, table="metadata", record=record.label, columns=columns, aggregate=aggregate, trailer_field=tf.name, total_field=total_field.name if total_field else None)

    if level not in ROW_LEVELS:
        problems.append(_problem(data, display, i, "level", f"a {kind} rule measures rows, so its level is record, pair or business, not {level}; file-level rules are control totals"))
        return None
    for key in ("trailer_field", "aggregate"):
        if raw.get(key) is not None:
            problems.append(_problem(data, display, i, key, f"{key} belongs to a control_total rule, not to {kind}"))

    table = "record" if level == "record" else "silver"
    if table == "record":
        record = _record_for(raw, i, data, display, spec, problems, physical=True)
        if record is None:
            return None
        available = {f.name.upper(): DqColumn(f.name.upper(), sql_type(f)) for f in record.fields if f.name != "filler"}
        types = {f.name.upper(): f for f in record.fields if f.name != "filler"}
        record_label = record.label
    else:
        record_label = spec.merge.record if spec.merge and spec.merge.record else spec.logical_records()[0]
        if raw.get("record") and raw["record"] != record_label:
            problems.append(_problem(data, display, i, "record", f"a {level}-level rule measures the Silver table of logical record '{record_label}'; record cannot be '{raw['record']}'"))
            return None
        cols = logical_columns(spec, record_label)
        available = {c.name: DqColumn(c.name, sql_type(c.field)) for c in cols}
        types = {c.name: c.field for c in cols}
    where = f"{'record' if table == 'record' else 'logical record'} '{record_label}'"

    def column(name: str, key: str) -> DqColumn | None:
        col = available.get(name.upper())
        if col is None:
            problems.append(_problem(data, display, i, key, f"{key} '{name}' is not a column of {where}; its columns are {', '.join(available)}"))
        return col

    if kind == "not_null":
        name = _named(raw, "field")
        if name is None:
            problems.append(_problem(data, display, i, "kind", "a not_null rule needs field: the field that must be present"))
            return None
        col = column(name, "field")
        return CompiledDqRule(**common, table=table, record=record_label, columns=(col,)) if col else None

    if kind == "unique":
        names = raw.get("fields") or ([raw["field"]] if raw.get("field") else [])
        if not names:
            problems.append(_problem(data, display, i, "kind", "a unique rule needs fields: the field or fields that identify a row"))
            return None
        cols = [column(n, "fields") for n in names]
        return CompiledDqRule(**common, table=table, record=record_label, columns=tuple(cols)) if all(cols) else None

    if kind == "accepted_values":
        name = _named(raw, "field")
        values = raw.get("values") or []
        if name is None or not values:
            problems.append(_problem(data, display, i, "kind", "an accepted_values rule needs field and values: the field and the values it may hold"))
            return None
        col = column(name, "field")
        return CompiledDqRule(**common, table=table, record=record_label, columns=(col,), values=tuple(values)) if col else None

    if kind == "range":
        name = _named(raw, "field")
        if name is None or (raw.get("min") is None and raw.get("max") is None):
            problems.append(_problem(data, display, i, "kind", "a range rule needs field and min, max or both"))
            return None
        col = column(name, "field")
        if col is None:
            return None
        f = types[name.upper()]
        if f.type not in ORDERED_TYPES:
            problems.append(_problem(data, display, i, "field", f"field '{name}' is {f.type}; a range applies to integer, decimal, date or time fields"))
            return None
        if raw.get("min") is not None and raw.get("max") is not None and raw["min"] > raw["max"]:
            problems.append(_problem(data, display, i, "min", f"min {raw['min']} is greater than max {raw['max']}"))
            return None
        return CompiledDqRule(**common, table=table, record=record_label, columns=(col,), minimum=raw.get("min"), maximum=raw.get("max"))

    if kind == "condition":
        condition = (raw.get("condition") or "").strip()
        if not condition:
            problems.append(_problem(data, display, i, "kind", "a condition rule needs condition: a SQL condition over the table's columns that every row must satisfy"))
            return None
        used = [c for c in available if re.search(rf"(?<![A-Z0-9_\"]){re.escape(c)}(?![A-Z0-9_\"])", condition.upper())]
        if not used:
            problems.append(_problem(data, display, i, "condition", f"condition names no column of {where}; its columns are {', '.join(available)}"))
            return None
        return CompiledDqRule(**common, table=table, record=record_label, columns=tuple(available[c] for c in used), condition=condition)

    problems.append(_problem(data, display, i, "kind", f"kind '{kind}' is not one of {', '.join(KINDS)}"))
    return None


def _named(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    return str(value) if value not in (None, "") else None


def _names(record) -> str:
    return ", ".join(f.name for f in record.fields if f.name not in ("filler", "record_type"))


def _record_for(raw: dict, i: int, data: LineDict, display: str, spec: SourceSpec, problems: list[Problem], *, physical: bool):
    """The physical detail record a rule measures: the named one, or the only detail record when there is one."""
    details = [r for r in spec.records if r.type == "detail"]
    name = raw.get("record")
    if name is None:
        if len(details) == 1:
            return details[0]
        problems.append(_problem(data, display, i, "id", f"spec {spec.label} has {len(details)} detail records ({', '.join(r.label for r in details)}); say which with record"))
        return None
    record = spec.record(str(name))
    if record is None or record.type != "detail":
        problems.append(_problem(data, display, i, "record", f"record '{name}' is not a detail record of spec {spec.label}; detail records are {', '.join(r.label for r in details)}"))
        return None
    return record
