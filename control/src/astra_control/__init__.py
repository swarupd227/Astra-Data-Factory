"""Astra Data Factory control plane (E6).

Today: the factory board (S6.1.1) — every custodian at its station, one WIP limit enforced per
stream, custodians live per week. No live Postgres yet: `board.yaml` is a real, working
stand-in for the store E6 will eventually have (astra_control.board's own module docstring).
"""

from astra_control.board import (
    STATIONS,
    IN_FLIGHT_STATIONS,
    Board,
    BoardError,
    CustodianCard,
    Station,
    Transition,
    add_custodian,
    live_per_week,
    load_board,
    move,
    render_markdown,
    save_board,
    set_wip_limit,
)

__all__ = [
    "STATIONS",
    "IN_FLIGHT_STATIONS",
    "Board",
    "BoardError",
    "CustodianCard",
    "Station",
    "Transition",
    "add_custodian",
    "live_per_week",
    "load_board",
    "move",
    "render_markdown",
    "save_board",
    "set_wip_limit",
]
