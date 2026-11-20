import pytest
from support import assert_rebuilds

from xpt import expect
from xpt.equal import equal
from xpt.serialize import UnsupportedValue, serialize
from xpt.tolerance import Tolerance
from xpt.value import Float, Int

np = pytest.importorskip("numpy")

NAMES = {"numpy:array": "np.array", "numpy:zeros": "np.zeros"}
SCOPE = {"np": np}


def test_an_array_is_written_as_a_call_with_its_dtype():
    source = assert_rebuilds(np.array([[1, 2], [3, 4]]), NAMES, SCOPE)
    assert source == 'np.array([[1, 2], [3, 4]], dtype="int64")'


def test_float32_values_print_the_shortest_text_that_round_trips():
    source = assert_rebuilds(np.array([0.1, 1.5], dtype=np.float32), NAMES, SCOPE)
    assert source == 'np.array([0.1, 1.5], dtype="float32")'


def test_float16_bool_and_string_arrays_rebuild():
    assert_rebuilds(np.array([0.1, -2.0], dtype=np.float16), NAMES, SCOPE)
    assert_rebuilds(np.array([True, False]), NAMES, SCOPE)
    assert (
        assert_rebuilds(np.array(["ab", "c"]), NAMES, SCOPE) == 'np.array(["ab", "c"], dtype="<U2")'
    )


def test_an_empty_array_keeps_its_shape():
    source = assert_rebuilds(np.zeros((0, 3), dtype=np.int32), NAMES, SCOPE)
    assert source == 'np.zeros((0, 3), dtype="int32")'


def test_a_zero_dimensional_array_rebuilds():
    assert_rebuilds(np.array(7), NAMES, SCOPE)


def test_a_numpy_scalar_is_recorded_as_the_python_number():
    assert to_python(np.int64(3)) == 3
    assert to_python(np.float32(0.5)) == 0.5


def to_python(obj: object) -> object:
    value = serialize(obj)
    assert isinstance(value, Int | Float)
    return value.v


def test_datetime_arrays_are_unsupported_with_the_dtype_named():
    with pytest.raises(UnsupportedValue, match="datetime64"):
        serialize(np.array(["2026-01-01"], dtype="datetime64[D]"))


def test_equal_arrays_pass_expect_even_inside_containers():
    expect(np.array([1, 2]), np.array([1, 2]))
    expect({"a": [np.array([1, 2])]}, {"a": [np.array([1, 2])]})


def test_equal_requires_the_same_shape_and_dtype():
    assert not equal(np.array([1]), np.array([1.0]))
    assert not equal(np.array([1]), np.array([[1]]))
    assert not equal({"a": np.array([1])}, {"a": np.array([1.0])})
    assert not equal(np.array([1, 2]), [1, 2])


def test_a_mismatch_points_at_the_differing_element():
    with pytest.raises(AssertionError) as raised:
        expect(np.array([[1, 2], [3, 4]]), np.array([[1, 2], [3, 5]]))
    assert "value[1][1]" in str(raised.value)
    assert "5 -> 4" in str(raised.value)


def test_a_dtype_mismatch_is_reported_on_the_dtype():
    with pytest.raises(AssertionError) as raised:
        expect(np.array([1, 2], dtype=np.int32), np.array([1, 2], dtype=np.int64))
    assert "value.dtype" in str(raised.value)


def test_nan_elements_in_the_same_place_are_equal():
    expect(np.array([np.nan, 1.0]), np.array([np.nan, 1.0]))
    assert not equal(np.array([np.nan, 1.0]), np.array([1.0, np.nan]))


def test_a_complex_array_rebuilds_with_complex_elements():
    names = NAMES | {"builtins:complex": "complex"}
    source = assert_rebuilds(np.array([1 + 2j], dtype="complex64"), names, SCOPE)
    assert source == 'np.array([complex(real=1.0, imag=2.0)], dtype="complex64")'


def test_a_float_array_within_atol_passes_and_one_outside_fails():
    expect(np.array([1.0, 2.0004]), np.array([1.0, 2.0]), atol=1e-3)
    with pytest.raises(AssertionError, match=r"value\[1\]: 2.0 -> 2.5"):
        expect(np.array([1.0, 2.5]), np.array([1.0, 2.0]), atol=1e-3)


def test_a_tolerance_leaves_integer_arrays_exact():
    assert not equal(np.array([2]), np.array([1]), Tolerance(atol=5))


def test_a_complex_array_holds_each_part_to_the_tolerance():
    expect(np.array([1.0005 + 2j]), np.array([1 + 2j]), atol=1e-3)
    assert not equal(np.array([1 + 2.1j]), np.array([1 + 2j]), Tolerance(atol=1e-3))


def test_unordered_reorders_a_list_of_arrays_but_not_their_elements():
    expect(
        [np.array([3, 4]), np.array([1, 2])], [np.array([1, 2]), np.array([3, 4])], unordered=True
    )
    with pytest.raises(AssertionError, match=r"expect\(\) mismatch \(unordered\):"):
        expect([np.array([2, 1])], [np.array([1, 2])], unordered=True)
