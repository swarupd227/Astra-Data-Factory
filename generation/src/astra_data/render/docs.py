"""Documentation of one source, generated from the compiled config."""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import files_table, logical_columns, problems_table, record_table, sql_type, task_name


def _position(column) -> str:
    field = column.field
    if field.position:
        start, length = field.position
        return f"{start}-{start + length - 1}"
    if field.column:
        return f"column {field.column}"
    return ""


def render_doc(compiled: CompiledConfig) -> str:
    spec = compiled.spec
    src = compiled.source
    out: list[str] = []
    out.append(f"# {compiled.id}")
    out.append("")
    out.append(f"{src.get('description', '').strip() or 'Source config.'} Rendered by `astra-data render`; do not edit.")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append(f"| Custodian | `{src['custodian']}` |")
    out.append(f"| File type | {src['file_type']} |")
    out.append(f"| Tier | {src['tier']} |")
    out.append(f"| Source Spec | `{spec.id}` version `{spec.version}`, in force from {spec.effective_from} |")
    out.append(f"| Layout document | {spec.document.get('title', '')} ({spec.document.get('reference', '')}) |")
    out.append(f"| Pattern | `{compiled.pattern.id}`: {compiled.pattern.name} |")
    out.append(f"| Target | `{compiled.profile.id}`, {compiled.model.label} |")
    out.append(f"| Config in force from | {compiled.effective_from} |")
    out.append(f"| Owner | {compiled.owner['name']} <{compiled.owner['email']}> |")
    out.append("")

    out.append("## Layout")
    out.append("")
    fmt = spec.file
    if spec.format == "fixed_width":
        out.append(f"Fixed width, record length {fmt.get('record_length')}, encoding {fmt.get('encoding', 'UTF-8')}.")
    else:
        out.append(f"Delimited by `{fmt.get('delimiter')}`, {fmt.get('header_rows', 0)} header row(s), encoding {fmt.get('encoding', 'UTF-8')}.")
    out.append("")
    for label in spec.logical_records():
        out.append(f"### {label} → `BRONZE.{record_table(compiled, label)}`")
        out.append("")
        out.append("| Column | Source field | Position | Picture | Type | Required | Cited |")
        out.append("|---|---|---|---|---|---|---|")
        for column in logical_columns(spec, label):
            f = column.field
            cited = f"page {f.citation.page}" + (f", line {f.citation.line}" if getattr(f.citation, 'line', None) else "") if f.citation else ""
            out.append(f"| `{column.name}` | {column.record}.{f.name} | {_position(column)} | {f.picture.text if f.picture else ''} | {sql_type(f)} | {'yes' if f.required or column.key else ''} | {cited} |")
        out.append("")
    for kind in ("header", "trailer"):
        record = next((r for r in spec.records if r.type == kind), None)
        if record:
            fields = ", ".join(f"`{f.name}`" for f in record.fields if f.name not in ("filler", "record_type"))
            out.append(f"{kind.capitalize()} fields kept on `BRONZE.{files_table(compiled)}`: {fields or 'none'}.")
            out.append("")
    if spec.merge:
        out.append(f"Merge: `{spec.merge.mode_field}` in the header says refresh or update ({', '.join(f'{k} = {v}' for k, v in spec.merge.modes.items())}); scope {', '.join(spec.merge.scope)}; keys {', '.join(spec.merge.keys)}.")
        out.append("")

    out.append("## Mappings")
    out.append("")
    out.append("| Canonical column | Source | Transform | Rule |")
    out.append("|---|---|---|---|")
    for m in compiled.mappings:
        out.append(f"| `{m.entity.table}.{m.column.name}` ({m.column.sql_type}) | {m.record}.{m.source.name} ({m.source_type}) | {m.transform.text if m.transform else ''} | {m.rule.id if m.rule else ''} |")
    out.append("")

    out.append("## Rules")
    out.append("")
    if compiled.rules:
        out.append("| Rule | Class | Status | Citation | Text |")
        out.append("|---|---|---|---|---|")
        for r in compiled.rules:
            out.append(f"| `{r.id}` | {r.class_} | {r.status.replace('_', ' ')} | {r.citation.text} | {r.text} |")
    else:
        out.append("No rules referenced.")
    out.append("")

    out.append("## Data quality")
    out.append("")
    if compiled.dq_rules:
        out.append("| Rule | Level | Severity | Check |")
        out.append("|---|---|---|---|")
        for d in compiled.dq_rules:
            out.append(f"| `{d['id']}` | {d['level']} | {d.get('severity', 'error')} | {d['check']} |")
        out.append("")
    out.append(f"Data metric functions measure row counts, nulls in required fields and duplicate merge keys on the Bronze tables. Parse problems land in `BRONZE.{problems_table(compiled)}` with their rejection code.")
    out.append("")

    if compiled.delivery:
        d = compiled.delivery
        out.append("## Delivery")
        out.append("")
        out.append(f"Cutoff {d['cutoff_time']} {d['timezone']} on {', '.join(d.get('business_days') or ['mon', 'tue', 'wed', 'thu', 'fri'])}. Expected files:")
        out.append("")
        for f in d["files"]:
            out.append(f"- `{f['pattern']}`" + (f": {f['description']}" if f.get("description") else ""))
        out.append("")
        if compiled.alerts:
            out.append(f"Alerts: late = {compiled.alerts.get('late', 'error')}, task failure = {compiled.alerts.get('task_failure', 'error')}.")
            out.append("")

    out.append("## Pipeline")
    out.append("")
    out.append(f"Task `BRONZE.{task_name(compiled)}` runs `{compiled.id.upper()}_PROCESS` every 15 minutes on the {src['tier']} tier warehouse. Stages: intake (register landed files as pending). Parse, merge and resolution stages are added by their releases.")
    out.append("")
    return "\n".join(out)


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"docs/{compiled.id}.md": render_doc(compiled)}
