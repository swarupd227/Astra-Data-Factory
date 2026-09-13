"""Command line: `astra-agents spec-reader run`, `astra-agents profiler run`,
`astra-agents pattern-matcher run`, `astra-agents rule-recovery run` and
`astra-agents modeler run`.

Needs no Snowflake connection. spec-reader's, rule-recovery's and
modeler's real Anthropic API calls need ANTHROPIC_API_KEY in the
environment (every AnthropicClient class reads it the way the anthropic
SDK always does); profiler and pattern-matcher are plain deterministic
code, reading only the files given on the command line. Exit codes for
all five: 0 nothing to review, 1 the run found something a person
should look at (an invalid draft; a profile with drift; a new pattern
proposal; an untraced rejection code or an unrouted T-SQL line; a
breaking CDM change request or a rule tagged CONFIRM_WITH_LOADER), 2
the run could not start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from astra_agents.modeler import DEFAULT_MODEL as MODELER_DEFAULT_MODEL, MAX_TOKENS as MODELER_MAX_TOKENS
from astra_agents.modeler import AnthropicClient as ModelerClient
from astra_agents.modeler import ModelerError, load_domain_pack, load_known_rule_ids
from astra_agents.modeler import load_spec as load_modeler_spec
from astra_agents.modeler import run as run_modeler, write_draft as write_modeler_draft
from astra_agents.pattern_matcher import FAMILY_THRESHOLD, PatternMatcherError, load_registry, run as run_pattern_matcher, write_assignments
from astra_agents.profiler import TOP_N, ProfilerError, load_spec, run as run_profiler, write_profile
from astra_agents.rule_recovery import DEFAULT_MODEL as RULE_RECOVERY_DEFAULT_MODEL, MAX_TOKENS as RULE_RECOVERY_MAX_TOKENS
from astra_agents.rule_recovery import AnthropicClient as RuleRecoveryClient
from astra_agents.rule_recovery import RuleRecoveryError, run as run_rule_recovery, write_draft as write_rule_recovery_draft
from astra_agents.spec_reader import DEFAULT_MODEL, MAX_TOKENS, AnthropicClient, SpecReaderError, run as run_spec_reader, write_draft


def cmd_spec_reader_test_connection(args: argparse.Namespace) -> int:
    """Prove ANTHROPIC_API_KEY authenticates and the account can reach the API. Lists models; generates nothing, sends no document."""
    client = AnthropicClient(model=args.model)
    try:
        result = client.test_connection()
    except SpecReaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.detail)
        if result.ok and args.verbose:
            print(f"  models visible to this key: {', '.join(result.models)}")
    return 0 if result.ok else 1


def cmd_spec_reader_run(args: argparse.Namespace) -> int:
    client = AnthropicClient(model=args.model, max_tokens=args.max_tokens)
    try:
        draft = run_spec_reader(
            Path(args.document),
            client,
            spec_id=args.id,
            version=args.version,
            effective_from=args.effective_from,
            file_type=args.file_type,
            custodians=args.custodians,
            document_title=args.title,
            family=args.family,
            provider=args.provider,
            description=args.description,
        )
    except SpecReaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / args.id / args.version
    spec_path, report_path, _data_path = write_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{args.id} {args.version}: {len(draft.data.get('records', []))} record(s), {draft.field_count} field(s), valid: {'yes' if draft.valid else 'no'}")
        if draft.unparsed:
            print(f"  {len(draft.unparsed)} passage(s) left unparsed, not guessed")
        print(f"  draft: {spec_path}")
        print(f"  report: {report_path}")
    return 0 if draft.valid else 1


def cmd_profiler_run(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(Path(args.spec))
        profile = run_profiler(Path(args.sample), spec, top_n=args.top_n)
    except ProfilerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / profile.spec_id / profile.spec_version
    report_path, _data_path = write_profile(profile, out)
    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
    else:
        print(f"{profile.spec_id} {profile.spec_version}: {profile.lines} line(s), {len(profile.records)} record type(s), drift: {'yes' if not profile.ok else 'no'}")
        if profile.flagged:
            for label, name in profile.flagged:
                print(f"  flagged: {label}.{name}")
        print(f"  report: {report_path}")
    return 0 if profile.ok else 1


def cmd_pattern_matcher_run(args: argparse.Namespace) -> int:
    try:
        registry = load_registry(Path(args.registry))
        assignments = run_pattern_matcher(registry, spec_id=args.id, spec_version=args.version, family_threshold=args.family_threshold)
    except PatternMatcherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    report_path, _data_path = write_assignments(assignments, out)
    if args.json:
        print(json.dumps([a.to_dict() for a in assignments], indent=2))
    else:
        proposals = [a for a in assignments if a.new_pattern_proposal]
        print(f"{len(assignments)} spec(s) classified, {len(proposals)} new pattern proposal(s)")
        for a in assignments:
            print(f"  {a.spec_id} {a.spec_version}: family={a.family or '(none)'} tier={a.tier} patterns={','.join(a.patterns) or '-'}")
        print(f"  report: {report_path}")
    return 0 if not any(a.new_pattern_proposal for a in assignments) else 1


def cmd_rule_recovery_run(args: argparse.Namespace) -> int:
    client = RuleRecoveryClient(model=args.model, max_tokens=args.max_tokens)
    try:
        draft = run_rule_recovery([Path(p) for p in args.source], client, group=args.group, owner_name=args.owner_name, owner_email=args.owner_email)
    except RuleRecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / args.group
    _rules_dir, report_path = write_rule_recovery_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{args.group}: {len(draft.entries)} entry(ies) recovered, valid: {'yes' if draft.valid else 'no'}, ready to review: {'yes' if draft.ok else 'no'}")
        if draft.rejection_codes_untraced:
            print(f"  {len(draft.rejection_codes_untraced)} rejection code(s) not traced to any entry: {', '.join(draft.rejection_codes_untraced)}")
        if draft.tsql_lines_unrouted:
            print(f"  {len(draft.tsql_lines_unrouted)} T-SQL line(s) not routed to any embedded_sql entry")
        print(f"  report: {report_path}")
    return 0 if draft.ok else 1


def cmd_modeler_run(args: argparse.Namespace) -> int:
    try:
        spec = load_modeler_spec(Path(args.spec))
        pack = load_domain_pack(Path(args.domain))
        known_rule_ids = load_known_rule_ids(Path(args.rules)) if args.rules else frozenset()
        client = ModelerClient(model=args.model, max_tokens=args.max_tokens)
        draft = run_modeler(spec, pack, client, custodian=args.custodian, group=args.group or spec.id, owner_name=args.owner_name, owner_email=args.owner_email, tier=args.tier, known_rule_ids=known_rule_ids)
    except ModelerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / spec.id / spec.version
    report_path, _data_path = write_modeler_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{spec.id} {spec.version} ({args.custodian}): {len(draft.mappings)} mapping(s), tier {draft.tier}, valid: {'yes' if draft.valid else 'no'}, ready to review: {'yes' if draft.ok else 'no'}")
        if draft.breaking_change_requests:
            print(f"  {len(draft.breaking_change_requests)} breaking CDM change request(s)")
        if draft.confirm_with_loader:
            print(f"  {len(draft.confirm_with_loader)} rule(s) tagged CONFIRM_WITH_LOADER")
        print(f"  report: {report_path}")
    return 0 if draft.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-agents", description="Astra Data Factory agents plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    sr = sub.add_parser("spec-reader", help="a layout document turned into a Source Spec draft, with page citations")
    srsub = sr.add_subparsers(dest="spec_reader_command", required=True)

    tc = srsub.add_parser("test-connection", help="prove ANTHROPIC_API_KEY authenticates and the account is reachable; lists models, generates nothing")
    tc.add_argument("--model", default=os.environ.get("ASTRA_SPEC_READER_MODEL", DEFAULT_MODEL), help=f"the model a real run would use (default {DEFAULT_MODEL}); checked against the account's model list")
    tc.add_argument("--verbose", action="store_true", help="also print every model visible to this key")
    tc.add_argument("--json", action="store_true")
    tc.set_defaults(func=cmd_spec_reader_test_connection)

    srr = srsub.add_parser("run", help="extract a draft Source Spec from a layout document; never writes into the registry directly")
    srr.add_argument("document", help="the layout document, .pdf or .docx")
    srr.add_argument("--id", required=True, help="spec id (the specs/<id>/ directory it would become)")
    srr.add_argument("--version", required=True, help="this version (the file name it would become, without extension)")
    srr.add_argument("--effective-from", required=True, help="YYYY-MM-DD this version is in force from")
    srr.add_argument("--file-type", required=True, help="position, transaction, price, ...")
    srr.add_argument("--custodian", dest="custodians", action="append", required=True, help="a custodian that delivers this layout; repeatable")
    srr.add_argument("--title", help="document title for the draft (default: the file name)")
    srr.add_argument("--family", help="layout family, once assigned by the Pattern Matcher")
    srr.add_argument("--provider", help="who publishes the layout, for example the clearing firm")
    srr.add_argument("--description", help="a short description of the layout")
    srr.add_argument("--model", default=os.environ.get("ASTRA_SPEC_READER_MODEL", DEFAULT_MODEL), help=f"the model to call (default {DEFAULT_MODEL})")
    srr.add_argument("--max-tokens", type=int, default=int(os.environ.get("ASTRA_SPEC_READER_MAX_TOKENS", MAX_TOKENS)), help=f"raise this if the model's response is cut off before finishing (default {MAX_TOKENS})")
    srr.add_argument("--out", default=os.environ.get("ASTRA_SPEC_READER_OUT", "work/spec-reader"), help="the draft is written under <out>/<id>/<version>/ (default: work/spec-reader)")
    srr.add_argument("--json", action="store_true")
    srr.set_defaults(func=cmd_spec_reader_run)

    pr = sub.add_parser("profiler", help="a sample file profiled for types, nulls, value sets and record types, with drift against the spec flagged")
    prsub = pr.add_subparsers(dest="profiler_command", required=True)

    prr = prsub.add_parser("run", help="profile a fixed-width sample against a Source Spec; read-only, writes no registry change")
    prr.add_argument("sample", help="the fixed-width sample file")
    prr.add_argument("--spec", required=True, help="path to the Source Spec YAML file to profile against")
    prr.add_argument("--top-n", type=int, default=TOP_N, help=f"how many of each field's most common values to keep (default {TOP_N})")
    prr.add_argument("--out", default=os.environ.get("ASTRA_PROFILER_OUT", "work/profiler"), help="the report is written under <out>/<spec id>/<spec version>/ (default: work/profiler)")
    prr.add_argument("--json", action="store_true")
    prr.set_defaults(func=cmd_profiler_run)

    pm = sub.add_parser("pattern-matcher", help="a custodian's spec assigned a family, tier and pattern list; a new shape goes to the architect queue")
    pmsub = pm.add_subparsers(dest="pattern_matcher_command", required=True)

    pmr = pmsub.add_parser("run", help="classify every unclassified spec in the registry, or one named spec")
    pmr.add_argument("--registry", required=True, help="the specs/ directory to load and classify against")
    pmr.add_argument("--id", help="classify only this spec id (default: every spec the registry has no family for)")
    pmr.add_argument("--version", help="with --id, this version (default: its latest)")
    pmr.add_argument("--family-threshold", type=float, default=FAMILY_THRESHOLD, help=f"minimum similarity to reuse an existing family (default {FAMILY_THRESHOLD})")
    pmr.add_argument("--out", default="work/pattern-matcher", help="the report is written under <out>/ (default: work/pattern-matcher)")
    pmr.add_argument("--json", action="store_true")
    pmr.set_defaults(func=cmd_pattern_matcher_run)

    rr = sub.add_parser("rule-recovery", help="legacy Splitter/Loader Java turned into rule catalog entries with file:line citations, classified, with candidate tests")
    rrsub = rr.add_subparsers(dest="rule_recovery_command", required=True)

    rrr = rrsub.add_parser("run", help="recover a draft rule catalog from Java source; never writes into rules/ directly, never marks an entry confirmed")
    rrr.add_argument("source", nargs="+", help="one or more Java source files (for example Splitter.java Loader.java)")
    rrr.add_argument("--group", required=True, help="the catalog group these rules belong to (rules/<group>/); a custodian, spec id or domain")
    rrr.add_argument("--owner-name", required=True, help="who would confirm or reject these entries")
    rrr.add_argument("--owner-email", required=True)
    rrr.add_argument("--model", default=os.environ.get("ASTRA_RULE_RECOVERY_MODEL", RULE_RECOVERY_DEFAULT_MODEL), help=f"the model to call (default {RULE_RECOVERY_DEFAULT_MODEL})")
    rrr.add_argument("--max-tokens", type=int, default=int(os.environ.get("ASTRA_RULE_RECOVERY_MAX_TOKENS", RULE_RECOVERY_MAX_TOKENS)), help=f"raise this if the model's response is cut off before finishing (default {RULE_RECOVERY_MAX_TOKENS})")
    rrr.add_argument("--out", default=os.environ.get("ASTRA_RULE_RECOVERY_OUT", "work/rule-recovery"), help="the draft is written under <out>/<group>/ (default: work/rule-recovery)")
    rrr.add_argument("--json", action="store_true")
    rrr.set_defaults(func=cmd_rule_recovery_run)

    md = sub.add_parser("modeler", help="a Source Spec mapped to the canonical model, with resolution parameters, DQ suggestions and a draft config")
    mdsub = md.add_subparsers(dest="modeler_command", required=True)

    mdr = mdsub.add_parser("run", help="draft a config from a Source Spec; never writes into configs/ or the domain pack directly")
    mdr.add_argument("--spec", required=True, help="path to the Source Spec YAML file to map")
    mdr.add_argument("--domain", required=True, help="the domain pack directory (for example domains/custodial)")
    mdr.add_argument("--rules", default="rules", help="the rule catalog directory, to check existing_rule references against (default: rules)")
    mdr.add_argument("--custodian", required=True, help="the custodian this config is for")
    mdr.add_argument("--group", help="catalog group for any new rule proposed (default: the spec id)")
    mdr.add_argument("--tier", choices=["simple", "medium", "complex"], help="default: the Pattern Matcher's own assign_tier(spec)")
    mdr.add_argument("--owner-name", required=True, help="who would confirm or reject a proposed rule")
    mdr.add_argument("--owner-email", required=True)
    mdr.add_argument("--model", default=os.environ.get("ASTRA_MODELER_MODEL", MODELER_DEFAULT_MODEL), help=f"the model to call (default {MODELER_DEFAULT_MODEL})")
    mdr.add_argument("--max-tokens", type=int, default=int(os.environ.get("ASTRA_MODELER_MAX_TOKENS", MODELER_MAX_TOKENS)), help=f"raise this if the model's response is cut off before finishing (default {MODELER_MAX_TOKENS})")
    mdr.add_argument("--out", default=os.environ.get("ASTRA_MODELER_OUT", "work/modeler"), help="the draft is written under <out>/<spec id>/<spec version>/ (default: work/modeler)")
    mdr.add_argument("--json", action="store_true")
    mdr.set_defaults(func=cmd_modeler_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
