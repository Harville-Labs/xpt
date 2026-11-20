import math
import textwrap
from pathlib import Path

import pytest
from test_record import finished, observation
from test_rewrite import rewritten

from xpt import expect
from xpt.diff import diff
from xpt.group import group
from xpt.options import Options
from xpt.record import load_run, to_record
from xpt.render import group_diff
from xpt.serialize import serialize
from xpt.tolerance import Tolerance
from xpt.value import Float, Seq


def failure(actual: object, expected: object, *, atol: float = 0.0, rtol: float = 0.0) -> str:
    with pytest.raises(AssertionError) as info:
        expect(actual, expected, atol=atol, rtol=rtol)
    return str(info.value)


def test_a_float_within_atol_passes():
    expect(1.0005, 1.0, atol=1e-3)
    expect({"x": [1.0, 2.0004]}, {"x": [1.0, 2.0]}, atol=1e-3)


def test_rtol_scales_with_the_expected_value():
    expect(102.0, 100.0, rtol=0.05)
    assert "value: 0.0 -> 1e-09" in failure(1e-9, 0.0, rtol=0.5)


def test_a_float_outside_the_tolerance_fails_and_the_header_names_it():
    assert failure([1.0, 1.01], [1.0, 1.0], atol=1e-3) == textwrap.dedent("""\
        expect() mismatch (atol=0.001):
          value[1]: 1.0 -> 1.01
          1 changed, 1 unchanged""")


def test_integers_compare_exactly_under_a_tolerance():
    assert "value: 1 -> 2" in failure(2, 1, atol=5)
    expect(1.5, 1, atol=1)


def test_infinities_and_nan_are_equal_only_to_themselves():
    expect(math.inf, math.inf, atol=1)
    expect(math.nan, math.nan, atol=1)
    failure(1e308, math.inf, rtol=1)
    failure(math.nan, 1.0, atol=1)


def test_each_part_of_a_complex_number_is_held_to_the_tolerance():
    expect(1.0005 + 2.0005j, 1 + 2j, atol=1e-3)
    assert "value.imag: 2.0 -> 2.1" in failure(1 + 2.1j, 1 + 2j, atol=1e-3)


@pytest.mark.parametrize(
    ("tolerance", "error", "message"),
    [
        ({"atol": -1.0}, ValueError, "atol must be a finite number of 0 or more, got -1.0."),
        ({"rtol": math.inf}, ValueError, "rtol must be a finite number of 0 or more, got inf."),
        ({"atol": True}, TypeError, "atol must be an int or float, got bool."),
        ({"rtol": "0.1"}, TypeError, "rtol must be an int or float, got str."),
    ],
)
def test_a_bad_tolerance_is_refused_with_its_name(
    tolerance: dict[str, object], error: type[Exception], message: str
):
    with pytest.raises(error) as info:
        expect(1.0, 1.0, **tolerance)  # type: ignore
    assert str(info.value) == message


def test_diff_counts_numbers_within_the_tolerance_as_unchanged():
    found = diff(serialize([1.0, 2.0, 3.0]), serialize([1.0004, 2.5, 3.0]), Tolerance(atol=1e-3))
    assert [(c.expected, c.actual) for c in found.changes] == [(Float("2.0"), Float("2.5"))]
    assert found.unchanged == 2


def test_the_tolerance_is_recorded_so_review_diffs_use_it(tmp_path: Path):
    run = "20260101T000000-aaaa"
    within = observation(
        run,
        expected=Seq("list", (Float("1.0"), Float("2.0"))),
        actual=Seq("list", (Float("1.0004"), Float("2.5"))),
        options=Options(Tolerance(atol=1e-3)),
    )
    finished(tmp_path, run, within)
    [loaded] = load_run(tmp_path)
    assert loaded == within
    found = group_diff(group([loaded])[0])
    assert found is not None
    assert [c.actual for c in found.changes] == [Float("2.5")]


def test_an_exact_observation_records_no_tolerance_fields():
    assert "atol" not in to_record(observation("20260101T000000-aaaa"))


def test_accept_inserts_the_expected_value_before_the_tolerance_keyword():
    text = rewritten("def test_it():\n    expect(compute(), atol=0.01)\n", serialize([1.5]))
    assert text == "def test_it():\n    expect(compute(), [1.5], atol=0.01)\n"
