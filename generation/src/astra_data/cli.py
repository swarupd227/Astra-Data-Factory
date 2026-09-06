"""Command line: `astra-data validate | bundles check | deploy | test`.

Exit codes: 0 nothing wrong, 1 problems or failures found, 2 usage or
connection error. `--format github` prints workflow annotations so problems
show up on the pull request; `--format json` is for other tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from astra_data.bundle import BundleError, DeployError, Target, check_bundles, deploy, run_tests
from astra_data.compiler import compile_paths
from astra_data.custodians import custodians_from_configs, sync, sync_statements
from astra_data.reference_data import bundle_name, check_bundle, packs_with_reference_data, sync as sync_reference_feeds, sync_statements as reference_feed_statements, write_bundle
from astra_data.rejections import sync as sync_rejections, sync_statements as rejection_statements, taxonomies_from_packs
from astra_data.snowflake_connection import ConnectionConfigError, SnowflakeExecutor, connect
from astra_data.validate import Problem, validate_paths

FORMATS = ("text", "github", "json")


def _print_problems(problems: list[Problem], style: str) -> None:
    if style == "json":
        print(json.dumps([asdict(p) for p in problems], indent=2))
        return
    for p in problems:
        print(p.format(style, title="Config validation"))


def _summary(message: str, style: str) -> None:
    if style == "github":
        print(f"::notice title=Astra Data::{message}")
    elif style == "text":
        print(message)


def environment_name(value: str) -> str:
    try:
        Target(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


# -- commands ----------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    registry = None
    if args.specs:
        from astra_knowledge.registry import Registry

        registry, registry_problems = Registry.load(Path(args.specs), repo_root=Path(args.root))
        if registry_problems:
            _print_problems(registry_problems, args.format)
            _summary(f"{len(registry_problems)} problem{'s' if len(registry_problems) != 1 else ''} in the spec registry; fix them before configs can be checked against it", args.format)
            return 1
    catalog = None
    if args.rules:
        from astra_knowledge.rules import Catalog

        catalog, catalog_problems = Catalog.load(Path(args.rules), Path(args.root), registry)
        if catalog_problems:
            _print_problems(catalog_problems, args.format)
            _summary(f"{len(catalog_problems)} problem{'s' if len(catalog_problems) != 1 else ''} in the rule catalog; fix them before configs can be checked against it", args.format)
            return 1
    count, problems = validate_paths(args.paths, root=Path(args.root), registry=registry, catalog=catalog)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in {count} config file{'s' if count != 1 else ''}", args.format)
        return 1
    if args.format == "json":
        print("[]")
    _summary(f"checked {count} config file{'s' if count != 1 else ''}: no problems" if count else "no config files found", args.format)
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    from astra_knowledge.cdm import load_packs
    from astra_knowledge.registry import Registry
    from astra_knowledge.rules import Catalog

    root = Path(args.root)
    registry, problems = Registry.load(Path(args.specs), repo_root=root)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the spec registry; fix them before configs can be compiled", args.format)
        return 1
    catalog, problems = Catalog.load(Path(args.rules), root, registry)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the rule catalog; fix them before configs can be compiled", args.format)
        return 1
    packs, problems = load_packs(Path(args.domains), root)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the domain packs; fix them before configs can be compiled", args.format)
        return 1
    compiled, problems = compile_paths(args.paths, registry=registry, catalog=catalog, packs=packs, root=root)
    count = len(compiled) + len({p.path for p in problems})
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} compiling {count} config{'s' if count != 1 else ''}", args.format)
        return 1
    if args.format == "json":
        print(json.dumps([c.to_dict() for c in compiled], indent=2))
        return 0
    for c in compiled:
        _summary(f"{c.id}: {c.spec.label} via {c.pattern.id} -> {c.model.label} on {c.profile.id}; {len(c.mappings)} mapping{'s' if len(c.mappings) != 1 else ''}, {len(c.rules)} rule{'s' if len(c.rules) != 1 else ''}, {len(c.dq_rules)} dq rule{'s' if len(c.dq_rules) != 1 else ''}", args.format)
    _summary(f"compiled {count} config{'s' if count != 1 else ''}: no problems" if count else "no config files found", args.format)
    return 0


def cmd_bundles_check(args: argparse.Namespace) -> int:
    bundles, problems = check_bundles(Path(args.releases), repo_root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in release bundles", args.format)
        return 1
    if args.format == "json":
        print(json.dumps([{"bundle": b.name, "version": b.version, "source": b.source, "steps": len(b.steps), "tests": len(b.tests)} for b in bundles], indent=2))
    else:
        for b in bundles:
            _summary(f"bundle {b.name} {b.version}: {len(b.steps)} step{'s' if len(b.steps) != 1 else ''}, {len(b.tests)} test{'s' if len(b.tests) != 1 else ''}", args.format)
        _summary(f"checked {len(bundles)} release bundle{'s' if len(bundles) != 1 else ''}: no problems" if bundles else f"no release bundles under {args.releases}", args.format)
    return 0


def _executor():
    try:
        return SnowflakeExecutor(connect())
    except ConnectionConfigError as exc:
        raise SystemExit(f"error: {exc}") from exc


def cmd_deploy(args: argparse.Namespace) -> int:
    target = Target(args.environment, args.prefix)
    bundles, problems = check_bundles(Path(args.releases), repo_root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        return 1
    if not bundles:
        _summary(f"no release bundles under {args.releases}; nothing to deploy to {target.database}", args.format)
        return 0

    executor = _executor()
    results = []
    try:
        for bundle in bundles:
            results.append(deploy(bundle, target, executor))
            _summary(f"deployed {bundle.name} {bundle.version} to {target.database}: {', '.join(results[-1].steps)}", args.format)
    except DeployError as exc:
        print(f"::error title=Deploy failed::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()
    if args.format == "json":
        print(json.dumps([asdict(r) for r in results], indent=2))
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    target = Target(args.environment, args.prefix)
    bundles, problems = check_bundles(Path(args.releases), repo_root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        return 1
    if not bundles:
        _summary(f"no release bundles under {args.releases}; no tests to run against {target.database}", args.format)
        return 0

    executor = _executor()
    results = []
    try:
        for bundle in bundles:
            results.extend(run_tests(bundle, target, executor))
    except DeployError as exc:
        print(f"::error title=Test failed to run::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()

    if args.format == "json":
        print(json.dumps([asdict(r) | {"detail": r.detail} for r in results], indent=2))
    else:
        for r in results:
            line = f"{'PASS' if r.passed else 'FAIL'}  {r.bundle}/{r.test}  {r.detail}"
            print(f"::error title=Generated test failed::{r.bundle}/{r.test}: {r.detail}" if (args.format == "github" and not r.passed) else line)
    failed = sum(1 for r in results if not r.passed)
    _summary(f"{len(results) - failed} of {len(results)} generated tests passed against {target.database}", args.format)
    return 1 if failed else 0


def _custodian_summary(custodians, style: str) -> None:
    for c in custodians:
        schedule = f"cutoff {c.cutoff_time} {c.timezone} on {c.business_days_text or 'no days'}" if c.business_days else "no delivery schedule"
        _summary(f"custodian {c.custodian_id}: {schedule}; {len(c.files)} expected file(s); late={c.late_severity}, task_failure={c.failure_severity}", style)


def cmd_custodians_render(args: argparse.Namespace) -> int:
    custodians, problems = custodians_from_configs(args.paths, root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        return 1
    target = Target(args.environment, args.prefix)
    if args.format == "json":
        print(json.dumps({"custodians": [asdict(c) for c in custodians], "statements": sync_statements(custodians, target)}, indent=2, default=list))
    else:
        _custodian_summary(custodians, args.format)
        print(";\n".join(sync_statements(custodians, target)) + ";")
    return 0


def cmd_custodians_sync(args: argparse.Namespace) -> int:
    custodians, problems = custodians_from_configs(args.paths, root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        return 1
    target = Target(args.environment, args.prefix)
    executor = _executor()
    try:
        count = sync(executor, custodians, target)
    except Exception as exc:  # the database's own error is the message
        print(f"::error title=Custodian sync failed::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()
    _custodian_summary(custodians, args.format)
    _summary(f"synced {count} custodian{'s' if count != 1 else ''} to {target.environment_database}.CONTROL", args.format)
    if args.format == "json":
        print(json.dumps([asdict(c) for c in custodians], indent=2, default=list))
    return 0


def cmd_rejections_render(args: argparse.Namespace) -> int:
    taxonomies, problems = taxonomies_from_packs(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    target = Target(args.environment, args.prefix)
    statements = rejection_statements(taxonomies, target, root=Path(args.root))
    if args.format == "json":
        print(json.dumps({"codes": [{"domain": t.domain, **asdict(c)} for t in taxonomies for c in t.codes], "statements": statements}, indent=2, default=list))
    else:
        for t in taxonomies:
            _summary(f"{t.domain}: {len(t.codes)} rejection codes, {len(t.active())} active", args.format)
        print(";\n".join(statements) + ";")
    return 0


def cmd_rejections_sync(args: argparse.Namespace) -> int:
    taxonomies, problems = taxonomies_from_packs(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    target = Target(args.environment, args.prefix)
    executor = _executor()
    try:
        count = sync_rejections(executor, taxonomies, target, root=Path(args.root))
    except Exception as exc:  # the database's own error is the message
        print(f"::error title=Rejection taxonomy sync failed::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()
    _summary(f"synced {count} rejection code{'s' if count != 1 else ''} from {len(taxonomies)} domain pack{'s' if len(taxonomies) != 1 else ''} to {target.environment_database}.CONTROL.REJECTION_CODES", args.format)
    return 0


def cmd_reference_render(args: argparse.Namespace) -> int:
    packs, problems = packs_with_reference_data(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    if not packs:
        _summary("no domain pack declares reference data", args.format)
        return 0
    releases = Path(args.releases)
    if args.check:
        problems = [p for pack in packs for p in check_bundle(pack, releases, Path(args.root))]
        if problems:
            _print_problems(problems, args.format)
            _summary(f"{len(problems)} reference-data bundle file{'s' if len(problems) != 1 else ''} out of date; run astra-data reference render and commit the result", args.format)
            return 1
        _summary(f"reference-data bundles are current for {len(packs)} domain pack{'s' if len(packs) != 1 else ''}", args.format)
        return 0
    for pack in packs:
        root = write_bundle(pack, releases)
        feeds = len(pack.reference_data.feeds)
        _summary(f"rendered {bundle_name(pack)}: {feeds} feed{'s' if feeds != 1 else ''} -> {_rel(root, Path(args.root))}", args.format)
    return 0


def cmd_reference_sync(args: argparse.Namespace) -> int:
    packs, problems = packs_with_reference_data(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    target = Target(args.environment, args.prefix)
    if args.format == "json" and args.dry_run:
        print(json.dumps({"statements": reference_feed_statements(packs, target, root=Path(args.root))}, indent=2))
        return 0
    if args.dry_run:
        print(";\n".join(reference_feed_statements(packs, target, root=Path(args.root))) + ";")
        return 0
    executor = _executor()
    try:
        count = sync_reference_feeds(executor, packs, target, root=Path(args.root))
    except Exception as exc:  # the database's own error is the message
        print(f"::error title=Reference feed sync failed::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()
    _summary(f"synced {count} reference feed{'s' if count != 1 else ''} to {target.environment_database}.CONTROL.REFERENCE_FEEDS", args.format)
    return 0


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-data", description="Validate source configs; deploy and test release bundles.")
    parser.add_argument("--format", choices=FORMATS, default="text", help="text (default), github (workflow annotations) or json")
    parser.add_argument("--root", default=".", help="repository root used to display paths (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate config files against the config schema and their references")
    v.add_argument("paths", nargs="*", default=["configs"], help="files or directories (default: configs)")
    v.add_argument("--specs", help="spec registry directory; when given, each config's spec reference is checked against it")
    v.add_argument("--rules", help="rule catalog directory; when given, each config's rule references must exist and must not be rejected")
    v.set_defaults(func=cmd_validate)

    cp = sub.add_parser("compile", help="validate configs and resolve every reference into the compiled model the renderers use")
    cp.add_argument("paths", nargs="*", default=["configs"], help="config files or directories (default: configs)")
    cp.add_argument("--specs", default="specs", help="spec registry directory (default: specs)")
    cp.add_argument("--rules", default="rules", help="rule catalog directory (default: rules)")
    cp.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    cp.set_defaults(func=cmd_compile)

    b = sub.add_parser("bundles", help="release bundle commands")
    bsub = b.add_subparsers(dest="bundles_command", required=True)
    bc = bsub.add_parser("check", help="check every bundle's manifest, files and placeholders without connecting anywhere")
    bc.add_argument("releases", nargs="?", default="releases")
    bc.set_defaults(func=cmd_bundles_check)

    for name, func, help_text in (
        ("deploy", cmd_deploy, "run every bundle's steps against an environment"),
        ("test", cmd_test, "run every bundle's tests against an environment"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--environment", required=True, type=environment_name)
        p.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"), help="object name prefix (default: ASTRA_PREFIX or ASTRA)")
        p.add_argument("releases", nargs="?", default="releases")
        p.set_defaults(func=func)

    cu = sub.add_parser("custodians", help="custodian delivery expectations and alert severities, from configs to CONTROL")
    cusub = cu.add_subparsers(dest="custodians_command", required=True)
    for name, func, help_text in (
        ("render", cmd_custodians_render, "print the SQL that would bring CONTROL.CUSTODIANS in line with the configs"),
        ("sync", cmd_custodians_sync, "bring CONTROL.CUSTODIANS and CUSTODIAN_FILES in line with the configs"),
    ):
        p = cusub.add_parser(name, help=help_text)
        p.add_argument("--environment", required=True, type=environment_name)
        p.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
        p.add_argument("paths", nargs="*", default=["configs"], help="config files or directories (default: configs)")
        p.set_defaults(func=func)

    rj = sub.add_parser("rejections", help="the domain pack's rejection taxonomy, from rejections.yaml to CONTROL.REJECTION_CODES")
    rjsub = rj.add_subparsers(dest="rejections_command", required=True)
    for name, func, help_text in (
        ("render", cmd_rejections_render, "print the SQL that would bring CONTROL.REJECTION_CODES in line with the taxonomy"),
        ("sync", cmd_rejections_sync, "bring CONTROL.REJECTION_CODES in line with the taxonomy; codes that left it are retired"),
    ):
        p = rjsub.add_parser(name, help=help_text)
        p.add_argument("--environment", required=True, type=environment_name)
        p.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
        p.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
        p.add_argument("--domain", help="one domain pack (default: all)")
        p.set_defaults(func=func)

    rf = sub.add_parser("reference", help="reference-data replication: render each domain pack's bundle, sync CONTROL.REFERENCE_FEEDS")
    rfsub = rf.add_subparsers(dest="reference_command", required=True)
    rr = rfsub.add_parser("render", help="write releases/<pack>-reference-data/ from the pack's feeds; --check fails when it is stale")
    rr.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    rr.add_argument("--domain", help="one domain pack (default: all)")
    rr.add_argument("--releases", default="releases", help="bundles directory (default: releases)")
    rr.add_argument("--check", action="store_true")
    rr.set_defaults(func=cmd_reference_render)
    rs = rfsub.add_parser("sync", help="bring CONTROL.REFERENCE_FEEDS in line with the packs' feeds; feeds that left are disabled")
    rs.add_argument("--environment", required=True, type=environment_name)
    rs.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    rs.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    rs.add_argument("--domain", help="one domain pack (default: all)")
    rs.add_argument("--dry-run", action="store_true", help="print the SQL instead of running it")
    rs.set_defaults(func=cmd_reference_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except BundleError as exc:
        _print_problems(exc.problems, args.format)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
