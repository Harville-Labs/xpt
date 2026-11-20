"""expect(..., key=k): one call site holding a dict of expected values, one entry per case."""

from __future__ import annotations

from xpt.diff import norm
from xpt.options import Options
from xpt.value import Dict, Value


def cases(expected: Value, how: Options) -> Dict:
    """The expected dict with each case's value prepared the way its actual value is."""
    if not isinstance(expected, Dict):
        raise TypeError(_NOT_A_DICT)
    return Dict(tuple((k, how.prepare(v)) for k, v in expected.items))


def entry(cases: Value | None, key: Value) -> Value | None:
    if isinstance(cases, Dict):
        for k, v in cases.items:
            if norm(k) == norm(key):
                return v
    return None


def with_entry(cases: Value | None, key: Value, value: Value) -> Dict:
    """`cases` with `key` set to `value`, in place if present and appended if not."""
    items = cases.items if isinstance(cases, Dict) else ()
    if entry(cases, key) is None:
        return Dict((*items, (key, value)))
    return Dict(tuple((k, value if norm(k) == norm(key) else v) for k, v in items))


def check(expected: object) -> dict[object, object]:
    if not isinstance(expected, dict):
        raise TypeError(f"{_NOT_A_DICT} Got {type(expected).__name__}.")
    return expected


_NOT_A_DICT = (
    "with key=, expected must be a dict from each key to that case's expected value, "
    "such as expect(double(x), {1: 2, 2: 4}, key=x)."
)
