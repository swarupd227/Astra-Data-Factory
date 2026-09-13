"""Command line: `astra-agents spec-reader run`, `astra-agents profiler run`,
`astra-agents pattern-matcher run`, `astra-agents rule-recovery run`,
`astra-agents modeler run`, `astra-agents dq-generator run`,
`astra-agents test-generator run`, `astra-agents exception-triage run`,
`astra-agents drift-watcher run`, `astra-agents break-explainer run` and
`astra-agents gate-evidence-compiler run`.

Needs no Snowflake connection. spec-reader's, rule-recovery's and
modeler's real Anthropic API calls need ANTHROPIC_API_KEY in the
environment (every AnthropicClient class reads it the way the anthropic
SDK always does); profiler, pattern-matcher, dq-generator,
test-generator, exception-triage, drift-watcher, break-explainer and
gate-evidence-compiler are plain deterministic code, reading only the
files given on the command line. Exit codes for all eleven: 0 nothing
to review, 1 the run found something a person should look at (an
invalid draft; a profile with drift; a new pattern proposal; an
untraced rejection code or an unrouted T-SQL line; a breaking CDM
change request or a rule tagged CONFIRM_WITH_LOADER; a dq_rule this
agent could not synthesize a branch for; an exception code with no
taxonomy entry; a record-length or code-set drift against the spec; a
dual-run difference this agent could not explain; a gate criterion with
no evidence), 2 the run could not start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from astra_agents.break_explainer import BreakExplainerError, run as run_break_explainer, write_draft as write_explain_draft
from astra_agents.dq_generator import DqGeneratorError, run as run_dq_generator, write_draft as write_dq_draft
from astra_agents.drift_watcher import DriftWatcherError, run as run_drift_watcher, write_draft as write_drift_draft
from astra_agents.exception_triage import AUTO_APPLY_CONFIDENCE, ExceptionTriageError, record_decision, run as run_exception_triage, write_draft as write_triage_draft
from astra_agents.gate_evidence_compiler import EvidenceSources, GateEvidenceCompilerError, record_approval, run as run_gate_evidence_compiler, write_pack
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
from astra_agents.test_generator import TestGeneratorError, run as run_test_generator, write_draft as write_test_draft


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


def cmd_dq_generator_run(args: argparse.Namespace) -> int:
    try:
        draft = run_dq_generator(Path(args.spec), targets_path=Path(args.targets) if args.targets else None)
    except DqGeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / draft.spec_id / draft.spec_version
    report_path, _data_path = write_dq_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        by_category = draft.by_category
        print(f"{draft.spec_id} {draft.spec_version}: {len(draft.rules)} rule(s), valid: {'yes' if draft.valid else 'no'}")
        for category in ("control_total", "sign_field", "date", "key", "pairing"):
            print(f"  {category}: {len(by_category[category])}")
        print(f"  report: {report_path}")
    return 0 if draft.valid else 1


def cmd_test_generator_run(args: argparse.Namespace) -> int:
    try:
        draft = run_test_generator(Path(args.config), Path(args.spec))
    except TestGeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / draft.config_id
    report_path, _data_path = write_test_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{draft.config_id}: {len(draft.rule_ids)} rule(s), {len(draft.covered_rule_ids)} covered ({draft.coverage:.0%}), {len(draft.cases)} case(s)")
        if draft.uncovered_rule_ids:
            print(f"  not covered: {', '.join(draft.uncovered_rule_ids)}")
        print(f"  report: {report_path}")
    return 0 if draft.ok else 1


def cmd_exception_triage_run(args: argparse.Namespace) -> int:
    try:
        draft = run_exception_triage(Path(args.exceptions), Path(args.rejections), Path(args.decisions) if args.decisions else None)
    except ExceptionTriageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    report_path, _data_path = write_triage_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{len(draft.suggestions)} root cause(s), {len(draft.auto_apply_suggestions)} eligible to auto-apply (whitelisted and confidence >= {AUTO_APPLY_CONFIDENCE:.0%})")
        if draft.unresolved_codes:
            print(f"  no taxonomy entry: {', '.join(draft.unresolved_codes)}")
        print(f"  report: {report_path}")
    return 0 if draft.ok else 1


def cmd_exception_triage_record_decision(args: argparse.Namespace) -> int:
    try:
        record_decision(Path(args.decisions), code=args.code, decision=args.decision, by=args.by)
    except ExceptionTriageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"recorded: {args.code} {args.decision} by {args.by}")
    return 0


def cmd_drift_watcher_run(args: argparse.Namespace) -> int:
    try:
        draft = run_drift_watcher(Path(args.spec), Path(args.sample))
    except DriftWatcherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / draft.spec_id / draft.spec_version
    report_path, _data_path = write_drift_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{draft.spec_id} {draft.spec_version}: {draft.lines_checked} line(s) checked, drift detected: {'yes' if draft.drift_detected else 'no'}")
        for f in draft.findings:
            print(f"  {f.kind}: {f.description}")
        print(f"  report: {report_path}")
    return 0 if draft.ok else 1


def cmd_break_explainer_run(args: argparse.Namespace) -> int:
    try:
        draft = run_break_explainer(
            Path(args.config),
            Path(args.parity),
            Path(args.legacy),
            Path(args.lakehouse),
            args.business_date,
            specs_dir=Path(args.specs),
            rules_dir=Path(args.rules),
            domains_dir=Path(args.domains),
        )
    except BreakExplainerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) / draft.custodian / draft.business_date
    report_path, _data_path = write_explain_draft(draft, out)
    if args.json:
        print(json.dumps(draft.to_dict(), indent=2))
    else:
        print(f"{draft.custodian} {draft.business_date}: {len(draft.explanations)} difference(s), {draft.explained_count} explained ({draft.explained_rate:.0%})")
        for cause, items in draft.by_cause.items():
            if items:
                print(f"  {cause}: {len(items)}")
        print(f"  report: {report_path}")
    return 0 if draft.explained_rate == 1.0 else 1


def cmd_gate_evidence_compiler_run(args: argparse.Namespace) -> int:
    sources = EvidenceSources(
        dq_report=Path(args.dq_report) if args.dq_report else None,
        parity_report=Path(args.parity_report) if args.parity_report else None,
        volume_report=Path(args.volume_report) if args.volume_report else None,
        chaos_report=Path(args.chaos_report) if args.chaos_report else None,
        dr_report=Path(args.dr_report) if args.dr_report else None,
        agent_eval_report=Path(args.agent_eval_report) if args.agent_eval_report else None,
        approvals=Path(args.approvals) if args.approvals else None,
    )
    pack = run_gate_evidence_compiler(args.release, sources)
    out = Path(args.out) / pack.release
    report_path, _data_path = write_pack(pack, out)
    if args.json:
        print(json.dumps(pack.to_dict(), indent=2))
    else:
        print(f"{pack.release}: {len(pack.criteria)} criteria, ready to release: {'yes' if pack.all_met else 'no'}")
        for c in pack.criteria:
            print(f"  {c.status}: {c.name}")
        print(f"  pack: {report_path}")
    return 0 if pack.all_met else 1


def cmd_gate_evidence_compiler_record_approval(args: argparse.Namespace) -> int:
    try:
        record_approval(Path(args.approvals), release=args.release, approver=args.approver, note=args.note)
    except GateEvidenceCompilerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"recorded: {args.release} approved by {args.approver}")
    return 0


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

    dq = sub.add_parser("dq-generator", help="file, record and pair-level DQ rules proposed from a Source Spec")
    dqsub = dq.add_subparsers(dest="dq_generator_command", required=True)

    dqr = dqsub.add_parser("run", help="propose DQ rules from a Source Spec; deterministic, no model call")
    dqr.add_argument("--spec", required=True, help="path to the Source Spec YAML file")
    dqr.add_argument("--targets", help="a client's own severity targets by category (control_total, sign_field, date, key, pairing); default severity is error when not given")
    dqr.add_argument("--out", default="work/dq-generator", help="the draft is written under <out>/<spec id>/<spec version>/ (default: work/dq-generator)")
    dqr.add_argument("--json", action="store_true")
    dqr.set_defaults(func=cmd_dq_generator_run)

    tg = sub.add_parser("test-generator", help="SQL assertion tests and synthetic edge files generated from a config's own dq_rules")
    tgsub = tg.add_subparsers(dest="test_generator_command", required=True)

    tgr = tgsub.add_parser("run", help="propose tests/unit/*.sql and tests/edge/*.dat from a config; deterministic, no model call, no real records")
    tgr.add_argument("--config", required=True, help="path to the source config YAML file")
    tgr.add_argument("--spec", required=True, help="path to the Source Spec YAML file the config references")
    tgr.add_argument("--out", default="work/test-generator", help="the draft is written under <out>/<config id>/ (default: work/test-generator)")
    tgr.add_argument("--json", action="store_true")
    tgr.set_defaults(func=cmd_test_generator_run)

    et = sub.add_parser("exception-triage", help="suggested resolutions per exception, with confidence, grouped by root cause; auto-apply only for whitelisted classes at L3")
    etsub = et.add_subparsers(dest="exception_triage_command", required=True)

    etr = etsub.add_parser("run", help="propose resolutions from a batch of exceptions; deterministic, no model call")
    etr.add_argument("--exceptions", required=True, help="a CSV of exception rows (the CDM Exception entity's own columns)")
    etr.add_argument("--rejections", required=True, help="path to the domain pack's rejections.yaml")
    etr.add_argument("--decisions", help="a decisions log to compute confidence from (default: no history, every code starts at 50%%)")
    etr.add_argument("--out", default="work/exception-triage", help="the draft is written under <out>/ (default: work/exception-triage)")
    etr.add_argument("--json", action="store_true")
    etr.set_defaults(func=cmd_exception_triage_run)

    etd = etsub.add_parser("record-decision", help="append one accepted or rejected decision to a decisions log, so future confidence reflects it")
    etd.add_argument("--decisions", required=True, help="path to the decisions log (created if it does not exist)")
    etd.add_argument("--code", required=True, help="the rejection code this decision is about")
    etd.add_argument("--decision", required=True, help="accepted or rejected")
    etd.add_argument("--by", required=True, help="who made the decision")
    etd.set_defaults(func=cmd_exception_triage_record_decision)

    dw = sub.add_parser("drift-watcher", help="a new file's layout compared against its spec, with a proposed delta; never modifies the spec")
    dwsub = dw.add_subparsers(dest="drift_watcher_command", required=True)

    dwr = dwsub.add_parser("run", help="detect record-length and code-set drift; deterministic, no model call")
    dwr.add_argument("--spec", required=True, help="path to the Source Spec YAML file")
    dwr.add_argument("--sample", required=True, help="path to the fixed-width sample file to check")
    dwr.add_argument("--out", default="work/drift-watcher", help="the report is written under <out>/<spec id>/<spec version>/ (default: work/drift-watcher)")
    dwr.add_argument("--json", action="store_true")
    dwr.set_defaults(func=cmd_drift_watcher_run)

    be = sub.add_parser("break-explainer", help="every dual-run difference explained by rule, field and cause")
    besub = be.add_subparsers(dest="break_explainer_command", required=True)

    ber = besub.add_parser("run", help="explain parity differences between a legacy and a lakehouse row set; deterministic, no model call")
    ber.add_argument("--config", required=True, help="path to the source config YAML file")
    ber.add_argument("--parity", required=True, help="path to the golden/<custodian>/parity.yaml mapping")
    ber.add_argument("--legacy", required=True, help="CSV of legacy rows, the parity mapping's own legacy column names")
    ber.add_argument("--lakehouse", required=True, help="CSV of lakehouse rows, the parity mapping's own lakehouse column names")
    ber.add_argument("--business-date", required=True, help="YYYY-MM-DD the two row sets are for")
    ber.add_argument("--specs", default="specs", help="the spec registry directory (default: specs)")
    ber.add_argument("--rules", default="rules", help="the rule catalog directory (default: rules)")
    ber.add_argument("--domains", default="domains", help="the domain packs directory (default: domains)")
    ber.add_argument("--out", default="work/break-explainer", help="the draft is written under <out>/<custodian>/<business date>/ (default: work/break-explainer)")
    ber.add_argument("--json", action="store_true")
    ber.set_defaults(func=cmd_break_explainer_run)

    gc = sub.add_parser("gate-evidence-compiler", help="a gate pack assembled from verification results, approvals and metrics, against named criteria")
    gcsub = gc.add_subparsers(dest="gate_evidence_compiler_command", required=True)

    gcr = gcsub.add_parser("run", help="assemble a gate pack for one release; deterministic, no model call")
    gcr.add_argument("--release", required=True, help="the release this pack is for, for example pershing_position-2026-09-13")
    gcr.add_argument("--dq-report", help="path to a DQ runner report.json")
    gcr.add_argument("--parity-report", help="path to a parity report.json")
    gcr.add_argument("--volume-report", help="path to a volume test report.json")
    gcr.add_argument("--chaos-report", help="path to a chaos scenarios report.json")
    gcr.add_argument("--dr-report", help="path to a DR drill report.json")
    gcr.add_argument("--agent-eval-report", help="path to an agent-eval report.json (per-agent or the weekly rollup)")
    gcr.add_argument("--approvals", help="path to the approvals log to check for this release")
    gcr.add_argument("--out", default="work/gate-evidence-compiler", help="the pack is written under <out>/<release>/ (default: work/gate-evidence-compiler)")
    gcr.add_argument("--json", action="store_true")
    gcr.set_defaults(func=cmd_gate_evidence_compiler_run)

    gca = gcsub.add_parser("record-approval", help="append one release approval to the approvals log")
    gca.add_argument("--approvals", required=True, help="path to the approvals log (created if it does not exist)")
    gca.add_argument("--release", required=True, help="the release being approved")
    gca.add_argument("--approver", required=True, help="who approved it")
    gca.add_argument("--note", help="an optional note")
    gca.set_defaults(func=cmd_gate_evidence_compiler_record_approval)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
