import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from values import Point

from xpt import expect


def failure(actual: object, *expected: object) -> str:
    with pytest.raises(AssertionError) as info:
        expect(actual, *expected)
    return str(info.value)


def test_an_equal_value_passes():
    expect({"a": [1, 2]}, {"a": [1, 2]})
    expect(1, 1.0)


def test_a_missing_expected_value_fails_and_shows_the_actual_value():
    assert failure({"a": 1}) == 'expect() has no expected value. Actual: {"a": 1}'


def test_an_explicit_none_is_an_expected_value_not_a_missing_one():
    expect(None, None)


def test_a_mismatch_lists_each_change_at_its_path():
    assert failure({"a": [1, 2], "b": 3}, {"a": [1, 9, 4], "b": 3}) == textwrap.dedent("""\
        expect() mismatch:
          value["a"][1]: 9 -> 2
          value["a"][2]: removed 4
          2 changed, 2 unchanged""")


def test_a_top_level_mismatch_and_added_set_members_read_plainly():
    assert failure({1, 2}, {1}) == "expect() mismatch:\n  value: added 2\n  1 changed, 1 unchanged"
    assert failure(Point(1, 2), "p").splitlines()[1] == '  value: "p" -> Point(x=1, y=2)'


def test_more_than_ten_changes_are_cut_with_a_count():
    lines = failure(list(range(12)), [-1] * 12).splitlines()
    assert lines[10] == "  value[9]: -1 -> 9"
    assert lines[11] == "  ... 2 more changes"
    assert lines[12] == "  12 changed, 0 unchanged"


def test_long_values_are_cut_to_eighty_characters_with_their_length():
    line = failure("x" * 200, "y").splitlines()[1]
    assert line == '  value: "y" -> "' + "x" * 79 + "... (202 chars)"


def test_multiline_text_stays_on_one_line_in_the_report():
    assert failure("a\nb", "a").splitlines()[1] == '  value: "a" -> "a\\nb"'


def test_a_failing_expect_fails_under_plain_unittest(tmp_path: Path):
    (tmp_path / "test_plain.py").write_text(
        textwrap.dedent("""\
            import unittest
            from xpt import expect

            class T(unittest.TestCase):
                def test_it(self):
                    expect([1], [2])
            """)
    )
    run = subprocess.run(
        [sys.executable, "-m", "unittest", "test_plain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert "value[0]: 2 -> 1" in run.stderr
