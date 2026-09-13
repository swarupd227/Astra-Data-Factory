"""Command line: `astra-agents spec-reader run <document> ...` and `astra-agents profiler run <sample> ...`.

Needs no Snowflake connection. spec-reader's real Anthropic API call needs
ANTHROPIC_API_KEY in the environment (astra_agents.spec_reader.AnthropicClient
reads it the way the anthropic SDK always does); profiler is plain
deterministic code, reading only the sample file and spec given on the
command line. Exit codes for both: 0 nothing to review, 1 the run found
something a person should look at (an invalid draft; a profile with drift),
2 the run could not start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from astra_agents.profiler import TOP_N, ProfilerError, load_spec, run as run_profiler, write_profile
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
