"""Interval estimates for the report, using only the standard library."""

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054
RESAMPLES = 4000
SEED = 0


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


def wilson(successes: int, n: int, z: float = Z95) -> Interval:
    """Wilson score interval for a binomial proportion. An empty sample spans [0, 1]."""
    if n == 0:
        return Interval(0.0, 0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(p, max(0.0, center - half), min(1.0, center + half))


def rate_difference(a: tuple[int, int], b: tuple[int, int], z: float = Z95) -> Interval:
    """Newcombe's hybrid score interval for p_a - p_b, built from each arm's Wilson interval.

    `a` and `b` are (successes, n).
    """
    wa, wb = wilson(*a, z=z), wilson(*b, z=z)
    d = wa.estimate - wb.estimate
    low = d - math.sqrt((wa.estimate - wa.low) ** 2 + (wb.high - wb.estimate) ** 2)
    high = d + math.sqrt((wa.high - wa.estimate) ** 2 + (wb.estimate - wb.low) ** 2)
    return Interval(d, max(-1.0, low), min(1.0, high))


def _geomean(values: Sequence[float]) -> float:
    return math.exp(statistics.fmean(math.log(v) for v in values))


def median_ratio(
    cells: Sequence[tuple[Sequence[float], Sequence[float]]],
    resamples: int = RESAMPLES,
    seed: int = SEED,
    level: float = 0.95,
) -> Interval | None:
    """Percentile bootstrap interval for median(a) / median(b).

    Each cell is (a values, b values) for one task. With several cells the estimate is the
    geometric mean of the per-cell ratios, and each resample redraws every cell's runs
    within that cell, so tasks of different sizes are never pooled. Returns None when no
    cell has positive values on both sides.
    """
    usable = [(a, b) for a, b in cells if a and b and min(a) > 0 and min(b) > 0]
    if not usable:
        return None
    rng = random.Random(seed)

    def draw(values: Sequence[float]) -> float:
        return statistics.median(rng.choices(values, k=len(values)))

    estimate = _geomean([statistics.median(a) / statistics.median(b) for a, b in usable])
    boots = sorted(_geomean([draw(a) / draw(b) for a, b in usable]) for _ in range(resamples))
    tail = (1 - level) / 2
    low = boots[int(tail * (resamples - 1))]
    high = boots[math.ceil((1 - tail) * (resamples - 1))]
    return Interval(estimate, min(low, estimate), max(high, estimate))
