"""Turning the characters of a field into a typed value.

The rules are the ones custodian layouts assume and the ones later patterns
render into SQL, so they live in one place:

  string    trailing spaces removed; all blank is NULL
  code      kept as written when it is a declared code (a blank code is a
            code), otherwise stripped; an undeclared value is reported
  integer   digits only; all blank is NULL
  decimal   digits with implied decimals from the picture; a separate sign
            field gives the sign: '-' negative, '+' positive, blank NULL
            (an unsigned number with an unknown sign is not a number)
  date      digits or separators in the declared format; all blank or all
            zeros is NULL
  time      HHMMSS or HHMM
  boolean   Y/N, T/F, 1/0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

DATE_FORMATS = {
    "YYYYMMDD": "%Y%m%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "MMDDYYYY": "%m%d%Y",
    "DDMMYYYY": "%d%m%Y",
    "YYMMDD": "%y%m%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
}
TIME_FORMATS = {
    "HHMMSS": "%H%M%S",
    "HHMM": "%H%M",
    "HH:MM:SS": "%H:%M:%S",
    "HH:MM": "%H:%M",
}
TRUE_VALUES = {"Y", "T", "1", "TRUE", "YES"}
FALSE_VALUES = {"N", "F", "0", "FALSE", "NO"}


class ValueError_(ValueError):
    """A field's characters do not make the value the spec says they should."""


@dataclass(frozen=True)
class Converted:
    value: Any
    problem: str | None = None


def convert(
    raw: str,
    type_: str,
    *,
    scale: int = 0,
    format: str | None = None,
    codes: tuple[tuple[str, str], ...] = (),
    sign: str | None = None,
    has_sign_field: bool = False,
    explicit: bool = False,
) -> Converted:
    """Convert one field. Never raises: a bad value comes back as None with a problem.

    `explicit` is for delimited files, where numbers are written as people
    write them ("-123.45") rather than as unsigned digits with implied decimals.
    """
    try:
        if type_ == "string":
            text = raw.rstrip()
            return Converted(text if text.strip() else None)
        if type_ == "code":
            return _code(raw, codes)
        if type_ == "integer":
            return _explicit_integer(raw) if explicit else _integer(raw)
        if type_ == "decimal":
            return _explicit_decimal(raw) if explicit else _decimal(raw, scale, sign, has_sign_field)
        if type_ == "date":
            return _date(raw, format)
        if type_ == "time":
            return _time(raw, format)
        if type_ == "boolean":
            return _boolean(raw)
    except ValueError_ as exc:
        return Converted(None, str(exc))
    return Converted(None, f"unsupported type '{type_}'")


def _code(raw: str, codes: tuple[tuple[str, str], ...]) -> Converted:
    declared = {value for value, _ in codes}
    if not declared:
        text = raw.strip()
        return Converted(text or None)
    if raw in declared:
        return Converted(raw)
    stripped = raw.strip()
    if stripped in declared:
        return Converted(stripped)
    if not stripped:
        return Converted(None)
    return Converted(stripped, f"'{stripped}' is not a declared code; declared codes are {', '.join(repr(v) for v in declared)}")


def _integer(raw: str) -> Converted:
    text = raw.strip()
    if not text:
        return Converted(None)
    if not text.isdigit():
        raise ValueError_(f"'{raw}' is not all digits")
    return Converted(int(text))


def _decimal(raw: str, scale: int, sign: str | None, has_sign_field: bool) -> Converted:
    text = raw.strip()
    if not text:
        return Converted(None)
    if not text.isdigit():
        raise ValueError_(f"'{raw}' is not all digits")
    magnitude = Decimal(text).scaleb(-scale) if scale else Decimal(text)
    if not has_sign_field:
        return Converted(magnitude)
    if sign == "-":
        return Converted(-magnitude)
    if sign == "+":
        return Converted(magnitude)
    if sign is None or not sign.strip():
        return Converted(None, "sign is blank; the value is unknown, not zero") if magnitude else Converted(Decimal(0).scaleb(-scale) if scale else Decimal(0))
    raise ValueError_(f"sign '{sign}' is not '+', '-' or blank")


EXPLICIT_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def _explicit_integer(raw: str) -> Converted:
    text = raw.strip().replace(",", "")
    if not text:
        return Converted(None)
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError_(f"'{raw.strip()}' is not an integer")
    return Converted(int(text))


def _explicit_decimal(raw: str) -> Converted:
    text = raw.strip().replace(",", "")
    if not text:
        return Converted(None)
    if not EXPLICIT_NUMBER.match(text):
        raise ValueError_(f"'{raw.strip()}' is not a number")
    return Converted(Decimal(text))


def _date(raw: str, format: str | None) -> Converted:
    text = raw.strip()
    if not text or set(text) <= {"0"}:
        return Converted(None)
    pattern = DATE_FORMATS.get((format or "YYYYMMDD").upper())
    if pattern is None:
        raise ValueError_(f"date format '{format}' is not supported; supported formats are {', '.join(DATE_FORMATS)}")
    try:
        return Converted(datetime.strptime(text, pattern).date())
    except ValueError as exc:
        raise ValueError_(f"'{text}' is not a date in the format {format or 'YYYYMMDD'}") from exc


def _time(raw: str, format: str | None) -> Converted:
    text = raw.strip()
    if not text:
        return Converted(None)
    pattern = TIME_FORMATS.get((format or "HHMMSS").upper())
    if pattern is None:
        raise ValueError_(f"time format '{format}' is not supported; supported formats are {', '.join(TIME_FORMATS)}")
    try:
        return Converted(datetime.strptime(text, pattern).time())
    except ValueError as exc:
        raise ValueError_(f"'{text}' is not a time in the format {format or 'HHMMSS'}") from exc


def _boolean(raw: str) -> Converted:
    text = raw.strip().upper()
    if not text:
        return Converted(None)
    if text in TRUE_VALUES:
        return Converted(True)
    if text in FALSE_VALUES:
        return Converted(False)
    raise ValueError_(f"'{raw.strip()}' is not a boolean (Y/N, T/F, 1/0)")


__all__ = ["Converted", "convert", "date", "time"]
