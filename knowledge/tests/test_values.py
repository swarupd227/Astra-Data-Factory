from datetime import date, time
from decimal import Decimal

from astra_knowledge.patterns.values import convert


def test_strings_lose_trailing_spaces_and_blank_is_null():
    assert convert("ACME      ", "string").value == "ACME"
    assert convert("  ACME ", "string").value == "  ACME"
    assert convert("          ", "string").value is None


def test_codes_keep_declared_blank_codes_and_report_undeclared_values():
    codes = (("+", "long"), ("-", "short"), (" ", "unknown"))
    assert convert(" ", "code", codes=codes).value == " "
    assert convert("-", "code", codes=codes).value == "-"
    assert convert("EQ ", "code", codes=(("EQ", "equity"),)).value == "EQ"
    bad = convert("ZZ", "code", codes=(("EQ", "equity"),))
    assert bad.value == "ZZ" and bad.problem == "'ZZ' is not a declared code; declared codes are 'EQ'"
    assert convert("   ", "code", codes=(("EQ", "equity"),)).value is None
    assert convert(" AB ", "code").value == "AB"


def test_integers():
    assert convert("000000012", "integer").value == 12
    assert convert("         ", "integer").value is None
    assert convert("00000A012", "integer").problem == "'00000A012' is not all digits"


def test_implied_decimals_with_a_separate_sign_field():
    assert convert("000000000012345678", "decimal", scale=5, sign="+", has_sign_field=True).value == Decimal("123.45678")
    assert convert("000000000012345678", "decimal", scale=5, sign="-", has_sign_field=True).value == Decimal("-123.45678")
    blank = convert("000000000012345678", "decimal", scale=5, sign=" ", has_sign_field=True)
    assert blank.value is None and blank.problem == "sign is blank; the value is unknown, not zero"
    zero = convert("000000000000000000", "decimal", scale=5, sign=" ", has_sign_field=True)
    assert zero.value == Decimal("0.00000") and zero.problem is None
    assert convert("000000000012345678", "decimal", scale=5, sign="X", has_sign_field=True).problem == "sign 'X' is not '+', '-' or blank"


def test_decimals_without_a_sign_field_are_unsigned_magnitudes():
    assert convert("000000012345600", "decimal", scale=6).value == Decimal("12.345600")
    assert convert("000000000000042", "decimal", scale=0).value == Decimal("42")
    assert convert("               ", "decimal", scale=6).value is None


def test_dates_and_times_follow_the_declared_format_and_zeros_are_null():
    assert convert("20260905", "date", format="YYYYMMDD").value == date(2026, 9, 5)
    assert convert("09/05/2026", "date", format="MM/DD/YYYY").value == date(2026, 9, 5)
    assert convert("00000000", "date", format="YYYYMMDD").value is None
    assert convert("20261332", "date", format="YYYYMMDD").problem == "'20261332' is not a date in the format YYYYMMDD"
    assert convert("20260905", "date", format="ISO").problem.startswith("date format 'ISO' is not supported")
    assert convert("143005", "time", format="HHMMSS").value == time(14, 30, 5)
    assert convert("1430", "time", format="HHMM").value == time(14, 30)


def test_booleans():
    assert convert("Y", "boolean").value is True and convert("n", "boolean").value is False
    assert convert(" ", "boolean").value is None
    assert convert("maybe", "boolean").problem == "'maybe' is not a boolean (Y/N, T/F, 1/0)"


def test_unsupported_type_is_a_problem_not_an_exception():
    assert convert("x", "geometry").problem == "unsupported type 'geometry'"
