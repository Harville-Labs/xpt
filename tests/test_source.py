import ast
import math
import shutil
import subprocess

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from values import Point, values

from xpt.serialize import UnsupportedValue, serialize
from xpt.source import to_source

NAMES = {"values:Point": "Point"}


def source(obj: object, indent: int = 0, column: int | None = None) -> str:
    return to_source(serialize(obj), indent, column=column, names=NAMES)


def evaluate(src: str) -> object:
    return eval(compile(ast.parse(src, mode="eval"), "<test>", "eval"), {"Point": Point})


def ruff_changes(src: str) -> str:
    ruff = shutil.which("ruff")
    assert ruff, "ruff must be installed in the dev environment"
    done = subprocess.run(
        [ruff, "format", "--isolated", "--line-length", "100", "--diff", "-"],
        input=src,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout


@given(values)
def test_printed_source_evaluates_to_the_same_value(obj: object):
    assert serialize(evaluate(source(obj))) == serialize(obj)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values, st.sampled_from([0, 4, 8]))
def test_ruff_format_leaves_printed_source_unchanged(obj: object, indent: int):
    statement = " " * indent + "x = " + source(obj, indent, column=indent + 4)
    blocks = ["def f():", "    if x:"][: indent // 4]
    assert ruff_changes("\n".join([*blocks, statement]) + "\n") == ""


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ([], "[]"),
        ((), "()"),
        ((1,), "(1,)"),
        (set(), "set()"),
        (frozenset({2, 1}), "frozenset({1, 2})"),
        ({"a": [1, 2.5, None, True]}, '{"a": [1, 2.5, None, True]}'),
        (Point(x=1, y=(2,)), "Point(x=1, y=(2,))"),
        (-0.0, "-0.0"),
        (math.inf, 'float("inf")'),
        (1e16, "1e16"),
        (1e-05, "1e-05"),
        ('a"b', "'a\"b'"),
        ("a'b\"c", '"a\'b\\"c"'),
        ("\x00\t", '"\\x00\\t"'),
        ("é", '"é"'),
    ],
)
def test_short_values_print_on_one_line(obj: object, expected: str):
    assert source(obj) == expected


def test_nan_prints_as_a_float_call():
    assert source(math.nan) == 'float("nan")'


def test_long_collections_put_one_element_per_line_with_trailing_commas():
    words = ["a" * 40, "b" * 40, "c" * 40]
    assert source({"k": words, "j": 1}) == (
        "{\n"
        '    "k": [\n'
        f'        "{"a" * 40}",\n'
        f'        "{"b" * 40}",\n'
        f'        "{"c" * 40}",\n'
        "    ],\n"
        '    "j": 1,\n'
        "}"
    )


def test_a_single_long_child_gets_its_own_line_without_a_trailing_comma():
    assert source(frozenset({"a" * 50, "b" * 50})) == (
        f'frozenset(\n    {{\n        "{"a" * 50}",\n        "{"b" * 50}",\n    }}\n)'
    )


def test_nested_lines_indent_from_the_statement_not_the_start_column():
    printed = source(["a" * 60, "b" * 60], indent=4, column=30)
    assert printed.splitlines()[1].startswith('        "a')
    assert printed.splitlines()[-1] == "    ]"


def test_wide_characters_count_double_toward_the_width():
    assert "\n" in source(["界" * 26, "界" * 26])
    assert "\n" not in source(["a" * 26, "a" * 26])


def test_multiline_text_prints_triple_quoted():
    assert source("line one\nline two\n") == '"""line one\nline two\n"""'


@pytest.mark.parametrize("text", ['ends with a quote\n"', 'has """ inside\n', "back\\slash\n"])
def test_multiline_text_that_triple_quotes_cannot_hold_is_escaped(text: str):
    printed = source(text)
    assert "\n" not in printed
    assert evaluate(printed) == text


def test_a_dataclass_without_a_resolvable_name_is_unsupported():
    with pytest.raises(UnsupportedValue, match=r"value\[0\] is a values:Point"):
        to_source(serialize([Point(1, 2)]))
