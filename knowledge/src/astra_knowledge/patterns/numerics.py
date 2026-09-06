"""Signed implied-decimal numerics: one function for every custodian's convention.

Custodian files write a number as unsigned digits with implied decimals
(picture 9(13)V9(5) is eighteen digits, five of them decimals) and carry the
sign somewhere else: a separate one-character field, or a leading character.
Which characters mean positive, negative or unknown differs per custodian
('+'/'-'/blank, 'C'/'D', 'P'/'N'). The convention is configuration, taken
from the sign field's codes in the Source Spec; this function is the code.

Rules:
  digits blank            -> NULL
  digits not all digits   -> NULL, with a problem
  sign positive           -> +magnitude
  sign negative           -> -magnitude
  sign unknown (blank)    -> NULL with a problem, unless the magnitude is zero
  sign not in convention  -> NULL, with a problem

The same rules are shipped for Snowflake as CONTROL.SIGNED_IMPLIED_DECIMAL
and CONTROL.SIGNED_IMPLIED_DECIMAL_PROBLEM (infra/terraform/foundation/numerics.tf).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from astra_knowledge.patterns.values import Converted


@dataclass(frozen=True)
class SignConvention:
    positive: frozenset[str] = frozenset({"+"})
    negative: frozenset[str] = frozenset({"-"})
    unknown: frozenset[str] = frozenset({" ", ""})

    @classmethod
    def from_codes(cls, codes: Iterable[tuple[str, str | None]]) -> "SignConvention | None":
        """Build a convention from (value, sign) pairs; None when no code carries a sign."""
        positive, negative, unknown = set(), set(), set()
        seen = False
        for value, sign in codes:
            if sign is None:
                continue
            seen = True
            {"positive": positive, "negative": negative, "unknown": unknown}[sign].add(value)
        if not seen:
            return None
        return cls(frozenset(positive), frozenset(negative), frozenset(unknown or {" ", ""}))

    def classify(self, sign: str | None) -> str:
        """positive, negative, unknown or invalid."""
        if sign is None:
            return "unknown"
        for kind, values in (("negative", self.negative), ("positive", self.positive), ("unknown", self.unknown)):
            if sign in values or sign.strip() in values:
                return kind
        if not sign.strip() and "" in self.unknown:
            return "unknown"
        return "invalid"

    def accepted(self) -> str:
        return ", ".join(repr(v) for v in sorted(self.positive | self.negative | self.unknown))


DEFAULT = SignConvention()


def implied_decimal(digits: str, scale: int) -> Decimal:
    """Unsigned digits with `scale` implied decimals as a Decimal."""
    return Decimal(digits).scaleb(-scale) if scale else Decimal(digits)


def signed_implied_decimal(digits: str, sign: str | None, scale: int, convention: SignConvention = DEFAULT, *, sign_style: str = "separate") -> Converted:
    """Convert unsigned digits, a sign and a scale into a number, or NULL with a reason."""
    text = digits.strip()
    if sign_style == "leading":
        if text and text[0] in "+-":
            sign, text = text[0], text[1:].strip()
        elif text:
            sign = "+"
    if not text:
        return Converted(None)
    if not text.isdigit():
        return Converted(None, f"'{digits.strip()}' is not all digits")
    magnitude = implied_decimal(text, scale)

    kind = convention.classify(sign)
    if kind == "negative":
        return Converted(-magnitude)
    if kind == "positive":
        return Converted(magnitude)
    if kind == "unknown":
        if magnitude == 0:
            return Converted(magnitude)
        reason = "sign is blank; the value is unknown, not zero" if sign is None or not sign.strip() else f"sign '{sign}' means unknown; the value is unknown, not zero"
        return Converted(None, reason)
    return Converted(None, f"sign '{sign}' is not an accepted sign; accepted signs are {convention.accepted()}")
