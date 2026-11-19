import pytest
from support import assert_rebuilds

from xpt import expect
from xpt.equal import equal
from xpt.serialize import UnsupportedValue, serialize

pd = pytest.importorskip("pandas")

NAMES = {"pandas:Series": "pd.Series", "pandas:DataFrame": "pd.DataFrame"}
SCOPE = {"pd": pd}


def test_a_series_is_written_with_its_dtype_and_name():
    source = assert_rebuilds(pd.Series([1, 2], name="n"), NAMES, SCOPE)
    assert source == 'pd.Series([1, 2], dtype="int64", name="n")'


def test_a_series_with_a_custom_index_keeps_it():
    source = assert_rebuilds(pd.Series([1.5], index=["a"]), NAMES, SCOPE)
    assert source == 'pd.Series([1.5], dtype="float64", index=["a"])'


def test_a_dataframe_is_written_column_by_column():
    frame = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    source = assert_rebuilds(frame, NAMES, SCOPE)
    assert source == 'pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})'


def test_a_dataframe_column_whose_dtype_would_change_is_unsupported():
    with pytest.raises(UnsupportedValue, match="column 'a' has dtype int32"):
        serialize(pd.DataFrame({"a": pd.Series([1], dtype="int32")}))


def test_equal_frames_pass_expect_and_different_dtypes_fail():
    expect(pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [1]}))
    assert not equal(pd.Series([1]), pd.Series([1.0]))


def test_a_frame_mismatch_points_at_the_column_and_row():
    with pytest.raises(AssertionError) as raised:
        expect(pd.DataFrame({"a": [1, 2]}), pd.DataFrame({"a": [1, 3]}))
    assert 'value["a"][1]' in str(raised.value)


INDEX_NAMES = NAMES | {
    "pandas:Index": "pd.Index",
    "pandas:MultiIndex.from_tuples": "pd.MultiIndex.from_tuples",
}


def test_a_named_index_is_written_as_a_pandas_index():
    series = pd.Series([1], index=pd.Index(["a"], name="key"))
    source = assert_rebuilds(series, INDEX_NAMES, SCOPE)
    assert source == 'pd.Series([1], dtype="int64", index=pd.Index(["a"], name="key"))'


def test_a_multi_level_index_is_written_from_its_tuples():
    index = pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["k", None])
    source = assert_rebuilds(pd.DataFrame({"v": [1, 2]}, index=index), INDEX_NAMES, SCOPE)
    assert source == (
        "pd.DataFrame(\n"
        '    {"v": [1, 2]},\n'
        '    index=pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["k", None]),\n'
        ")"
    )


def test_an_index_whose_dtype_would_change_keeps_its_dtype():
    series = pd.Series([1], index=pd.Index([2], dtype="int32"))
    source = assert_rebuilds(series, INDEX_NAMES, SCOPE)
    assert source == 'pd.Series([1], dtype="int64", index=pd.Index([2], dtype="int32"))'


def test_multi_level_columns_are_written_as_tuple_keys():
    frame = pd.DataFrame({("a", "x"): [1], ("a", "y"): [2]})
    source = assert_rebuilds(frame, INDEX_NAMES, SCOPE)
    assert source == 'pd.DataFrame({("a", "x"): [1], ("a", "y"): [2]})'


def test_index_names_take_part_in_equality():
    assert not equal(
        pd.Series([1], index=pd.Index([0], name="a")), pd.Series([1], index=pd.Index([0]))
    )
