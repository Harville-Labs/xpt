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
