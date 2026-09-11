"""Client DQ tool connector: the same golden output and lakehouse table astra-verify parity already compares, described for the client's own tool to read (S4.2.4, ADR 0036).

Envestnet validates a custodian's parity with its own tool (iceDQ or
Datagaps) as well as astra-verify's parity engine (S4.2.1). Both tools
must reconcile the same two things: the golden capture's legacy output
(golden/<custodian>/parity.yaml already names it) and the lakehouse
table it compares against (the same file already names that too, with
its keys, fields and tolerances). Rather than a second, parallel
description that could drift from the first, `describe` reads the
custodian's already-reviewed parity.yaml and renders it into what a
QE engineer configuring the client's tool needs: where the golden CSV
lives in the golden store (a plain S3 prefix, since iceDQ and Datagaps
both have a native file connector), the Snowflake identity of the
lakehouse table (a native connector too), and the same keys, fields
and tolerances so the two tools are validating identically, not just
similarly.

`connector.yaml` carries only what parity.yaml does not already say and
is not a runtime flag: which tool the custodian uses, and the Snowflake
role granted to it (outside Terraform, by the client's own Snowflake
admin — the connector is configured once it is granted, not before).

The client's tool runs outside this repository; nothing here calls it.
Once it runs, `store_result` is the other half of this story: it files
the tool's own exported result next to astra-verify's own parity report
for the same business date, hashed, so the evidence a reviewer reads is
what the client's tool actually produced, not a copy that could have
changed since.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

from astra_data.bundle import Target

from astra_verification.golden import ObjectStore
from astra_verification.parity import ParityMapping

SCHEMA = "connector-v0.schema.json"
CONNECTOR_FILE = "connector.yaml"
TOOLS = ("icedq", "datagaps")


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectorConfig:
    custodian: str
    description: str
    tool: str
    role: str
    result_formats: tuple[str, ...]
    path: Path


def load_connector(path: Path, root: Path | None = None) -> tuple[ConnectorConfig | None, list[Problem]]:
    path = Path(path)
    display = display_path(path, root)
    if not path.is_file():
        return None, [Problem(display, None, "no such file")]
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    if data.get("connector_version") != 0:
        line = line_of(data, ["connector_version"]) if "connector_version" in data else 1
        return None, [Problem(display, line, f"connector_version must be 0; found {data.get('connector_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_verification.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)

    config = ConnectorConfig(
        custodian=data["custodian"],
        description=" ".join(str(data.get("description", "")).split()),
        tool=data["tool"],
        role=data["role"],
        result_formats=tuple(data.get("result_formats", ["csv"])),
        path=path,
    )
    return config, []


def discover(golden_dir: Path) -> list[Path]:
    return sorted(p for p in Path(golden_dir).glob(f"*/{CONNECTOR_FILE}") if p.is_file())


def check(golden_dir: Path, root: Path | None = None) -> tuple[list[ConnectorConfig], list[Problem]]:
    configs: list[ConnectorConfig] = []
    problems: list[Problem] = []
    for path in discover(golden_dir):
        config, found = load_connector(path, root)
        problems.extend(found)
        if config is None:
            continue
        if config.custodian != path.parent.name:
            problems.append(Problem(display_path(path, root), None, f"custodian '{config.custodian}' must match the directory '{path.parent.name}'"))
        configs.append(config)
    return configs, problems


# -- the descriptor: how to point the client's tool at the same two things astra-verify compares ------


def describe(connector: ConnectorConfig, mapping: ParityMapping, store: ObjectStore, target: Target) -> dict:
    """Everything a QE engineer configuring the client's tool needs, derived from parity.yaml and the runtime store/target — nothing restated that could drift."""
    golden_columns = [k.legacy for k in mapping.keys] + [f.legacy for f in mapping.fields]
    lakehouse_columns = [k.lakehouse for k in mapping.keys] + [f.lakehouse for f in mapping.fields]
    return {
        "custodian": connector.custodian,
        "tool": connector.tool,
        "golden": {
            "kind": "s3" if store.uri.startswith("s3://") else "file",
            "location": f"{store.uri}/{connector.custodian}/{{business_date}}/v{{version}}/outputs/{mapping.legacy_output}.csv",
            "note": "{business_date} is YYYY-MM-DD; {version} is the dataset version (astra-verify golden status names the latest per date). One CSV per business date, header row, comma-separated.",
            "format": "csv",
            "header": True,
            "columns_used_by_astra_verify": golden_columns,
        },
        "lakehouse": {
            "kind": "snowflake",
            "database": target.database,
            "schema": mapping.schema,
            "table": mapping.table,
            "role": connector.role,
            "filter": f"{mapping.custodian_column} = '{connector.custodian}' AND {mapping.business_date_column} = '{{business_date}}'",
            "columns_used_by_astra_verify": lakehouse_columns,
        },
        "keys": [{"golden": k.legacy, "lakehouse": k.lakehouse, "type": k.type} for k in mapping.keys],
        "fields": [{"golden": f.legacy, "lakehouse": f.lakehouse, "type": f.type, "tolerance": f.tolerance.to_dict()} for f in mapping.fields],
        "result": {
            "accepted_formats": list(connector.result_formats),
            "stored_with": "astra-verify connector store-result --config <connector.yaml> --business-date <date> --tool " + connector.tool + " --file <the tool's exported result>",
        },
    }


