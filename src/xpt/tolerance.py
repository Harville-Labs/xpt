"""How far a floating-point actual value may be from the expected one and still pass."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Tolerance:
    """`|actual - expected| <= atol + rtol * |expected|`, applied to floats and complex parts.

    Integers, strings, and every other value still compare exactly. An infinity equals only
    itself, and nan equals nan.
    """

    atol: float = 0.0
    rtol: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.atol or self.rtol)

    def close(self, actual: float, expected: float) -> bool:
        if actual == expected or (math.isnan(actual) and math.isnan(expected)):
            return True
        if not (math.isfinite(actual) and math.isfinite(expected)):
            return False
        return abs(actual - expected) <= self.atol + self.rtol * abs(expected)

    def within(self, actual: Any, expected: Any) -> Any:
        """Element-wise `close` for NumPy arrays and PyTorch tensors of floats."""
        same = (actual == expected) | ((actual != actual) & (expected != expected))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return same | (abs(actual - expected) <= self.atol + self.rtol * abs(expected))

    def label(self) -> str:
        return ", ".join(f"{name}={value:g}" for name, value in self.fields())

    def fields(self) -> tuple[tuple[str, float], ...]:
        return tuple((n, v) for n, v in (("atol", self.atol), ("rtol", self.rtol)) if v)


EXACT = Tolerance()


def tolerance(atol: object, rtol: object) -> Tolerance:
    return Tolerance(_bound("atol", atol), _bound("rtol", rtol))


def _bound(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be an int or float, got {type(value).__name__}.")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number of 0 or more, got {value!r}.")
    return float(value)
