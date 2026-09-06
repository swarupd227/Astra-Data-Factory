"""COBOL-style picture clauses, the way custodian layout documents write them.

  X(10)          ten alphanumeric characters
  A(3)           three alphabetic characters
  9(8)           eight digits, an integer
  9(13)V9(5)     eighteen digits with five implied decimals
  S9(7)V9(2)     the same with a sign; the sign takes no position here,
                 because custodian files carry it in a separate field

The picture is the source of truth for a field's width and numeric shape;
the spec's position length must agree with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PICTURE = re.compile(r"^(?P<sign>S)?(?P<kind>[9XA])\((?P<n>[1-9][0-9]*)\)(?:V9\((?P<m>[1-9][0-9]*)\))?$")

KINDS = {"9": "numeric", "X": "alphanumeric", "A": "alphabetic"}


class PictureError(ValueError):
    pass


@dataclass(frozen=True)
class Picture:
    text: str
    kind: str
    length: int
    digits: int = 0
    scale: int = 0
    signed: bool = False

    @property
    def precision(self) -> int:
        return self.digits + self.scale

    @property
    def is_numeric(self) -> bool:
        return self.kind == "numeric"

    @property
    def default_type(self) -> str:
        if not self.is_numeric:
            return "string"
        return "decimal" if self.scale else "integer"


def parse_picture(text: str) -> Picture:
    match = PICTURE.match(text.strip().upper())
    if not match:
        raise PictureError(f"'{text}' is not a picture; expected X(n), A(n), 9(n), 9(n)V9(m) or S9(n)V9(m)")
    kind = KINDS[match.group("kind")]
    n = int(match.group("n"))
    m = int(match.group("m")) if match.group("m") else 0
    signed = match.group("sign") is not None
    if kind != "numeric" and (signed or m):
        raise PictureError(f"'{text}': only numeric pictures (9) may carry a sign or implied decimals")
    if kind != "numeric":
        return Picture(text=text, kind=kind, length=n)
    return Picture(text=text, kind=kind, length=n + m, digits=n, scale=m, signed=signed)


# Which declared types make sense for which picture kinds.
COMPATIBLE_TYPES = {
    "numeric": {"integer", "decimal", "date", "time", "code"},
    "alphanumeric": {"string", "date", "time", "code", "boolean"},
    "alphabetic": {"string", "code", "boolean"},
}
