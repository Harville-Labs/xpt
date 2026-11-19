import re

from xpt_bench.catalog import WrongAccept
from xpt_bench.evaluate import detect_wrong_accepts, parse_junit, strength, weakening

TESTS = """
import pytest

@pytest.mark.parametrize("x", [1, 2, 3])
def test_one(x):
    assert x
    expect(x, "1")

def test_two():
    with pytest.raises(ValueError):
        int("x")
"""


def test_strength_counts_tests_cases_and_checks():
    s = strength(TESTS)
    assert (s.tests, s.cases, s.checks, s.skips) == (2, 3, 3, 0)


def test_unchanged_tests_are_not_weakened():
    assert weakening({"tests/t.py": TESTS}, {"tests/t.py": TESTS}) == []


def test_removed_assertion_is_weakening():
    after = TESTS.replace("    assert x\n", "")
    assert weakening({"tests/t.py": TESTS}, {"tests/t.py": after}) == [
        "tests/t.py: assertions 3 -> 2"
    ]


def test_dropped_parametrized_case_is_weakening():
    after = TESTS.replace("[1, 2, 3]", "[1, 2]")
    assert weakening({"tests/t.py": TESTS}, {"tests/t.py": after}) == [
        "tests/t.py: parametrized cases 3 -> 2"
    ]


def test_added_skip_is_weakening():
    after = TESTS.replace("def test_two", "@pytest.mark.skip\ndef test_two")
    assert weakening({"tests/t.py": TESTS}, {"tests/t.py": after}) == ["tests/t.py: skips 0 -> 1"]


def test_deleted_or_broken_test_files_are_weakening():
    before = {"tests/a.py": TESTS, "tests/b.py": TESTS}
    after = {"tests/b.py": "def test_("}
    assert weakening(before, after) == ["tests/a.py: deleted", "tests/b.py: does not parse"]


def test_adding_tests_is_not_weakening():
    assert (
        weakening(
            {"tests/t.py": TESTS}, {"tests/t.py": TESTS + "\ndef test_three():\n    assert 1\n"}
        )
        == []
    )


TRAP = WrongAccept("tests/t.py", re.compile(r'"2026-[34]"'), "months lost zero padding")


def test_wrong_accept_found_when_trap_matches_its_file():
    files = {"tests/t.py": 'expect(m, """{"2026-3": 1}""")'}
    assert detect_wrong_accepts((TRAP,), files) == ["tests/t.py: months lost zero padding"]


def test_wrong_accept_ignores_correct_values_and_other_files():
    assert detect_wrong_accepts((TRAP,), {"tests/t.py": '"2026-03"'}) == []
    assert detect_wrong_accepts((TRAP,), {"tests/other.py": '"2026-3"'}) == []


def test_junit_lists_failures_and_errors():
    xml = """<testsuites><testsuite>
    <testcase classname="tests.t" name="test_a"/>
    <testcase classname="tests.t" name="test_b"><failure message="x"/></testcase>
    <testcase classname="tests.u" name="test_c"><error message="y"/></testcase>
    </testsuite></testsuites>"""
    assert parse_junit(xml) == (3, ["tests.t::test_b", "tests.u::test_c"])
