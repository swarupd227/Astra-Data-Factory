"""Command line: `astra-spec validate | list | resolve | show | search | unclassified | parse` and `astra-spec cdm ...`.

Exit codes: 0 done, 1 problems found or nothing in force, 2 usage error.
`--format github` prints workflow annotations; `--format json` is for tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from astra_core.problems import Problem

from astra_knowledge import cdm, rules
from astra_knowledge.registry import Registry, SourceSpec

FORMATS = ("text", "github", "json")


def _print_problems(problems: list[Problem], style: str, title: str = "Spec registry") -> None:
    if style == "json":
        print(json.dumps([p.__dict__ for p in problems], indent=2))
        return
    for p in problems:
        print(p.format(style, title=title))


def _summary(message: str, style: str, title: str = "Spec registry") -> None:
    if style == "github":
        print(f"::notice title={title}::{message}")
    elif style == "text":
        print(message)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _spec_dict(spec: SourceSpec, root: Path) -> dict:
    return {
        "id": spec.id,
        "version": spec.version,
        "effective_from": spec.effective_from.isoformat(),
        "file_type": spec.file_type,
        "custodians": list(spec.custodians),
        "family": spec.family,
        "format": spec.format,
        "records": [r.label for r in spec.records],
        "fields": sum(len(r.fields) for r in spec.records),
        "path": _rel(spec.path, root),
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load(args: argparse.Namespace) -> Registry | None:
    registry, problems = Registry.load(Path(args.specs), repo_root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the spec registry", args.format)
        return None
    return registry


def business_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a date written as YYYY-MM-DD") from exc


# -- commands ----------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    count = len(registry.specs)
    unclassified = registry.unclassified()
    if args.format == "json":
        print(json.dumps({"problems": [], "unclassified": [_spec_dict(s, Path(args.root)) for s in unclassified]}, indent=2))
    _summary(f"checked {count} spec version{'s' if count != 1 else ''} across {len(registry.ids())} spec{'s' if len(registry.ids()) != 1 else ''}: no problems" if count else "no specs found", args.format)
    for spec in unclassified:
        if args.format == "github":
            print(f"::warning file={_rel(spec.path, Path(args.root))},title=Spec registry::{spec.label} has no family; needs classification by the Pattern Matcher or an architect")
        elif args.format == "text":
            print(f"note: {spec.label} has no family; needs classification")
    return 0


def cmd_unclassified(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    specs = registry.unclassified()
    if args.format == "json":
        print(json.dumps([_spec_dict(s, Path(args.root)) for s in specs], indent=2))
    elif not specs:
        print("every spec has a family")
    else:
        for s in specs:
            print(f"{s.label}  {s.file_type}  custodians: {', '.join(s.custodians)}  needs classification")
    return 0


def _hit_dict(hit, root: Path) -> dict:
    payload = _spec_dict(hit.spec, root)
    payload["rank"] = hit.rank
    payload["matched_by"] = hit.match
    payload["flags"] = list(hit.flags)
    return payload


def cmd_search(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    try:
        hits = registry.search(custodian=args.custodian, family=args.family, file_type=args.file_type, business_date=args.date, all_versions=args.all_versions)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps([_hit_dict(h, Path(args.root)) for h in hits], indent=2))
        return 0 if hits else 1
    if not hits:
        print("no matching specs")
        return 1
    for i, hit in enumerate(hits, start=1):
        when = f"in force on {args.date}" if args.date else "latest version"
        family = hit.spec.family or "no family"
        flags = f"  [{'; '.join(hit.flags)}]" if hit.flags else ""
        print(f"{i}. {hit.spec.label}  matched by {hit.match}  {when}  {hit.spec.file_type}  custodians: {', '.join(hit.spec.custodians)}  family: {family}{flags}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    rows = [_spec_dict(s, Path(args.root)) for s in registry.specs]
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no specs")
    else:
        for r in rows:
            print(f"{r['id']} {r['version']}  in force from {r['effective_from']}  {r['file_type']}  custodians: {', '.join(r['custodians'])}  {r['fields']} fields in {len(r['records'])} record type(s)")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    spec = registry.resolve(args.custodian, args.file_type, args.date)
    if spec is None:
        history = registry.in_force(args.custodian, args.file_type, args.date)
        detail = f"; the earliest version comes into force on {history[0].effective_from}" if history else "; no spec lists this custodian and file type"
        if args.format == "json":
            print(json.dumps({"in_force": None, "custodian": args.custodian, "file_type": args.file_type, "date": args.date.isoformat()}))
        else:
            print(f"no spec in force for {args.custodian} {args.file_type} on {args.date}{detail}")
        return 1
    if args.format == "json":
        print(json.dumps({"in_force": _spec_dict(spec, Path(args.root)), "custodian": args.custodian, "file_type": args.file_type, "date": args.date.isoformat()}, indent=2))
    else:
        print(f"{spec.id} {spec.version} is in force for {args.custodian} {args.file_type} on {args.date} (from {spec.effective_from}); {_rel(spec.path, Path(args.root))}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    registry = _load(args)
    if registry is None:
        return 1
    spec = registry.get(args.id, args.version)
    if spec is None:
        known = ", ".join(s.version for s in registry.versions(args.id)) or "none"
        print(f"error: no spec {args.id} version {args.version}; known versions: {known}", file=sys.stderr)
        return 1
    if args.format == "json":
        payload = _spec_dict(spec, Path(args.root))
        payload["document"] = spec.document
        payload["file"] = spec.file
        payload["record_details"] = [
            {
                "type": r.type,
                "name": r.name,
                "match": asdict(r.match) if r.match else None,
                "fields": [
                    {
                        "name": f.name,
                        "position": list(f.position) if f.position else None,
                        "column": f.column,
                        "picture": f.picture.text if f.picture else None,
                        "type": f.type,
                        "format": f.format,
                        "citation": f.citation.text(),
                        "codes": [list(c) for c in f.codes],
                    }
                    for f in r.fields
                ],
            }
            for r in spec.records
        ]
        print(json.dumps(payload, indent=2))
        return 0

    print(f"{spec.label}: {spec.file_type} files from {', '.join(spec.custodians)}, in force from {spec.effective_from}")
    print(f"document: {spec.document['title']} ({spec.document['reference']}); file: {spec.format}" + (f", record length {spec.record_length}" if spec.record_length else ""))
    for r in spec.records:
        print(f"\n{r.label}" + (f"  match {r.match.text()}" if r.match else ""))
        for f in r.fields:
            where = f"{f.start}-{f.end}" if f.position else f"column {f.column}"
            picture = f.picture.text if f.picture else "-"
            print(f"  {f.name.ljust(24)} {where.ljust(10)} {picture.ljust(14)} {f.type.ljust(8)} {f.citation.text()}")
    return 0


def _plain(value):
    """Typed values as JSON-safe text."""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def cmd_parse(args: argparse.Namespace) -> int:
    from astra_knowledge.patterns import parse, patterns_for

    registry = _load(args)
    if registry is None:
        return 1
    spec = registry.get(args.id, args.version)
    if spec is None:
        print(f"error: no spec {args.id} version {args.version}", file=sys.stderr)
        return 1
    applicable = patterns_for(spec)
    if not applicable:
        print(f"error: no pattern handles {spec.label} ({spec.format})", file=sys.stderr)
        return 1
    with open(args.file, encoding=args.encoding, newline="") as handle:
        parsed = parse(spec, handle)

    shown = parsed.rows[: args.limit] if args.limit else parsed.rows
    if args.format == "json":
        print(
            json.dumps(
                {
                    "spec": spec.label,
                    "pattern": applicable[0].id,
                    "lines": parsed.lines,
                    "counts": parsed.counts,
                    "metadata": {k: {n: _plain(v) for n, v in fields.items()} for k, fields in parsed.metadata.items()},
                    "rows": [_row_dict(r) for r in shown],
                    "problems": [asdict(p) for p in parsed.problems],
                },
                indent=2,
            )
        )
        return 0 if parsed.ok else 1

    print(f"{spec.label} parsed with {applicable[0].id}: {parsed.lines} line(s), " + ", ".join(f"{n} {label}" for label, n in parsed.counts.items()))
    for label, fields in parsed.metadata.items():
        print(f"\n{label}:")
        for name, value in fields.items():
            if name != "filler":
                print(f"  {name.ljust(20)} {_plain(value)}")
    if shown:
        print(f"\nrows (showing {len(shown)} of {len(parsed.rows)}):")
        for row in shown:
            values = ", ".join(f"{n}={_plain(v)}" for n, v in row.values.items() if n not in ("filler", "record_type"))
            provenance = f" (from {row.origin}, split {row.split} part {row.part + 1})" if row.origin is not None else ""
            print(f"  line {row.line_number} {row.record}{provenance}: {values}")
    if parsed.problems:
        print(f"\nproblems ({len(parsed.problems)}):")
        for p in parsed.problems:
            print(f"  {p.text()}")
    return 0 if parsed.ok else 1


def _row_dict(row) -> dict:
    """A parsed row for JSON output; split parts carry the record, rule and part they came from."""
    item = {"record": row.record, "line": row.line_number, "values": {n: _plain(v) for n, v in row.values.items()}}
    if row.origin is not None:
        item["origin"] = {"record": row.origin, "split": row.split, "part": row.part}
    return item


# -- canonical data model ----------------------------------------------------

CDM_TITLE = "Domain packs"


def _load_packs(args: argparse.Namespace) -> list[cdm.DomainPack] | None:
    packs, problems = cdm.load_packs(Path(args.domains), Path(args.root))
    if problems:
        _print_problems(problems, args.format, CDM_TITLE)
        _summary(f"{_plural(len(problems), 'problem')} in the domain packs", args.format, CDM_TITLE)
        return None
    if getattr(args, "domain", None):
        packs = [p for p in packs if p.name == args.domain]
        if not packs:
            print(f"no domain pack named '{args.domain}' under {args.domains}", file=sys.stderr)
            return None
    return packs


def _pack_dict(pack: cdm.DomainPack, root: Path) -> dict:
    return {
        "name": pack.name,
        "path": _rel(pack.root, root),
        "versions": [m.version for m in pack.models],
        "entities": {m.version: [e.name for e in m.entities] for m in pack.models},
        "terms": len(pack.glossary.terms),
    }


def cmd_cdm_validate(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    if args.format == "json":
        print(json.dumps({"packs": [_pack_dict(p, Path(args.root)) for p in packs]}, indent=2))
        return 0
    versions = sum(len(p.models) for p in packs)
    terms = sum(len(p.glossary.terms) for p in packs)
    codes = sum(len(p.rejections.codes) for p in packs)
    parity = "".join(f"; Loader parity checked against {_rel(p.loader_reference.path, Path(args.root))}" for p in packs if p.loader_reference)
    _summary(f"checked {_plural(versions, 'model version')}, {_plural(terms, 'glossary term')} and {_plural(codes, 'rejection code')} across {_plural(len(packs), 'domain pack')}: no problems{parity}", args.format, CDM_TITLE)
    return 0


def cmd_rejections_list(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    codes = [(pack, c) for pack in packs for c in pack.rejections.codes if (args.level is None or c.level == args.level) and (args.owner is None or c.owner == args.owner)]
    if args.format == "json":
        print(json.dumps([{"domain": pack.name, **asdict(c)} for pack, c in codes], indent=2))
        return 0
    if not codes:
        print("no rejection codes match")
        return 1
    width = max(len(c.code) for _, c in codes)
    for pack, c in codes:
        flags = c.severity + ("  auto" if c.auto_resolve else "") + ("  retired" if not c.active else "")
        loader = f"  [Loader {', '.join(c.loader_codes)}]" if c.loader_codes else ""
        print(f"{c.code.ljust(width)}  {c.level.ljust(6)}  {flags.ljust(20)}  {c.owner.ljust(13)}  {c.name}{loader}")
    print(f"{_plural(len(codes), 'code')}")
    return 0


def cmd_rejections_parity(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    pack = packs[0]
    root = Path(args.root)
    if args.reference:
        reference, problems = cdm.load_loader_reference(Path(args.reference), root)
        if reference is None:
            _print_problems(problems, args.format, CDM_TITLE)
            return 1
    elif pack.loader_reference is not None:
        reference = pack.loader_reference
    else:
        print(f"{pack.name} has no Loader Rejections reference: add {cdm.LOADER_REFERENCE_FILE} to {_rel(pack.root, root)} or pass --reference", file=sys.stderr)
        return 1
    report = cdm.parity(pack.rejections, reference)
    if args.format == "json":
        print(json.dumps({"reference": _rel(reference.path, root), "mapped": report.mapped, "unmapped": list(report.unmapped), "unknown": list(report.unknown)}, indent=2))
        return 0 if report.ok else 1
    print(f"{pack.name}: {_plural(len(reference.codes), 'Loader code')} in {_rel(reference.path, root)}; {len(report.mapped)} reproduced, {len(report.unmapped)} not reproduced, {len(report.unknown)} named but not in the reference")
    for loader, code in report.mapped.items():
        print(f"  {loader.ljust(12)}  -> {code}")
    for loader in report.unmapped:
        print(f"  {loader.ljust(12)}  NOT REPRODUCED  {reference.codes[loader]}")
    for loader in report.unknown:
        print(f"  {loader.ljust(12)}  NOT IN REFERENCE  named by {pack.rejections.by_loader_code()[loader].code}")
    return 0 if report.ok else 1


def _model_for(args: argparse.Namespace, pack: cdm.DomainPack, version: str | None) -> cdm.Model | None:
    if version is None:
        return pack.latest
    model = pack.model(version)
    if model is None:
        print(f"{pack.name} has no model version {version}; versions are {', '.join(m.version for m in pack.models)}", file=sys.stderr)
    return model


def cmd_cdm_show(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    pack = packs[0]
    model = _model_for(args, pack, args.version)
    if model is None:
        return 1
    if args.format == "json":
        print(json.dumps(_model_dict(model), indent=2))
        return 0
    print(f"{model.label}: {model.description}")
    print(f"schema {model.schema}; {_plural(len(model.entities), 'entity').replace('entitys', 'entities')}; {_plural(len(model.lineage), 'lineage column')} on every table" + (f"; migration note {model.migration}" if model.migration else ""))
    name_width = max(len(e.name) for e in model.entities)
    table_width = max(len(e.table) for e in model.entities)
    for entity in model.entities:
        print()
        print(f"{entity.name.ljust(name_width)}  {entity.table.ljust(table_width)}  key: {', '.join(entity.key)}")
        print(f"  {entity.definition}")
        for ref in entity.references:
            print(f"  references {ref.entity} through {', '.join(ref.columns)}{' when present' if ref.optional else ''}")
        columns = model.table_columns(entity)
        width = max(len(c.name) for c in columns)
        for column in columns:
            flags = "required" if column.required else "optional"
            if column.pii:
                flags += f"  PII {column.pii}"
            print(f"  {column.name.ljust(width)}  {column.sql_type.ljust(16)}  {flags.ljust(26)}  {column.description}")
    return 0


def _model_dict(model: cdm.Model) -> dict:
    def column(c: cdm.Column) -> dict:
        return {"name": c.name, "type": c.sql_type, "required": c.required, "pii": c.pii, "description": c.description, "codes": [asdict(code) for code in c.codes]}

    return {
        "domain": model.domain,
        "version": model.version,
        "schema": model.schema,
        "description": model.description,
        "migration": model.migration,
        "lineage": [column(c) for c in model.lineage],
        "entities": [
            {
                "name": e.name,
                "table": e.table,
                "definition": e.definition,
                "key": list(e.key),
                "references": [asdict(r) for r in e.references],
                "columns": [column(c) for c in e.columns],
            }
            for e in model.entities
        ],
    }


def cmd_cdm_diff(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    pack = packs[0]
    old = _model_for(args, pack, args.from_version)
    new = _model_for(args, pack, args.to_version)
    if old is None or new is None:
        return 1
    changes = cdm.diff(old, new)
    breaking = [c for c in changes if c.breaking]
    additive = [c for c in changes if not c.breaking]
    if args.format == "json":
        print(json.dumps({"from": old.version, "to": new.version, "migration": new.migration, "changes": [{"kind": c.kind, "entity": c.entity, "message": c.message} for c in changes]}, indent=2))
        return 0
    print(f"{pack.name} CDM {old.version} -> {new.version}: {_plural(len(breaking), 'breaking change')}, {_plural(len(additive), 'additive change')}" + (f"; migration note {new.migration}" if new.migration else ""))
    for change in changes:
        print(f"  {change.kind.ljust(8)}  {change.message}")
    return 0


def cmd_cdm_render(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    root = Path(args.root)
    if args.check:
        problems = [p for pack in packs for p in cdm.check_rendered(pack, root)]
        if problems:
            _print_problems(problems, args.format, CDM_TITLE)
            _summary(f"{_plural(len(problems), 'rendered file')} out of date; run astra-spec cdm render and commit the result", args.format, CDM_TITLE)
            return 1
        versions = sum(len(p.models) for p in packs)
        _summary(f"rendered files are current for {_plural(versions, 'model version')}", args.format, CDM_TITLE)
        return 0
    for pack in packs:
        cdm.write_rendered(pack)
        for model in pack.models:
            tests = len(cdm.render_tests(model))
            if args.format != "json":
                print(f"rendered {model.label}: {_plural(len(model.entities), 'table')}, {_plural(tests, 'test')} -> {_rel(cdm.rendered_dir(pack, model), root)}")
    return 0


# -- rule catalog ------------------------------------------------------------

RULES_TITLE = "Rule catalog"


def _config_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in (".yaml", ".yml") and not p.name.startswith("_")))
        elif path.is_file():
            files.append(path)
    return files


def _load_catalog(args: argparse.Namespace) -> rules.Catalog | None:
    registry, registry_problems = Registry.load(Path(args.specs), repo_root=Path(args.root))
    catalog, problems = rules.Catalog.load(Path(args.rules), Path(args.root), registry if not registry_problems else None)
    if problems:
        _print_problems(problems, args.format, RULES_TITLE)
        _summary(f"{_plural(len(problems), 'problem')} in the rule catalog", args.format, RULES_TITLE)
        return None
    return catalog


def _rule_dict(rule: rules.Rule, root: Path, usage: list[str] | None = None) -> dict:
    payload = {
        "id": rule.id,
        "text": rule.text,
        "class": rule.class_,
        "owner": asdict(rule.owner),
        "status": rule.status,
        "citation": rule.citation.text,
        "custodians": list(rule.custodians),
        "entities": list(rule.entities),
        "tags": list(rule.tags),
        "history": [{"status": h.status, "by": h.by, "at": h.at.isoformat(), "note": h.note} for h in rule.history],
        "path": _rel(rule.path, root),
    }
    if usage is not None:
        payload["configs"] = usage
    return payload


def cmd_rules_validate(args: argparse.Namespace) -> int:
    catalog = _load_catalog(args)
    if catalog is None:
        return 1
    problems: list[Problem] = []
    usage: rules.Lineage | None = None
    if args.configs:
        usage = rules.lineage(catalog, _config_files(args.configs), Path(args.root))
        problems += [Problem(config, None, f"references rule '{rule_id}', which is not in the catalog ({args.rules}/<group>/<name>.yaml)") for config, rule_id in usage.unknown]
        problems += [Problem(config, None, f"references rule '{rule_id}', which its owner has rejected; a config may not use a rejected rule") for config, rule_id in usage.rejected]
    if problems:
        _print_problems(problems, args.format, RULES_TITLE)
        _summary(f"{_plural(len(problems), 'problem')} in rule references from configs", args.format, RULES_TITLE)
        return 1
    if args.format == "json":
        print(json.dumps({"problems": [], "rules": [_rule_dict(r, Path(args.root), usage.configs_for(r.id) if usage else None) for r in catalog.rules]}, indent=2))
        return 0
    counts = ", ".join(f"{len(catalog.by_status(s))} {s.replace('_', ' ')}" for s in rules.STATUSES if catalog.by_status(s))
    detail = f" ({counts})" if counts else ""
    configs = len(_config_files(args.configs)) if args.configs else 0
    lineage_note = f"; {_plural(configs, 'config')} reference{'s' if configs == 1 else ''} only known, unrejected rules" if args.configs else ""
    _summary(f"checked {_plural(len(catalog.rules), 'rule')}{detail}: no problems{lineage_note}", args.format, RULES_TITLE)
    if usage:
        for rule in catalog.rules:
            if rule.status in ("recovered", "confirmed") and not usage.configs_for(rule.id) and args.format == "text":
                print(f"note: {rule.id} is {rule.status} but no config uses it")
    return 0


def cmd_rules_list(args: argparse.Namespace) -> int:
    catalog = _load_catalog(args)
    if catalog is None:
        return 1
    usage = rules.lineage(catalog, _config_files(args.configs), Path(args.root)) if args.configs else None
    selected = [r for r in catalog.rules if (args.status is None or r.status == args.status) and (getattr(args, "class_", None) is None or r.class_ == args.class_) and (args.group is None or r.group == args.group)]
    if args.format == "json":
        print(json.dumps([_rule_dict(r, Path(args.root), usage.configs_for(r.id) if usage else None) for r in selected], indent=2))
        return 0
    if not selected:
        print("no rules match")
        return 1
    width = max(len(r.id) for r in selected)
    for r in selected:
        used = f"  used by {_plural(len(usage.configs_for(r.id)), 'config')}" if usage else ""
        print(f"{r.id.ljust(width)}  {r.class_.ljust(13)}  {r.status.replace('_', ' ').ljust(13)}  {r.owner.email}  {r.citation.text}{used}")
    print(_plural(len(selected), "rule"))
    return 0


def cmd_rules_show(args: argparse.Namespace) -> int:
    catalog = _load_catalog(args)
    if catalog is None:
        return 1
    rule = catalog.get(args.id)
    if rule is None:
        print(f"error: no rule {args.id} in {args.rules}; rules are {', '.join(catalog.ids()) or 'none'}", file=sys.stderr)
        return 1
    usage = rules.lineage(catalog, _config_files(args.configs), Path(args.root)).configs_for(rule.id) if args.configs else None
    if args.format == "json":
        print(json.dumps(_rule_dict(rule, Path(args.root), usage), indent=2))
        return 0
    print(f"{rule.id}  {rule.class_}  {rule.status.replace('_', ' ')}  owner {rule.owner.name} <{rule.owner.email}>  {_rel(rule.path, Path(args.root))}")
    print(f"  {rule.text}")
    print(f"  citation: {rule.citation.text}")
    if rule.custodians or rule.entities:
        print(f"  applies to: {', '.join(rule.custodians) or 'any custodian'}; {', '.join(rule.entities) or 'any entity'}")
    if rule.tags:
        print(f"  tags: {', '.join(rule.tags)}")
    print("  history:")
    for h in rule.history:
        print(f"    {h.at.strftime('%Y-%m-%d %H:%M UTC')}  {h.status.replace('_', ' ').ljust(13)}  by {h.by}" + (f"  {h.note}" if h.note else ""))
    if usage is not None:
        print("  used by: " + (", ".join(usage) if usage else "no config"))
    return 0


def cmd_rules_set_status(args: argparse.Namespace) -> int:
    catalog = _load_catalog(args)
    if catalog is None:
        return 1
    rule = catalog.get(args.id)
    if rule is None:
        print(f"error: no rule {args.id} in {args.rules}; rules are {', '.join(catalog.ids()) or 'none'}", file=sys.stderr)
        return 1
    try:
        changed = rules.set_status(rule, args.status, args.by, args.note)
    except rules.StatusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(_rule_dict(changed, Path(args.root)), indent=2))
    else:
        print(f"{changed.id}: {rule.status.replace('_', ' ')} -> {changed.status.replace('_', ' ')} by {changed.last_change.by} at {changed.last_change.at.strftime('%Y-%m-%d %H:%M UTC')}; recorded in {_rel(changed.path, Path(args.root))}")
    return 0


def cmd_reference_list(args: argparse.Namespace) -> int:
    packs = _load_packs(args)
    if packs is None:
        return 1
    feeds = [(pack, feed) for pack in packs if pack.reference_data for feed in pack.reference_data.feeds]
    if args.format == "json":
        print(json.dumps([{"domain": pack.name, **asdict(feed)} for pack, feed in feeds], indent=2))
        return 0
    if not feeds:
        print("no domain pack declares reference data")
        return 1
    for pack, feed in feeds:
        identifiers = ", ".join(feed.resolves.identifiers) or "the key"
        print(f"{feed.id}  {feed.name} ({feed.system})  REFERENCE.{feed.table}  key {', '.join(feed.key)}")
        print(f"  resolves {feed.resolves.entity} by {identifiers}; not found {feed.rejections.not_found}, ambiguous {feed.rejections.ambiguous}, conflict {feed.rejections.conflict}")
        print(f"  snapshots under reference/{feed.file.folder}/; runs {feed.schedule.cron} {feed.schedule.timezone}; expected at least every {feed.expected_every_hours} hours ({feed.stale_severity} when stale); {_plural(len(feed.columns), 'column')}")
    return 0


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-spec", description="The Source Spec registry.")
    parser.add_argument("--format", choices=FORMATS, default="text")
    parser.add_argument("--root", default=".", help="repository root used to display paths (default: current directory)")
    parser.add_argument("--specs", default="specs", help="registry directory (default: specs)")
    parser.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    parser.add_argument("--rules", default="rules", help="rule catalog directory (default: rules)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate every spec and the registry as a whole").set_defaults(func=cmd_validate)
    sub.add_parser("list", help="list every spec version").set_defaults(func=cmd_list)

    r = sub.add_parser("resolve", help="which version is in force for a custodian's file type on a business date")
    r.add_argument("--custodian", required=True)
    r.add_argument("--file-type", required=True)
    r.add_argument("--date", type=business_date, default=date.today(), help="business date, YYYY-MM-DD (default: today)")
    r.set_defaults(func=cmd_resolve)

    s = sub.add_parser("show", help="print a spec version with every field, position, picture and citation")
    s.add_argument("--id", required=True)
    s.add_argument("--version", required=True)
    s.set_defaults(func=cmd_show)

    q = sub.add_parser("search", help="find specs by custodian, family, file type and date, ranked: exact custodian first, then family")
    q.add_argument("--custodian")
    q.add_argument("--family")
    q.add_argument("--file-type")
    q.add_argument("--date", type=business_date, help="only versions in force on this business date")
    q.add_argument("--all-versions", action="store_true", help="every matching version, not one per spec")
    q.set_defaults(func=cmd_search)

    sub.add_parser("unclassified", help="specs with no family, waiting for classification").set_defaults(func=cmd_unclassified)

    p = sub.add_parser("parse", help="parse a sample file with the pattern that handles its spec; typed rows, file metadata and problems")
    p.add_argument("--id", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("file", help="sample file")
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--limit", type=int, default=20, help="rows to print (0 for all)")
    p.set_defaults(func=cmd_parse)

    c = sub.add_parser("cdm", help="the canonical data model of each domain pack: validate, show, diff versions, render DDL")
    csub = c.add_subparsers(dest="cdm_command", required=True)
    csub.add_parser("validate", help="validate every domain pack: glossary, model versions and the versioning rule").set_defaults(func=cmd_cdm_validate)

    cs = csub.add_parser("show", help="print a model version: entities, keys, references and columns")
    cs.add_argument("--domain", required=True)
    cs.add_argument("--version", help="model version (default: latest)")
    cs.set_defaults(func=cmd_cdm_show)

    cd = csub.add_parser("diff", help="the changes between two model versions, each classified breaking or additive")
    cd.add_argument("--domain", required=True)
    cd.add_argument("--from", dest="from_version", required=True, metavar="VERSION")
    cd.add_argument("--to", dest="to_version", required=True, metavar="VERSION")
    cd.set_defaults(func=cmd_cdm_diff)

    cr = csub.add_parser("render", help="write the DDL and key tests of every model version under cdm/rendered/<version>/")
    cr.add_argument("--domain", help="one domain pack (default: all)")
    cr.add_argument("--check", action="store_true", help="fail when the rendered files are missing or stale instead of writing them")
    cr.set_defaults(func=cmd_cdm_render)

    x = sub.add_parser("rejections", help="the rejection taxonomy of each domain pack: list codes, check parity with the Loader")
    xsub = x.add_subparsers(dest="rejections_command", required=True)
    xl = xsub.add_parser("list", help="every rejection code with its level, severity, owner and Loader codes")
    xl.add_argument("--domain", help="one domain pack (default: all)")
    xl.add_argument("--level", choices=("file", "record", "field"))
    xl.add_argument("--owner", choices=("custodian", "data_engineer", "steward", "platform"))
    xl.set_defaults(func=cmd_rejections_list)
    xp = xsub.add_parser("parity", help="which Loader codes the taxonomy reproduces and which it misses")
    xp.add_argument("--domain", required=True)
    xp.add_argument("--reference", help="Loader Rejections reference CSV (default: the pack's loader-rejections.csv)")
    xp.set_defaults(func=cmd_rejections_parity)

    rf = sub.add_parser("reference", help="the reference-data feeds of each domain pack")
    rfsub = rf.add_subparsers(dest="reference_command", required=True)
    rl = rfsub.add_parser("list", help="every feed: what it resolves, how it arrives, when it runs")
    rl.add_argument("--domain", help="one domain pack (default: all)")
    rl.set_defaults(func=cmd_reference_list)

    ru = sub.add_parser("rules", help="the rule catalog: validate, list, show, and record status changes")
    rusub = ru.add_subparsers(dest="rules_command", required=True)
    rv = rusub.add_parser("validate", help="every rule file, its history and citation; with --configs, every config reference resolves to a rule that is not rejected")
    rv.add_argument("--configs", nargs="*", default=[], help="config files or directories whose rule references are checked")
    rv.set_defaults(func=cmd_rules_validate)
    rli = rusub.add_parser("list", help="the catalog, optionally filtered, with usage when configs are given")
    rli.add_argument("--status", choices=rules.STATUSES)
    rli.add_argument("--class", dest="class_", choices=rules.CLASSES)
    rli.add_argument("--group", help="rules of one group (spec, custodian or domain)")
    rli.add_argument("--configs", nargs="*", default=[], help="config files or directories to count usage from")
    rli.set_defaults(func=cmd_rules_list)
    rsh = rusub.add_parser("show", help="one rule with its citation, history and the configs that use it")
    rsh.add_argument("id")
    rsh.add_argument("--configs", nargs="*", default=[])
    rsh.set_defaults(func=cmd_rules_show)
    rst = rusub.add_parser("set-status", help="record a status change: who, when and why are written to the rule's history")
    rst.add_argument("id")
    rst.add_argument("--status", required=True, choices=rules.STATUSES)
    rst.add_argument("--by", required=True, help="who is changing it: an email or an agent id")
    rst.add_argument("--note", help="why")
    rst.set_defaults(func=cmd_rules_set_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
