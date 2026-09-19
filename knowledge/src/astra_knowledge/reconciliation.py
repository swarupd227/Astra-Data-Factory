"""Reconciliation of a domain pack's canonical tables (S7.1.5, ADR 0079).

`domains/<name>/reconciliation.yaml` declares how the pack checks its own canonical tables against
each other: the position identity (a position's quantity equals its quantity in the previous
snapshot plus the securities movement of the transactions between the two dates), the cash balance
identity (a balance equals the previous balance plus the net amount of the transactions between the
two dates), and the tolerance per field type they are compared with. The pattern library holds the
reference implementation (`patterns/reconciliation.py`); the generation plane renders the same
checks as SQL over SILVER and a procedure that writes what it finds.

Nothing here is a client's threshold: a tolerance left out is exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from pathlib import Path

from astra_core.problems import Problem, dedupe, display_path
from astra_core.schema import describe_error, error_line, load_validator, sorted_errors
from astra_core.yamlsource import SourceError, line_of, load

SCHEMA = "reconciliation-v0.schema.json"
RECONCILIATION_FILE = "reconciliation.yaml"

FIELD_TYPES = ("quantity", "amount")
TOLERANCE_KINDS = ("exact", "decimal_places", "absolute", "relative")
MOVEMENT_DATES = ("trade_date", "settle_date")
UNRECONCILABLE = "unreconcilable"

# The two checks and the taxonomy code each raises when it finds a break.
CHECKS = ("position_quantity", "cash_balance")
CHECK_CODES = {"position_quantity": "POSITION_QUANTITY_MISMATCH", "cash_balance": "CASH_BALANCE_MISMATCH"}


@dataclass(frozen=True)
class Tolerance:
    """How far apart two values may be and still agree; the kinds the parity mappings already use."""

    kind: str = "exact"
    places: int | None = None
    epsilon: Decimal | None = None

    def matches(self, expected: Decimal, actual: Decimal) -> bool:
        # A wide context: the default 28 digits would raise on a NUMBER(28,8) value quantised to more places.
        with localcontext(Context(prec=80)):
            if self.kind == "decimal_places":
                step = Decimal(1).scaleb(-self.places)
                return expected.quantize(step, rounding=ROUND_HALF_UP) == actual.quantize(step, rounding=ROUND_HALF_UP)
            difference = abs(expected - actual)
            if self.kind == "absolute":
                return difference <= self.epsilon
            if self.kind == "relative":
                return difference <= self.epsilon * max(abs(expected), abs(actual))
            return expected == actual

    def to_dict(self) -> dict:
        data: dict = {"kind": self.kind}
        if self.places is not None:
            data["places"] = self.places
        if self.epsilon is not None:
            data["epsilon"] = str(self.epsilon)
        return data


EXACT = Tolerance()


@dataclass(frozen=True)
class CashCheck:
    balance_type: str
    movement_date: str


@dataclass(frozen=True)
class Reconciliation:
    domain: str
    model_version: str
    tolerances: dict[str, Tolerance]
    movement_date: str
    movements: dict[str, int | str]  # transaction type -> 1, -1, 0 or UNRECONCILABLE
    cash: tuple[CashCheck, ...]
    path: Path = field(compare=False)

    def tolerance(self, field_type: str) -> Tolerance:
        return self.tolerances.get(field_type, EXACT)


def load_reconciliation(path: Path, root: Path | None = None) -> tuple[dict | None, list[Problem]]:
    """Parse the file and check its shape. The types and codes are resolved by `resolve_reconciliation` against a model."""
    path = Path(path)
    display = display_path(path, root)
    try:
        data = load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return None, [Problem(display, None, f"file is not valid UTF-8: {exc.reason}")]
    except SourceError as exc:
        return None, [Problem(display, exc.line, f"invalid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(display, 1, "the file must contain a mapping (key: value pairs) at the top level")]
    if data.get("reconciliation_version") != 0:
        line = line_of(data, ["reconciliation_version"]) if "reconciliation_version" in data else 1
        return None, [Problem(display, line, f"reconciliation_version must be 0; found {data.get('reconciliation_version')!r}")]
    problems = [Problem(display, error_line(data, e), describe_error(e)) for e in sorted_errors(load_validator("astra_knowledge.schemas", SCHEMA), data)]
    if problems:
        return None, dedupe(problems)
    seen: set[str] = set()
    for i, check in enumerate(data["cash_identity"]):
        if check["balance_type"] in seen:
            problems.append(Problem(display, line_of(data, ["cash_identity", i, "balance_type"]), f"balance type {check['balance_type']} is checked twice"))
        seen.add(check["balance_type"])
    if problems:
        return None, problems
    data["_path"] = path
    return data, []


def resolve_reconciliation(data: dict, model, root: Path | None = None) -> tuple[Reconciliation | None, list[Problem]]:
    """Resolve the loaded file against the model version it pins: the entities and columns it reads, the transaction types it moves by, the balance types it checks."""
    path: Path = data["_path"]
    display = display_path(path, root)
    problems: list[Problem] = []

    needed = {"Position": ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "AS_OF_DATE", "QUANTITY"), "Transaction": ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "SECURITY_ID", "TRANSACTION_TYPE", "TRADE_DATE", "SETTLE_DATE", "QUANTITY", "NET_AMOUNT", "CURRENCY", "STATUS"), "Cash Balance": ("CUSTODIAN_ID", "ACCOUNT_NUMBER", "CURRENCY", "BALANCE_TYPE", "AS_OF_DATE", "AMOUNT")}
    entities = {e.name: e for e in model.entities}
    for name, columns in needed.items():
        entity = entities.get(name)
        if entity is None:
            problems.append(Problem(display, None, f"reconciliation reads {name}, which model {model.version} does not define; entities are {', '.join(entities)}"))
            continue
        missing = [c for c in columns if entity.column(c) is None]
        if missing:
            problems.append(Problem(display, None, f"reconciliation reads {name}.{', '.join(missing)}, which model {model.version} does not define"))
    if problems:
        return None, problems

    types = [c.value for c in entities["Transaction"].column("TRANSACTION_TYPE").codes]
    movements = data["position_identity"]["movements"]
    where = ["position_identity", "movements"]
    for kind in movements:
        if kind not in types:
            problems.append(Problem(display, line_of(data, where + [kind]), f"transaction type '{kind}' is not a code of Transaction.TRANSACTION_TYPE; codes are {', '.join(types)}"))
    absent = [t for t in types if t not in movements]
    if absent:
        problems.append(Problem(display, line_of(data, where), f"position_identity.movements must name every transaction type; missing {', '.join(absent)}"))

    balance_types = [c.value for c in entities["Cash Balance"].column("BALANCE_TYPE").codes]
    for i, check in enumerate(data["cash_identity"]):
        if check["balance_type"] not in balance_types:
            problems.append(Problem(display, line_of(data, ["cash_identity", i, "balance_type"]), f"balance type '{check['balance_type']}' is not a code of Cash Balance.BALANCE_TYPE; codes are {', '.join(balance_types)}"))
    if problems:
        return None, problems

    tolerances: dict[str, Tolerance] = {}
    for field_type, raw in data["tolerances"].items():
        tolerances[field_type] = Tolerance(raw["kind"], raw.get("places"), Decimal(str(raw["epsilon"])) if "epsilon" in raw else None)
    return (
        Reconciliation(
            data["domain"],
            model.version,
            tolerances,
            data["position_identity"]["movement_date"],
            dict(movements),
            tuple(CashCheck(c["balance_type"], c["movement_date"]) for c in data["cash_identity"]),
            path,
        ),
        [],
    )