def render_markdown(descriptor: dict) -> str:
    g, l = descriptor["golden"], descriptor["lakehouse"]
    out = [f"# Client DQ tool connector: {descriptor['custodian']} ({descriptor['tool']})", ""]
    out.append("Both astra-verify's own parity engine and the client's tool reconcile the same golden output and lakehouse table; this sheet is how the client's tool is pointed at them.")
    out.append("")
    out.append("## Golden (legacy) side — file connector")
    out.append("")
    out.append(f"- Kind: `{g['kind']}`")
    out.append(f"- Location pattern: `{g['location']}`")
    out.append(f"- {g['note']}")
    out.append(f"- Format: {g['format']}, header row: {g['header']}")
    out.append(f"- Columns astra-verify's own comparison reads: {', '.join(g['columns_used_by_astra_verify'])}")
    out.append("")
    out.append("## Lakehouse side — Snowflake connector")
    out.append("")
    out.append(f"- Database.Schema.Table: `{l['database']}.{l['schema']}.{l['table']}`")
    out.append(f"- Role: `{l['role']}`")
    out.append(f"- Filter: `{l['filter']}`")
    out.append(f"- Columns astra-verify's own comparison reads: {', '.join(l['columns_used_by_astra_verify'])}")
    out.append("")
    out.append("## Keys")
    out.append("")
    out.append("| Golden column | Lakehouse column | Type |")
    out.append("|---|---|---|")
    for k in descriptor["keys"]:
        out.append(f"| `{k['golden']}` | `{k['lakehouse']}` | {k['type']} |")
    out.append("")
    out.append("## Fields")
    out.append("")
    out.append("| Golden column | Lakehouse column | Type | Tolerance |")
    out.append("|---|---|---|---|")
    for f in descriptor["fields"]:
        tol = f["tolerance"]
        tol_text = tol["kind"] if tol["kind"] == "exact" else f"{tol['kind']} ({tol.get('places', tol.get('epsilon'))})"
        out.append(f"| `{f['golden']}` | `{f['lakehouse']}` | {f['type']} | {tol_text} |")
    out.append("")
    out.append("## The tool's result")
    out.append("")
    out.append(f"Accepted formats: {', '.join(descriptor['result']['accepted_formats'])}. Store the tool's exported result alongside astra-verify's own parity report for the same business date:")
    out.append("")
    out.append(f"```\n{descriptor['result']['stored_with']}\n```")
    out.append("")
    return "\n".join(out)


def write_descriptor(descriptor: dict, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "connector.md"
    data = out / "connector.json"
    markdown.write_text(render_markdown(descriptor), encoding="utf-8", newline="\n")
    data.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return markdown, data


# -- the tool's result, stored alongside the parity report --------------------------------------------


def store_result(connector: ConnectorConfig, file: Path, business_date: date, out: Path) -> tuple[Path, Path]:
    """Copy the client tool's exported result next to astra-verify's own parity report for the business date, with a hashed sidecar."""
    file = Path(file)
    if not file.is_file():
        raise ConnectorError(f"{file}: no such file")
    suffix = file.suffix.lstrip(".").lower()
    if suffix not in connector.result_formats:
        raise ConnectorError(f"{file}: '{suffix}' is not an accepted result format for {connector.custodian} ({connector.tool}); accepted: {', '.join(connector.result_formats)}")
    out.mkdir(parents=True, exist_ok=True)
    data = file.read_bytes()
    dest = out / f"{connector.tool}-result.{suffix}"
    dest.write_bytes(data)
    meta = {
        "tool": connector.tool,
        "custodian": connector.custodian,
        "business_date": business_date.isoformat(),
        "source_file": file.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    meta_path = out / f"{connector.tool}-result.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return dest, meta_path
