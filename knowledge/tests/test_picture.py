import pytest

from astra_knowledge.picture import PictureError, parse_picture


def test_alphanumeric_and_alphabetic_pictures():
    x = parse_picture("X(10)")
    assert (x.kind, x.length, x.default_type, x.is_numeric) == ("alphanumeric", 10, "string", False)
    a = parse_picture("A(3)")
    assert (a.kind, a.length, a.default_type) == ("alphabetic", 3, "string")


def test_integer_and_implied_decimal_pictures():
    n = parse_picture("9(8)")
    assert (n.kind, n.length, n.digits, n.scale, n.signed, n.default_type) == ("numeric", 8, 8, 0, False, "integer")
    d = parse_picture("9(13)V9(5)")
    assert (d.length, d.digits, d.scale, d.precision, d.default_type) == (18, 13, 5, 18, "decimal")


def test_sign_takes_no_position():
    s = parse_picture("S9(7)V9(2)")
    assert s.signed and s.length == 9 and s.precision == 9


def test_pictures_are_case_insensitive_and_trimmed():
    assert parse_picture(" x(4) ").length == 4


@pytest.mark.parametrize("text", ["X", "9(0)", "X(3)V9(2)", "SX(3)", "9(3)V9", "PIC 9(3)", "9(3).9(2)"])
def test_bad_pictures_are_rejected_with_the_expected_shapes(text):
    with pytest.raises(PictureError) as excinfo:
        parse_picture(text)
    assert "X(n), A(n), 9(n)" in str(excinfo.value) or "only numeric pictures" in str(excinfo.value)
