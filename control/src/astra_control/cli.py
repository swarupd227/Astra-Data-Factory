"""Command line: `astra-control board add|move|set-wip-limit|show`.

No credentials, no live Postgres: `board.yaml` (named on every command with `--board`) is the
whole state, read fresh and rewritten on every command — see astra_control.board's own module
docstring for why. Exit codes: `add`, `move` and `set-wip-limit` are 0 on success, 2 when the
board rejects the change (a blank field, an unknown custodian, a move that would exceed a
stream's WIP limit) — the same shape `astra_agents.guardrails`'s own `set-level` uses for a
rejected change, since a blocked move here is exactly that: refused outright, never applied and
flagged. `show` is 0 when every stream is within its own WIP limit, 1 when at least one stream is
over (a real condition to look at, not a start error), 2 only for a bad `--board` argument.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from astra_control.board import STATIONS, BoardError, add_custodian, load_board, move, render_markdown, save_board, set_wip_limit


def cmd_board_add(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = add_custodian(board, args.custodian, args.stream)
        save_board(board, Path(args.board))
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added: {args.custodian} to {args.stream}, at profile")
    return 0


def cmd_board_move(args: argparse.Namespace) -> int:
    try:
        board = load_board(Path(args.board))
        board = move(board, args.custodian, args.to)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astra-control", description="Astra Data Factory control plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    bd = sub.add_parser("board", help="the factory board: every custodian at its station, one WIP limit enforced per stream")
    bdsub = bd.add_subparsers(dest="board_command", required=True)

    bda = bdsub.add_parser("add", help="add a custodian to the board, at profile; blocked if it would exceed its stream's WIP limit")
    bda.add_argument("--board", required=True, help="path to the board state file (created on first write)")
    bda.add_argument("--custodian", required=True)
    bda.add_argument("--stream", required=True, help="the engagement this custodian belongs to, for example envestnet-custodial")
    bda.set_defaults(func=cmd_board_add)

    bdm = bdsub.add_parser("move", help="move a custodian to a station; blocked only when it would newly exceed its stream's WIP limit")
    bdm.add_argument("--board", required=True)
    bdm.add_argument("--custodian", required=True)
    bdm.add_argument("--to", required=True, help=f"the station to move to; stations are {', '.join(s.value for s in STATIONS)}")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
