import pytest

from astra_core.problems import Problem, dedupe
from astra_core.schema import describe_error, sorted_errors
from astra_core.yamlsource import LineDict, LineList, SourceError, line_of, load


def test_load_keeps_lines_of_keys_and_items():
    data = load("a: 1\nb:\n  - x\n  - y\nc:\n  d: 2\n")
    assert isinstance(data, LineDict) and data.line == 1
    assert data.key_lines == {"a": 1, "b": 2, "c": 5}
    assert isinstance(data["b"], LineList) and data["b"].item_lines == [3, 4]
    assert line_of(data, ["c", "d"]) == 6
    assert line_of(data, ["b", 1]) == 4
    assert line_of(data, ["missing", "deeper"]) == 1


def test_duplicate_keys_are_an_error_with_both_lines():
    with pytest.raises(SourceError) as excinfo:
        load("a: 1\nb: 2\na: 3\n")
    assert str(excinfo.value) == "duplicate key 'a' (first defined on line 1)" and excinfo.value.line == 3


def test_dates_stay_text_and_multiple_documents_are_rejected():
    assert load("when: 2026-09-06\n")["when"] == "2026-09-06"
    with pytest.raises(SourceError):
        load("a: 1\n---\nb: 2\n")


def test_syntax_errors_carry_a_line():
    with pytest.raises(SourceError) as excinfo:
        load("a: 1\n b: [\n")
    assert excinfo.value.line is not None


def test_problem_formats_for_people_and_for_github():
    p = Problem("x.yaml", 3, "field: bad")
    assert p.format() == "x.yaml:3: field: bad"
    assert p.format("github", title="Spec registry") == "::error file=x.yaml,line=3,title=Spec registry::field: bad"
    assert dedupe([p, p, Problem("x.yaml", 3, "other")]) == [p, Problem("x.yaml", 3, "other")]


def test_schema_errors_are_described_in_plain_words():
    from jsonschema import Draft202012Validator

    schema = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"enum": ["x", "y"]}, "n": {"type": "integer", "minimum": 1}}}
    v = Draft202012Validator(schema)
    messages = [describe_error(e) for e in sorted_errors(v, {"b": 1, "n": 0})]
    assert messages == ["top level: missing required field a", "top level: unknown field b; allowed fields are a, n", "n: must be at least 1"]
    assert [describe_error(e) for e in sorted_errors(v, {"a": "z"})] == ["a: 'z' is not one of x, y"]
