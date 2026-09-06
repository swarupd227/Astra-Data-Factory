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
    """A field's characters do not make the value the spec says they should. Carries the rejection code."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Converted:
    value: Any
    problem: str | None = None
    code: str | None = None  # rejection code of the problem, from the domain pack taxonomy


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
    convention=None,
    sign_style: str = "separate",
) -> Converted:
    """Convert one field. Never raises: a bad value comes back as None with a problem.

    `explicit` is for delimited files, where numbers are written as people
    write them ("-123.45") rather than as unsigned digits with implied decimals.
    `convention` (a numerics.SignConvention) says which sign characters mean
    what; `sign_style` is "separate" (a sign field) or "leading" (a sign
    character at the start of the digits).
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
            return _explicit_decimal(raw) if explicit else _decimal(raw, scale, sign, has_sign_field, convention, sign_style)
        if type_ == "date":
            return _date(raw, format)
        if type_ == "time":
            return _time(raw, format)
        if type_ == "boolean":
            return _boolean(raw)
    except ValueError_ as exc:
        return Converted(None, str(exc), exc.code)
    return Converted(None, f"unsupported type '{type_}'", "FIELD_TYPE_UNSUPPORTED")


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
    return Converted(stripped, f"'{stripped}' is not a declared code; declared codes are {', '.join(repr(v) for v in declared)}", "FIELD_CODE_UNKNOWN")


def _integer(raw: str) -> Converted:
    text = raw.strip()
    if not text:
        return Converted(None)
    if not text.isdigit():
        raise ValueError_(f"'{raw}' is not all digits", "FIELD_NOT_NUMERIC")
    return Converted(int(text))


def _decimal(raw: str, scale: int, sign: str | None, has_sign_field: bool, convention, sign_style: str) -> Converted:
    from astra_knowledge.patterns import numerics  # here to avoid an import cycle

    if not has_sign_field and sign_style != "leading":
        text = raw.strip()
        if not text:
            return Converted(None)
        if not text.isdigit():
            raise ValueError_(f"'{raw}' is not all digits", "FIELD_NOT_NUMERIC")
        return Converted(numerics.implied_decimal(text, scale))
    return numerics.signed_implied_decimal(raw, sign, scale, convention or numerics.DEFAULT, sign_style=sign_style)


EXPLICIT_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def _explicit_integer(raw: str) -> Converted:
    text = raw.strip().replace(",", "")
    if not text:
        return Converted(None)
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError_(f"'{raw.strip()}' is not an integer", "FIELD_NOT_NUMERIC")
    return Converted(int(text))


def _explicit_decimal(raw: str) -> Converted:
    text = raw.strip().replace(",", "")
    if not text:
        return Converted(None)
    if not EXPLICIT_NUMBER.match(text):
        raise ValueError_(f"'{raw.strip()}' is not a number", "FIELD_NOT_NUMERIC")
    return Converted(Decimal(text))


def _date(raw: str, format: str | None) -> Converted:
    text = raw.strip()
    if not text or set(text) <= {"0"}:
        return Converted(None)
    pattern = DATE_FORMATS.get((format or "YYYYMMDD").upper())
    if pattern is None:
        raise ValueError_(f"date format '{format}' is not supported; supported formats are {', '.join(DATE_FORMATS)}", "FIELD_FORMAT_UNSUPPORTED")
    try:
        return Converted(datetime.strptime(text, pattern).date())
    except ValueError as exc:
        raise ValueError_(f"'{text}' is not a date in the format {format or 'YYYYMMDD'}", "FIELD_DATE_INVALID") from exc


def _time(raw: str, format: str | None) -> Converted:
    text = raw.strip()
    if not text:
        return Converted(None)
    pattern = TIME_FORMATS.get((format or "HHMMSS").upper())
    if pattern is None:
        raise ValueError_(f"time format '{format}' is not supported; supported formats are {', '.join(TIME_FORMATS)}", "FIELD_FORMAT_UNSUPPORTED")
    try:
        return Converted(datetime.strptime(text, pattern).time())
    except ValueError as exc:
        raise ValueError_(f"'{text}' is not a time in the format {format or 'HHMMSS'}", "FIELD_TIME_INVALID") from exc


def _boolean(raw: str) -> Converted:
    text = raw.strip().upper()
    if not text:
        return Converted(None)
    if text in TRUE_VALUES:
        return Converted(True)
    if text in FALSE_VALUES:
        return Converted(False)
    raise ValueError_(f"'{raw.strip()}' is not a boolean (Y/N, T/F, 1/0)", "FIELD_BOOLEAN_INVALID")


__all__ = ["Converted", "convert", "date", "time"]
