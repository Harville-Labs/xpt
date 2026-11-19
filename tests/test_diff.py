import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from values import Point, values

from xpt.diff import Change, diff
from xpt.path import Attr, Index, Key
from xpt.serialize import serialize
from xpt.value import Int, Str

colliding = st.sampled_from([0, False, -0.0, 1, True, 1.0])
close_values = st.recursive(
    colliding,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.lists(inner, max_size=3).map(tuple),
        st.frozensets(colliding, max_size=3),
        st.sets(colliding, max_size=3),
        st.dictionaries(colliding, inner, max_size=3),
        st.builds(Point, inner, inner),
    ),
    max_leaves=8,
)


def d(expected: object, actual: object):
    return diff(serialize(expected), serialize(actual))


@given(values)
def test_a_value_has_no_changes_against_itself(obj: object):
    assert d(obj, obj).changes == ()


@settings(max_examples=500)
@given(close_values, close_values)
def test_changes_are_empty_exactly_when_python_says_equal(a: object, b: object):
    assert (d(a, b).changes == ()) == (a == b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (1, 1.0),
        (1, True),
        (0.0, -0.0),
        ({1}, frozenset({1.0})),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
    ],
)
def test_values_python_calls_equal_have_no_changes(a: object, b: object):
    assert d(a, b).changes == ()


@pytest.mark.parametrize(("a", "b"), [([1], (1,)), (math.nan, math.nan), (1, "1")])
def test_values_python_calls_unequal_are_replaced(a: object, b: object):
    assert d(a, b).changes == (Change((), "replace", serialize(a), serialize(b)),)


def test_a_changed_dict_entry_is_reported_at_its_key():
    result = d({"name": "a", "size": 1}, {"name": "a", "size": 2})
    assert result.changes == (Change((Key(Str("size")),), "replace", Int(1), Int(2)),)
    assert result.unchanged == 1


def test_dict_keys_only_on_one_side_are_added_or_removed():
    assert d({"a": 1}, {"b": 2}).changes == (
        Change((Key(Str("a")),), "remove", Int(1), None),
        Change((Key(Str("b")),), "add", None, Int(2)),
    )


def test_sequences_compare_by_index_and_report_the_tail():
    assert d([1, 2, 3], [1, 9]).changes == (
        Change((Index(1),), "replace", Int(2), Int(9)),
        Change((Index(2),), "remove", Int(3), None),
    )
    assert d((1,), (1, 2)).changes == (Change((Index(1),), "add", None, Int(2)),)


def test_set_members_are_added_or_removed_at_the_set():
    assert d({1, 2}, {2, 3}).changes == (
        Change((), "remove", Int(1), None),
        Change((), "add", None, Int(3)),
    )


def test_dataclass_fields_compare_by_name():
    assert d(Point(1, [2]), Point(1, [3])).changes == (
        Change((Attr("y"), Index(0)), "replace", Int(2), Int(3)),
    )


def test_unchanged_counts_every_equal_leaf():
    assert d({"a": [1, 2, 3], "b": {4, 5}}, {"a": [1, 2, 3], "b": {4, 5}}).unchanged == 5
