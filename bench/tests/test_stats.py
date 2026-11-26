import pytest

from xpt_bench.stats import Interval, median_ratio, rate_difference, wilson


def test_wilson_matches_published_values():
    w = wilson(5, 5)
    assert (w.estimate, w.high) == (1.0, 1.0)
    assert w.low == pytest.approx(0.5655, abs=1e-4)
    w = wilson(0, 10)
    assert (w.estimate, w.low) == (0.0, 0.0)
    assert w.high == pytest.approx(0.2775, abs=1e-4)
    w = wilson(3, 10)
    assert (w.low, w.high) == (pytest.approx(0.1078, abs=1e-4), pytest.approx(0.6032, abs=1e-4))


def test_wilson_of_an_empty_sample_spans_everything():
    assert wilson(0, 0) == Interval(0.0, 0.0, 1.0)


def test_rate_difference_of_equal_arms_contains_zero():
    d = rate_difference((5, 5), (5, 5))
    assert d.estimate == 0
    assert d.low == pytest.approx(-0.4345, abs=1e-4)
    assert d.high == pytest.approx(0.4345, abs=1e-4)


def test_rate_difference_resolves_a_large_gap():
    d = rate_difference((10, 10), (0, 10))
    assert d.estimate == 1.0
    assert d.low > 0
    assert rate_difference((0, 10), (10, 10)).high < 0


def test_median_ratio_is_deterministic_for_a_seed():
    a, b = [90, 100, 110, 120, 95], [100, 130, 105, 140, 98]
    first = median_ratio([(a, b)], resamples=500, seed=3)
    assert first == median_ratio([(a, b)], resamples=500, seed=3)
    assert first is not None
    assert first.estimate == pytest.approx(100 / 105)
    assert first.low <= first.estimate <= first.high


def test_median_ratio_of_separated_arms_excludes_one():
    i = median_ratio([([50, 52, 55, 51, 49], [100, 98, 104, 101, 99])], resamples=500)
    assert i is not None and i.high < 1


def test_median_ratio_of_overlapping_arms_contains_one():
    i = median_ratio([([80, 120, 100, 90, 115], [85, 125, 95, 110, 100])], resamples=500)
    assert i is not None and i.contains(1.0)


def test_median_ratio_combines_tasks_by_geometric_mean():
    i = median_ratio([([50] * 5, [100] * 5), ([400] * 5, [200] * 5)], resamples=200)
    assert i is not None
    assert (i.estimate, i.low, i.high) == pytest.approx((1.0, 1.0, 1.0))


def test_median_ratio_needs_positive_values():
    assert median_ratio([([0, 1], [1, 2])]) is None
    assert median_ratio([([], [1])]) is None
