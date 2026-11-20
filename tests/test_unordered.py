import textwrap
from pathlib import Path

import pytest
from test_record import finished, observation
from test_rewrite import rewritten

from xpt import expect
from xpt.diff import diff
from xpt.group import group
from xpt.options import options
from xpt.record import load_run, to_record
from xpt.render import group_diff
from xpt.serialize import serialize
from xpt.value import Dataclass, Int, Seq


def failure(actual: object, expected: object, **how: object) -> str:
    with pytest.raises(AssertionError) as info:
        expect(actual, expected, **how)  # type: ignore
    return str(info.value)


def test_lists_in_any_order_pass():
    expect([3, 1, 2], [1, 2, 3], unordered=True)


def test_nested_lists_and_tuples_are_unordered_too():
    expect({"tags": [("b", 2), ("a", 1)]}, {"tags": [("a", 1), ("b", 2)]}, unordered=True)


def test_repeated_items_must_match_in_count():
    assert failure([1, 1, 2], [1, 2, 2], unordered=True) == textwrap.dedent("""\
        expect() mismatch (unordered):
          value: removed 2
          value: added 1
          2 changed, 2 unchanged""")


def test_items_match_within_the_tolerance():
    expect([2.0004, 1.0], [1.0, 2.0], unordered=True, atol=1e-3)


def test_without_unordered_a_reordered_list_still_fails():
    failure([2, 1], [1, 2])


def test_the_generated_value_is_written_sorted(recorder):
    with pytest.raises(AssertionError) as info:
        expect(["b", "a"], unordered=True)
    assert 'Actual: ["a", "b"]' in str(info.value)


def test_array_elements_keep_their_order():
    array = Dataclass("numpy:array", (("", Seq("list", (Int(2), Int(1)))),))
    assert options(0.0, 0.0, (), True).prepare(Seq("list", (array,))) == Seq("list", (array,))


def test_unordered_must_be_a_bool():
    with pytest.raises(TypeError, match=r"unordered must be True or False, got str."):
        expect(1, 1, unordered="yes")  # type: ignore


def test_a_multiset_diff_counts_matched_items_as_unchanged():
    found = diff(serialize([1, 2, 3]), serialize([3, 4, 1]), unordered=True)
    assert [(c.op, c.expected, c.actual) for c in found.changes] == [
        ("remove", Int(2), None),
        ("add", None, Int(4)),
    ]
    assert found.unchanged == 2


def test_unordered_is_recorded_so_review_diffs_use_it(tmp_path: Path):
    run = "20260101T000000-aaaa"
    seen = observation(
        run,
        expected=Seq("list", (Int(1), Int(2))),
        actual=Seq("list", (Int(1), Int(3))),
        options=options(0.0, 0.0, (), True),
    )
    finished(tmp_path, run, seen)
    [loaded] = load_run(tmp_path)
    assert loaded == seen
    assert to_record(seen)["unordered"] is True
    found = group_diff(group([loaded])[0])
    assert found is not None
    assert [c.op for c in found.changes] == ["remove", "add"]


def test_accept_keeps_the_unordered_keyword_after_the_expected_value():
    text = rewritten("def test_it():\n    expect(compute(), unordered=True)\n", serialize([1, 2]))
    assert text == "def test_it():\n    expect(compute(), [1, 2], unordered=True)\n"
