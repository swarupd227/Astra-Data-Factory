from decimal import Decimal
from pathlib import Path

import pytest

from astra_knowledge.patterns import parse
from astra_knowledge.patterns.numerics import DEFAULT, SignConvention, implied_decimal, signed_implied_decimal
from astra_knowledge.registry import Registry

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"

# The story's own example: 9(13)V9(05), eighteen digits, five implied decimals.
DIGITS = "000000000012345678"


def test_plus_sign_returns_the_positive_decimal():
    result = signed_implied_decimal(DIGITS, "+", 5)
    assert result.value == Decimal("123.45678") and result.problem is None


def test_minus_sign_returns_the_negative_decimal():
    assert signed_implied_decimal(DIGITS, "-", 5).value == Decimal("-123.45678")


def test_blank_sign_returns_null_not_zero():
    result = signed_implied_decimal(DIGITS, " ", 5)
    assert result.value is None
    assert result.problem == "sign is blank; the value is unknown, not zero"
    assert signed_implied_decimal(DIGITS, None, 5).value is None
    # A blank sign on a zero magnitude is zero: there is nothing to sign.
    zero = signed_implied_decimal("000000000000000000", " ", 5)
    assert zero.value == Decimal("0") and zero.problem is None


def test_invalid_sign_and_invalid_digits_are_problems():
    bad_sign = signed_implied_decimal(DIGITS, "X", 5)
    assert bad_sign.value is None and bad_sign.problem == "sign 'X' is not an accepted sign; accepted signs are '', ' ', '+', '-'"
    bad_digits = signed_implied_decimal("0000000000123A5678", "+", 5)
    assert bad_digits.value is None and bad_digits.problem == "'0000000000123A5678' is not all digits"
    assert signed_implied_decimal("      ", "+", 5).value is None and signed_implied_decimal("      ", "+", 5).problem is None


def test_scale_zero_and_digits_shorter_than_the_scale():
    assert signed_implied_decimal("000042", "+", 0).value == Decimal("42")
    assert signed_implied_decimal("5", "+", 3).value == Decimal("0.005")
    assert implied_decimal("12345", 2) == Decimal("123.45")


def test_the_convention_is_configuration():
    debit_credit = SignConvention(positive=frozenset({"C"}), negative=frozenset({"D"}), unknown=frozenset({" ", ""}))
    assert signed_implied_decimal(DIGITS, "D", 5, debit_credit).value == Decimal("-123.45678")
    assert signed_implied_decimal(DIGITS, "C", 5, debit_credit).value == Decimal("123.45678")
    assert signed_implied_decimal(DIGITS, "+", 5, debit_credit).problem == "sign '+' is not an accepted sign; accepted signs are '', ' ', 'C', 'D'"

    from_codes = SignConvention.from_codes([("P", "positive"), ("N", "negative"), ("?", "unknown")])
    assert from_codes.classify("N") == "negative" and from_codes.classify("?") == "unknown" and from_codes.classify("+") == "invalid"
    assert signed_implied_decimal(DIGITS, "?", 5, from_codes).problem == "sign '?' means unknown; the value is unknown, not zero"
    assert SignConvention.from_codes([("+", None), ("-", None)]) is None
    assert DEFAULT.classify(None) == "unknown" and DEFAULT.classify(" ") == "unknown" and DEFAULT.classify("-") == "negative"


def test_leading_sign_style():
    assert signed_implied_decimal("-00012345", None, 2, sign_style="leading").value == Decimal("-123.45")
    assert signed_implied_decimal("+00012345", None, 2, sign_style="leading").value == Decimal("123.45")
    assert signed_implied_decimal(" 00012345", None, 2, sign_style="leading").value == Decimal("123.45")
    assert signed_implied_decimal("-", None, 2, sign_style="leading").value is None


def test_the_gcus_spec_declares_its_convention_on_the_sign_field_codes():
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    spec = registry.get("pershing_gcus", "2017-07-25")
    quantity = spec.record("detail").field("quantity")
    assert quantity.sign_field == "quantity_sign" and quantity.sign_style == "separate"
    assert quantity.sign_convention == SignConvention(frozenset({"+"}), frozenset({"-"}), frozenset({" "}))

    line = "DTL" + "ACC0000001" + "123456789" + DIGITS + "-" + "000000012345600" + "20260905" + "EQ" + " " * 54
    parsed = parse(spec, ["HDR20260905RMT0000001R" + " " * 98, line, "TRL000000001" + " " * 108])
    assert parsed.ok and parsed.rows[0].values["quantity"] == Decimal("-123.45678")


def test_registry_requires_both_positive_and_negative_when_signs_are_declared(tmp_path):
    folder = tmp_path / "specs" / "pershing_gcus"
    folder.mkdir(parents=True)
    text = (SPECS / "pershing_gcus" / "2017-07-25.yaml").read_text(encoding="utf-8")
    (folder / "2017-07-25.yaml").write_text(text.replace('- { value: "-", meaning: short, sign: negative }', '- { value: "-", meaning: short }'), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("'quantity_sign' declares sign meanings on its codes but must declare both a positive and a negative code" in p.message for p in problems)

    (folder / "2017-07-25.yaml").write_text(text.replace("        sign_field: quantity_sign\n", "        sign_field: quantity_sign\n        sign_style: leading\n"), encoding="utf-8")
    _, problems = Registry.load(tmp_path / "specs", tmp_path)
    assert any("has both sign_field and sign_style leading" in p.message for p in problems)


@pytest.mark.parametrize("sign,expected", [("+", Decimal("1.5")), ("-", Decimal("-1.5")), (" ", None), ("Z", None)])
def test_the_four_cases_of_the_story(sign, expected):
    assert signed_implied_decimal("00150", sign, 2).value == expected
