"""Command line: `astra-spec validate | list | resolve | show`.

Exit codes: 0 done, 1 problems found or nothing in force, 2 usage error.
`--format github` prints workflow annotations; `--format json` is for tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from astra_core.problems import Problem

from astra_knowledge.registry import Registry, SourceSpec

FORMATS = ("text", "github", "json")


def _print_problems(problems: list[Problem], style: str) -> None:
    if style == "json":
        print(json.dumps([p.__dict__ for p in problems], indent=2))
        return
    for p in problems:
        print(p.format(style, title="Spec registry"))


def _summary(message: str, style: str) -> None:
    if style == "github":
        print(f"::notice title=Spec registry::{message}")
    elif style == "text":
        print(message)


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
    if args.format == "json":
        print("[]")
    _summary(f"checked {count} spec version{'s' if count != 1 else ''} across {len(registry.ids())} spec{'s' if len(registry.ids()) != 1 else ''}: no problems" if count else "no specs found", args.format)
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
                "match": r.match,
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
        print(f"\n{r.label}" + (f"  match {r.match}" if r.match else ""))
        for f in r.fields:
            where = f"{f.start}-{f.end}" if f.position else f"column {f.column}"
            picture = f.picture.text if f.picture else "-"
            print(f"  {f.name.ljust(24)} {where.ljust(10)} {picture.ljust(14)} {f.type.ljust(8)} {f.citation.text()}")
    return 0


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-spec", description="The Source Spec registry.")
    parser.add_argument("--format", choices=FORMATS, default="text")
    parser.add_argument("--root", default=".", help="repository root used to display paths (default: current directory)")
    parser.add_argument("--specs", default="specs", help="registry directory (default: specs)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
