"""Command line: `astra-data validate | bundles check | deploy | test`.

Exit codes: 0 nothing wrong, 1 problems or failures found, 2 usage or
connection error. `--format github` prints workflow annotations so problems
show up on the pull request; `--format json` is for other tools.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from astra_data.bundle import BundleError, DeployError, Target, check_bundles, deploy, run_tests
from astra_data.compiler import compile_paths
from astra_data.render import bundle_name as release_bundle_name, check_bundle as check_release_bundle, write_bundle as write_release_bundle
from astra_data.custodians import custodians_from_configs, sync, sync_statements
from astra_data.lint import lint_bundles
from astra_data.migration import PHASES, load_run_bundle, plan as migration_plan, run_migration, validate_paths as validate_migrations
from astra_data.gold import bundle_name as gold_bundle_name, check_bundle as check_gold_bundle, packs_with_read_models, write_bundle as write_gold_bundle
from astra_data.silver import bundle_name as silver_bundle_name, check_bundle as check_silver_bundle, packs_with_cdm, write_bundle as write_silver_bundle
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


def _compile_inputs(args: argparse.Namespace):
    """The spec registry, rule catalog and domain packs, or None after printing what is wrong with them."""
    from astra_knowledge.cdm import load_packs
    from astra_knowledge.registry import Registry
    from astra_knowledge.rules import Catalog

    root = Path(args.root)
    registry, problems = Registry.load(Path(args.specs), repo_root=root)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the spec registry; fix them before configs can be compiled", args.format)
        return None
    catalog, problems = Catalog.load(Path(args.rules), root, registry)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the rule catalog; fix them before configs can be compiled", args.format)
        return None
    packs, problems = load_packs(Path(args.domains), root)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the domain packs; fix them before configs can be compiled", args.format)
        return None
    return registry, catalog, packs


def cmd_compile(args: argparse.Namespace) -> int:
    inputs = _compile_inputs(args)
    if inputs is None:
        return 1
    registry, catalog, packs = inputs
    root = Path(args.root)
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


def cmd_render(args: argparse.Namespace) -> int:
    from astra_data.render import RenderError

    inputs = _compile_inputs(args)
    if inputs is None:
        return 1
    registry, catalog, packs = inputs
    root = Path(args.root)
    compiled, problems = compile_paths(args.paths, registry=registry, catalog=catalog, packs=packs, root=root)
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} compiling configs; nothing rendered", args.format)
        return 1
    out = Path(args.out)
    if args.check:
        problems = []
        for c in compiled:
            try:
                problems.extend(check_release_bundle(c, out, root))
            except RenderError as exc:
                problems.extend(exc.problems)
        if problems:
            _print_problems(problems, args.format)
            _summary(f"{len(problems)} release bundle file{'s' if len(problems) != 1 else ''} out of date; run astra-data render and commit the result", args.format)
            return 1
        _summary(f"release bundles are current for {len(compiled)} config{'s' if len(compiled) != 1 else ''}", args.format)
        return 0
    rendered = []
    for c in compiled:
        try:
            bundle_root = write_release_bundle(c, out)
        except RenderError as exc:
            _print_problems(exc.problems, args.format)
            return 1
        files = sum(1 for p in bundle_root.rglob("*") if p.is_file())
        rendered.append({"bundle": release_bundle_name(c), "path": _rel(bundle_root, root), "files": files})
        _summary(f"rendered {release_bundle_name(c)}: {files} files -> {_rel(bundle_root, root)}", args.format)
    if args.format == "json":
        print(json.dumps(rendered, indent=2))
    return 0


def cmd_bundles_lint(args: argparse.Namespace) -> int:
    results, problems = lint_bundles(Path(args.releases), repo_root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in generated tests", args.format)
        return 1
    tests = sum(r.tests for r in results)
    _summary(f"{tests} generated test{'s' if tests != 1 else ''} in {len(results)} bundle{'s' if len(results) != 1 else ''} parse as one Snowflake SELECT each" if results else f"no release bundles under {args.releases}", args.format)
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


def cmd_migrate_validate(args: argparse.Namespace) -> int:
    migrations, problems = validate_migrations(args.paths, root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        _summary(f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in {len(migrations) + len({p.path for p in problems})} migration file{'s' if len(migrations) + len({p.path for p in problems}) != 1 else ''}", args.format)
        return 1
    for m in migrations:
        _summary(f"{m.id}: {m.source.platform} {m.source.database} ({', '.join(m.source.schemas)}) on {m.source.server} -> {m.target_schema}; tool {' '.join(m.snowconvert.command)}", args.format)
    _summary(f"checked {len(migrations)} migration file{'s' if len(migrations) != 1 else ''}: no problems" if migrations else f"no migration files under {', '.join(args.paths)}", args.format)
    return 0


def _one_migration(args: argparse.Namespace):
    migrations, problems = validate_migrations([args.migration], root=Path(args.root))
    if problems:
        _print_problems(problems, args.format)
        return None
    return migrations[0]


def cmd_migrate_plan(args: argparse.Namespace) -> int:
    migration = _one_migration(args)
    if migration is None:
        return 1
    target = Target(args.environment, args.prefix)
    steps = migration_plan(migration, target, Path(args.work), args.phases or PHASES)
    if args.format == "json":
        print(json.dumps({"migration": migration.id, "target": target.database, "phases": [{"phase": p, "command": argv} for p, argv in steps]}, indent=2))
        return 0
    for phase, argv in steps:
        print(f"{phase:9} {' '.join(argv)}")
    _summary(f"{len(steps)} phase{'s' if len(steps) != 1 else ''} of {migration.id} would run against {target.database}; secrets shown as <VARIABLE>", args.format)
    return 0


def cmd_migrate_run(args: argparse.Namespace) -> int:
    migration = _one_migration(args)
    if migration is None:
        return 1
    target = Target(args.environment, args.prefix)
    executor = None if args.no_deploy else _executor()
    try:
        run = run_migration(
            migration,
            target,
            Path(args.work),
            Path(args.releases),
            phases=args.phases or PHASES,
            executor=executor,
            repo_root=Path(args.root),
            tool_override=shlex.split(args.tool) if args.tool else None,
        )
    except EnvironmentError as exc:
        print(f"::error title=Migration::{exc}" if args.format == "github" else f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if executor is not None:
            executor.close()
    if args.format == "json":
        print(json.dumps(run.to_dict(), indent=2))
    else:
        for p in run.phases:
            line = f"{p.phase:9} {p.status}" + (f"  exit {p.exit_code} in {p.seconds}s  {p.log}" if p.status != "skipped" else "")
            print(f"::error title=Migration phase failed::{migration.id} {p.phase}: exit {p.exit_code}, see {p.log}" if (args.format == "github" and p.status == "failed") else line)
        tables = len(run.tables)
        _summary(
            f"{migration.id} run {run.run_id} {run.status}: {tables} table{'s' if tables != 1 else ''} converted into {target.database}.{migration.target_schema}"
            + (f", {run.ewis} EWI marker{'s' if run.ewis != 1 else ''} to resolve" if run.ewis else "")
            + (", deployed" if run.deployed else ", not deployed")
            + f"; results under {_rel(run.bundle_dir, Path(args.root))}/migration/runs/{run.run_id}",
            args.format,
        )
    return 0 if run.status == "succeeded" else 1


def cmd_gold_render(args: argparse.Namespace) -> int:
    packs, problems = packs_with_read_models(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    if not packs:
        _summary("no domain pack declares Gold read models", args.format)
        return 0
    releases = Path(args.releases)
    if args.check:
        problems = [p for pack in packs for p in check_gold_bundle(pack, releases, Path(args.root))]
        if problems:
            _print_problems(problems, args.format)
            _summary(f"{len(problems)} Gold bundle file{'s' if len(problems) != 1 else ''} out of date; run astra-data gold render and commit the result", args.format)
            return 1
        _summary(f"Gold bundles are current for {len(packs)} domain pack{'s' if len(packs) != 1 else ''}", args.format)
        return 0
    for pack in packs:
        root = write_gold_bundle(pack, releases)
        models = len(pack.read_models.models)
        _summary(f"rendered {gold_bundle_name(pack)}: {models} read model{'s' if models != 1 else ''} -> {_rel(root, Path(args.root))}", args.format)
    return 0


def cmd_silver_render(args: argparse.Namespace) -> int:
    packs, problems = packs_with_cdm(args.domains, root=Path(args.root), domain=args.domain)
    if problems:
        _print_problems(problems, args.format)
        return 1
    if not packs:
        _summary("no domain pack declares a CDM model", args.format)
        return 0
    releases = Path(args.releases)
    if args.check:
        problems = [p for pack in packs for p in check_silver_bundle(pack, releases, Path(args.root))]
        if problems:
            _print_problems(problems, args.format)
            _summary(f"{len(problems)} Silver bundle file{'s' if len(problems) != 1 else ''} out of date; run astra-data silver render and commit the result", args.format)
            return 1
        _summary(f"Silver bundles are current for {len(packs)} domain pack{'s' if len(packs) != 1 else ''}", args.format)
        return 0
    for pack in packs:
        root = write_silver_bundle(pack, releases)
        entities = len(pack.latest.entities)
        _summary(f"rendered {silver_bundle_name(pack)}: {entities} entit{'y' if entities == 1 else 'ies'} (model {pack.latest.version}) -> {_rel(root, Path(args.root))}", args.format)
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

    rn = sub.add_parser("render", help="compile configs and render every artifact of each into a release bundle under --out")
    rn.add_argument("paths", nargs="*", default=["configs"], help="config files or directories (default: configs)")
    rn.add_argument("--specs", default="specs", help="spec registry directory (default: specs)")
    rn.add_argument("--rules", default="rules", help="rule catalog directory (default: rules)")
    rn.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    rn.add_argument("--out", default="releases", help="bundles directory (default: releases)")
    rn.add_argument("--check", action="store_true", help="fail when a bundle is missing or stale instead of writing it")
    rn.set_defaults(func=cmd_render)

    b = sub.add_parser("bundles", help="release bundle commands")
    bsub = b.add_subparsers(dest="bundles_command", required=True)
    bc = bsub.add_parser("check", help="check every bundle's manifest, files and placeholders without connecting anywhere")
    bc.add_argument("releases", nargs="?", default="releases")
    bc.set_defaults(func=cmd_bundles_check)
    bl = bsub.add_parser("lint", help="check that every generated test of every bundle parses as one Snowflake SELECT (needs astra-data[lint])")
    bl.add_argument("releases", nargs="?", default="releases")
    bl.set_defaults(func=cmd_bundles_lint)

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

    gd = sub.add_parser("gold", help="Gold read models and the watermark: render each domain pack's bundle")
    gdsub = gd.add_subparsers(dest="gold_command", required=True)
    gr = gdsub.add_parser("render", help="write releases/<pack>-gold/ from the pack's read models; --check fails when it is stale")
    gr.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    gr.add_argument("--domain", help="one domain pack (default: all)")
    gr.add_argument("--releases", default="releases", help="bundles directory (default: releases)")
    gr.add_argument("--check", action="store_true")
    gr.set_defaults(func=cmd_gold_render)

    sv = sub.add_parser("silver", help="Silver CDM on Iceberg: render each domain pack's canonical model as a release bundle")
    svsub = sv.add_subparsers(dest="silver_command", required=True)
    svr = svsub.add_parser("render", help="write releases/<pack>-silver/ from the pack's current CDM model version; --check fails when it is stale")
    svr.add_argument("--domains", default="domains", help="domain packs directory (default: domains)")
    svr.add_argument("--domain", help="one domain pack (default: all)")
    svr.add_argument("--releases", default="releases", help="bundles directory (default: releases)")
    svr.add_argument("--check", action="store_true")
    svr.set_defaults(func=cmd_silver_render)

    mg = sub.add_parser("migrate", help="historical migration with SnowConvert AI: validate migration files, plan the commands, run the phases and store the results with the release")
    mgsub = mg.add_subparsers(dest="migrate_command", required=True)
    mv = mgsub.add_parser("validate", help="validate migration files against the migration schema")
    mv.add_argument("paths", nargs="*", default=["migrations"], help="files or directories (default: migrations)")
    mv.set_defaults(func=cmd_migrate_validate)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--migration", required=True, help="migration file")
    common.add_argument("--environment", required=True, type=environment_name)
    common.add_argument("--prefix", default=os.environ.get("ASTRA_PREFIX", "ASTRA"))
    common.add_argument("--work", default=os.environ.get("ASTRA_MIGRATION_WORK", "work/migrations"), help="working directory for the tool's input and output (default: work/migrations)")
    common.add_argument("--phase", dest="phases", action="append", choices=list(PHASES), help="run only this phase; repeatable, in order (default: all four)")
    mp = mgsub.add_parser("plan", parents=[common], help="print the command lines a run would execute, secrets shown as <VARIABLE>")
    mp.set_defaults(func=cmd_migrate_plan)
    mr = mgsub.add_parser("run", parents=[common], help="run the phases, write the converted DDL bundle under releases/, deploy it, and store logs and results with it")
    mr.add_argument("--releases", default="releases", help="bundles directory (default: releases)")
    mr.add_argument("--tool", help="command that replaces snowconvert.command, for example a wrapper script")
    mr.add_argument("--no-deploy", action="store_true", help="do not deploy the converted DDL before the migrate phase (no Snowflake connection needed)")
    mr.set_defaults(func=cmd_migrate_run)

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
