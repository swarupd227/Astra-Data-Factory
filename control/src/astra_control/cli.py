"""Command line: `astra-control board add|move|set-wip-limit|show` and `astra-control
config-studio start|advance|request-promotion|show-requests`.

No credentials, no live Postgres: `board.yaml` and the promotion-requests log (named on every
command with `--board`/`--requests`) are the whole state, read fresh and rewritten on every
command — see astra_control.board's and astra_control.config_studio's own module docstrings for
why. Exit codes: every `board` and `config-studio` write command is 0 on success, 2 when the
change is rejected (a blank field, an unknown custodian, a move that would exceed a stream's WIP
limit, a promotion requested out of order or a medium/complex request missing its reviewer) — the
same shape `astra_agents.guardrails`'s own `set-level` uses for a rejected change: refused
outright, never applied and flagged. `board show` is 0 when every stream is within its own WIP
limit, 1 when at least one stream is over (a real condition to look at, not a start error), 2
only for a bad `--board` argument. `config-studio show-requests` is always 0 — a plain read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from astra_control.board import STATIONS, BoardError, add_custodian, load_board, move, render_markdown, save_board, set_wip_limit
from astra_control.config_studio import SEQUENCE, TIERS, ConfigStudioError, advance, load_promotion_requests, request_promotion, start


def cmd_board_add(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = add_custodian(board, args.custodian, args.stream, by=args.by)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added: {args.custodian} to {args.stream}, at profile")
    return 0


def cmd_board_move(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = move(board, args.custodian, args.to, by=args.by)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"moved: {args.custodian} -> {args.to}")
    return 0


def cmd_board_set_wip_limit(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = set_wip_limit(board, args.stream, args.limit)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"set: {args.stream} WIP limit -> {args.limit}")
    return 0


def cmd_board_show(args: argparse.Namespace) -> int:
    board = load_board(Path(args.board))
    if args.json:
        print(json.dumps(board.to_dict(), indent=2))
    else:
        print(render_markdown(board))
        over = board.over_limit_streams()
        if over:
            print(f"over limit: {', '.join(over)}")
    return 1 if board.over_limit_streams() else 0


def cmd_config_studio_start(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = start(board, args.custodian, args.stream, by=args.by)
        save_board(board, Path(args.board))
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"started: {args.custodian} ({args.stream}), profiled by {args.by}")
    return 0


def cmd_config_studio_advance(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = advance(board, args.custodian, args.to, by=args.by)
        save_board(board, Path(args.board))
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"advanced: {args.custodian} -> {args.to}, by {args.by}")
    return 0


def cmd_config_studio_request_promotion(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        request_promotion(
            board,
            Path(args.requests),
            args.custodian,
            tier=args.tier,
            requested_by=args.requested_by,
            reviewed_by=args.reviewed_by,
            note=args.note,
        )
    except ConfigStudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    self_service = args.tier == "simple"
    print(f"requested: promotion for {args.custodian} ({args.tier} tier){' — self-service, no engineer' if self_service else f', reviewed by {args.reviewed_by}'}")
    return 0


def cmd_config_studio_show_requests(args: argparse.Namespace) -> int:
    requests = load_promotion_requests(Path(args.requests) if args.requests else None)
    if args.custodian:
        requests = tuple(r for r in requests if r.custodian_id == args.custodian)
    if args.json:
        print(json.dumps([r.to_dict() for r in requests], indent=2))
    else:
        if not requests:
            print("No promotion requests recorded yet.")
        for r in requests:
            reviewer = "self-service, no engineer" if r.self_service else f"reviewed by {r.reviewed_by}"
            print(f"{r.at}  {r.custodian_id}  {r.tier}  requested by {r.requested_by}  ({reviewer})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-control", description="Astra Data Factory control plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    bd = sub.add_parser("board", help="the factory board: every custodian at its station, one WIP limit enforced per stream")
    bdsub = bd.add_subparsers(dest="board_command", required=True)

    bda = bdsub.add_parser("add", help="add a custodian to the board, at profile; blocked if it would exceed its stream's WIP limit")
    bda.add_argument("--board", required=True, help="path to the board state file (created on first write)")
    bda.add_argument("--custodian", required=True)
    bda.add_argument("--stream", required=True, help="the engagement this custodian belongs to, for example envestnet-custodial")
    bda.add_argument("--by", help="who added this custodian, recorded on the transition")
    bda.set_defaults(func=cmd_board_add)

    bdm = bdsub.add_parser("move", help="move a custodian to a station; blocked only when it would newly exceed its stream's WIP limit")
    bdm.add_argument("--board", required=True)
    bdm.add_argument("--custodian", required=True)
    bdm.add_argument("--to", required=True, help=f"the station to move to; stations are {', '.join(s.value for s in STATIONS)}")
    bdm.add_argument("--by", help="who made this move, recorded on the transition")
    bdm.set_defaults(func=cmd_board_move)

    bdw = bdsub.add_parser("set-wip-limit", help="set one stream's WIP limit")
    bdw.add_argument("--board", required=True)
    bdw.add_argument("--stream", required=True)
    bdw.add_argument("--limit", required=True, type=int)
    bdw.set_defaults(func=cmd_board_set_wip_limit)

    bds = bdsub.add_parser("show", help="every custodian at its station, WIP limits and custodians live per week")
    bds.add_argument("--board", required=True)
    bds.add_argument("--json", action="store_true")
    bds.set_defaults(func=cmd_board_show)

    cs = sub.add_parser("config-studio", help="profile a sample, review the drafted config, dry-run it and request promotion — self-service for a simple-tier custodian, no engineer involved")
    cssub = cs.add_subparsers(dest="config_studio_command", required=True)

    css = cssub.add_parser("start", help="a sample arrives: the custodian enters the board at profile")
    css.add_argument("--board", required=True)
    css.add_argument("--custodian", required=True)
    css.add_argument("--stream", required=True)
    css.add_argument("--by", required=True, help="who is running this (a BSA, for a simple-tier custodian)")
    css.set_defaults(func=cmd_config_studio_start)

    csa = cssub.add_parser("advance", help=f"move a custodian one step forward, in order ({' -> '.join(s.value for s in SEQUENCE)})")
    csa.add_argument("--board", required=True)
    csa.add_argument("--custodian", required=True)
    csa.add_argument("--to", required=True, help=f"the next station; config studio's own sequence is {', '.join(s.value for s in SEQUENCE)}")
    csa.add_argument("--by", required=True)
    csa.set_defaults(func=cmd_config_studio_advance)

    csr = cssub.add_parser("request-promotion", help="request promotion once a custodian has reached dry_run; simple tier needs only its requester, medium/complex needs a reviewer too")
    csr.add_argument("--board", required=True)
    csr.add_argument("--requests", required=True, help="path to the promotion-requests log (created on first write)")
    csr.add_argument("--custodian", required=True)
    csr.add_argument("--tier", required=True, help=f"tiers are {', '.join(TIERS)}")
    csr.add_argument("--requested-by", required=True)
    csr.add_argument("--reviewed-by", help="required for medium or complex tier; not needed for simple")
    csr.add_argument("--note")
    csr.set_defaults(func=cmd_config_studio_request_promotion)

    csw = cssub.add_parser("show-requests", help="every promotion request recorded")
    csw.add_argument("--requests", help="default: none recorded, so nothing is shown")
    csw.add_argument("--custodian", help="show only this custodian's requests")
    csw.add_argument("--json", action="store_true")
    csw.set_defaults(func=cmd_config_studio_show_requests)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
