import hashlib
import textwrap
from typing import Any

import pytest
from support import assert_rebuilds

from xpt import expect
from xpt.equal import equal
from xpt.serialize import serialize
from xpt.source import to_source

np = pytest.importorskip("numpy")

NAMES = {"xpt:expect.digest": "expect.digest"}
SCOPE = {"np": np, "expect": expect}


def big(dtype: str = "float64") -> Any:
    return np.arange(20_000, dtype=dtype).reshape(200, 100) / 2


def test_an_array_over_the_inline_limit_is_written_as_a_digest():
    sha256 = hashlib.sha256(big().tobytes()).hexdigest()
    assert to_source(serialize(big()), names=NAMES) == textwrap.dedent(f"""\
        expect.digest(
            kind="numpy",
            shape=(200, 100),
            dtype="float64",
            sha256="{sha256}",
            min=0.0,
            max=9999.5,
            mean=4999.75,
        )""")


def test_a_digest_rebuilds_and_equals_the_array():
    assert_rebuilds(big(), NAMES, SCOPE)
    expect(big(), eval(to_source(serialize(big()), names=NAMES), SCOPE))


def test_a_digest_compares_shape_dtype_and_bytes_and_ignores_the_summary():
    recorded = eval(to_source(serialize(big()), names=NAMES), SCOPE)
    changed = big()
    changed[3, 1] = -1
    assert not equal(changed, recorded)
    assert not equal(big().reshape(100, 200), recorded)
    assert not equal(big().astype("float32"), recorded)
    edited = eval(to_source(serialize(big()), names=NAMES).replace("mean=4999.75", "mean=0"), SCOPE)
    assert equal(big(), edited)


def test_a_digest_mismatch_shows_the_hash_and_summary_changes():
    recorded = eval(to_source(serialize(big()), names=NAMES), SCOPE)
    with pytest.raises(AssertionError) as info:
        expect(big() + 1, recorded)
    lines = str(info.value).splitlines()
    assert lines[0] == "expect() mismatch:"
    assert lines[1].startswith('  value.sha256: "')
    assert lines[2:] == [
        "  value.min: 0.0 -> 1.0",
        "  value.max: 9999.5 -> 10000.5",
        "  value.mean: 4999.75 -> 5000.75",
        "  4 changed, 4 unchanged",
    ]


def test_the_hash_ignores_byte_order():
    assert equal(big(">f8"), eval(to_source(serialize(big()), names=NAMES), SCOPE))


def test_a_bool_or_string_digest_has_no_summary():
    source = to_source(serialize(np.zeros(20_000, dtype=bool)), names=NAMES)
    assert "min=" not in source
    assert 'dtype="bool"' in source


def test_many_differing_elements_are_counted_with_the_largest_difference():
    expected = np.zeros((30, 40))
    actual = expected.copy()
    actual[3, 1] = 0.25
    actual[5, ::3] = 0.01
    with pytest.raises(AssertionError) as info:
        expect(actual, expected)
    lines = str(info.value).splitlines()
    assert lines[1] == "  value: 15 of 1200 elements differ, the largest by 0.25 at value[3][1]"
    assert lines[2] == "  value[3][1]: 0.0 -> 0.25"


def test_one_differing_element_gets_no_count_line():
    with pytest.raises(AssertionError) as info:
        expect(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.5, 3.0]))
    assert str(info.value) == textwrap.dedent("""\
        expect() mismatch:
          value[1]: 2.5 -> 2.0
          1 changed, 3 unchanged""")


def test_a_count_line_for_a_bool_array_has_no_largest_difference():
    with pytest.raises(AssertionError) as info:
        expect(np.array([True, True, True]), np.array([False, False, True]))
    assert "  value: 2 of 3 elements differ" in str(info.value).splitlines()
