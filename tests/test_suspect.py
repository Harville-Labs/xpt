import pytest

from xpt.path import format_path
from xpt.serialize import serialize
from xpt.suspect import suspect


def found(expected: object, actual: object) -> tuple[str, str] | None:
    """The path and rule of the first suspicious part of `actual`, or None."""
    result = suspect(None if expected is MISSING else serialize(expected), serialize(actual))
    return None if result is None else (format_path(result.path), result.rule)


MISSING = object()


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("ok", "ValueError('bad input')", ("value", "exception")),
        ({"a": 1}, {"a": "KeyError: 'b'"}, ('value["a"]', "exception")),
        ([1], ["Exception('boom')"], ("value[0]", "exception")),
        (
            "ok",
            'Traceback (most recent call last):\n  File "x.py", line 1\nOSError: no',
            ("value", "exception"),
        ),
        (MISSING, "builtins.RuntimeError: stopped", ("value", "exception")),
        ({"user": {"id": 1}}, {"user": None}, ('value["user"]', "none")),
        (0, None, ("value", "none")),
        ({"items": [1, 2]}, {"items": []}, ('value["items"]', "empty")),
        ({1, 2}, set(), ("value", "empty")),
        ({"a": 1}, {}, ("value", "empty")),
        ([1.5, 2.0], [1.5, float("nan")], ("value[1]", "nan")),
        ({"score": 3}, {"score": float("nan")}, ('value["score"]', "nan")),
    ],
)
def test_a_value_that_looks_broken_names_its_path_and_rule(
    expected: object, actual: object, result: tuple[str, str]
):
    assert found(expected, actual) == result


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("ValueError('a')", "ValueError('b')"),
        ("ok", "ValueError"),
        ("ok", "no Error here"),
        (MISSING, None),
        (MISSING, []),
        (MISSING, float("nan")),
        (None, None),
        ([], []),
        ("", []),
        ([1], [1, None]),
        (float("nan"), float("nan")),
        (float("inf"), float("nan")),
        (True, float("nan")),
        ({"a": 1}, {"a": 2}),
        ([1, 2], [1, 3, 4]),
    ],
)
def test_an_ordinary_change_is_not_suspect(expected: object, actual: object):
    assert found(expected, actual) is None


def test_a_missing_expectation_is_checked_for_exceptions_inside_containers():
    assert found(MISSING, {"errors": ["TypeError: bad"]}) == ('value["errors"][0]', "exception")


def test_the_reason_names_the_path_and_the_rule():
    result = suspect(serialize({"items": [1]}), serialize({"items": []}))
    assert result is not None
    assert result.reason == (
        'value["items"] is empty where the expected value had items (rule: empty).'
    )
