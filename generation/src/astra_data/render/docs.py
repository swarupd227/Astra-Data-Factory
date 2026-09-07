"""Documentation of one source, generated from the compiled config."""

from __future__ import annotations

from astra_data.compiler import CompiledConfig
from astra_data.render.names import custodian_folder, exceptions_table, file_metadata_table, files_table, parse_problems_table, pipe_name, raw_lines_table, record_table, silver_table, sql_type, task_name


def _position(field) -> str:
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
    for record in spec.records:
        if record.type != "detail":
            continue
        label = record.label
        out.append(f"### {label} → `BRONZE.{record_table(compiled, label)}` (dynamic table, target lag {compiled.target_lag_minutes} minutes)")
        out.append("")
        out.append("| Column | Source field | Position | Picture | Type | Required | Cited |")
        out.append("|---|---|---|---|---|---|---|")
        for f in record.fields:
            if f.name == "filler":
                continue
            cited = f"page {f.citation.page}" + (f", line {f.citation.line}" if getattr(f.citation, 'line', None) else "") if f.citation else ""
            out.append(f"| `{f.name.upper()}` | {label}.{f.name} | {_position(f)} | {f.picture.text if f.picture else ''} | {sql_type(f)} | {'yes' if f.required else ''} | {cited} |")
        out.append("")
    for kind in ("header", "trailer"):
        record = next((r for r in spec.records if r.type == kind), None)
        if record:
            fields = ", ".join(f"`{kind.upper()}_{f.name.upper()}`" for f in record.fields if f.name not in ("filler", "record_type"))
            out.append(f"{kind.capitalize()} values are kept per file on `BRONZE.{file_metadata_table(compiled)}`: {fields or 'none'}.")
            out.append("")
    out.append(f"Lines with a record-level problem (an unknown record type, a line longer than the record length) are excluded from the record tables and counted per file as `EXCLUDED_ROWS` on `BRONZE.{file_metadata_table(compiled)}`; every problem is a row of `BRONZE.{parse_problems_table(compiled)}` with its rejection code. A field-level problem leaves the value NULL and keeps the row.")
    out.append("")
    if spec.merge:
        out.append(f"Merge: `{spec.merge.mode_field}` in the header says refresh or update ({', '.join(f'{k} = {v}' for k, v in spec.merge.modes.items())}); scope {', '.join(spec.merge.scope) or 'the whole source'}; keys {', '.join(spec.merge.keys)}.")
        out.append("")
        out.append(f"## Silver")
        out.append("")
        out.append(f"`SILVER.{silver_table(compiled)}` holds one active row per scope and key. A refresh file replaces its scope as of the file's business date: rows in the file are inserted or updated, rows not in the file are retired (`RETIRED_AT`, `RETIRED_BY_FILE`); an update file merges on keys and carries every other row forward. Files are merged in arrival order once their parse is complete. A file whose header declares no known mode, or whose business date is earlier than what Silver holds for its scope, is rejected whole. Unpaired records, blank keys and duplicate keys within a file are exceptions in `EXCEPTIONS.{exceptions_table(compiled)}`; the first row for a key is kept. Every merge is logged in `CONTROL.MERGE_LOG`.")
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
    out.append(f"Data metric functions measure row counts, nulls in required fields and duplicate merge keys on the Bronze record tables. Parse problems are rows of `BRONZE.{parse_problems_table(compiled)}` with their rejection code.")
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
    out.append(f"Pipe `BRONZE.{pipe_name(compiled)}` loads every file under `{custodian_folder(compiled)}` of the landing prefix that matches the delivery patterns into `BRONZE.{raw_lines_table(compiled)}` on arrival, one row per line. Dynamic tables parse the lines with a target lag of {compiled.target_lag_minutes} minutes. Task `BRONZE.{task_name(compiled)}` runs `{compiled.id.upper()}_PROCESS` every {compiled.target_lag_minutes} minutes on the {src['tier']} tier warehouse. Stages: intake (register landed files as pending), merge (pending files into Silver in arrival order). Resolution is added by its release.")
    out.append("")
    return "\n".join(out)


def render(compiled: CompiledConfig) -> dict[str, str]:
    return {f"docs/{compiled.id}.md": render_doc(compiled)}
