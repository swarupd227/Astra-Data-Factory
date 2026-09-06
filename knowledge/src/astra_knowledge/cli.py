"""Command line: `astra-spec validate | list | resolve | show`.

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
                    "rows": [{"record": r.record, "line": r.line_number, "values": {n: _plain(v) for n, v in r.values.items()}} for r in shown],
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
            print(f"  line {row.line_number} {row.record}: {values}")
    if parsed.problems:
        print(f"\nproblems ({len(parsed.problems)}):")
        for p in parsed.problems:
            print(f"  {p.text()}")
    return 0 if parsed.ok else 1


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
