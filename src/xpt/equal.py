from __future__ import annotations

import dataclasses
import math
from typing import Any

from xpt import adapters, digest
from xpt.digest import Digest
from xpt.tolerance import EXACT, Tolerance


def equal(a: Any, b: Any, tol: Tolerance = EXACT) -> bool:
    """Python's ==, except that arrays and frames must match in type, shape, and dtype.

    NaN equals NaN, so a recorded float("nan") passes against a NaN result. `tol` lets
    floats and complex parts of `a`, the actual value, differ from `b`, the expected one.

    Lists, tuples, dicts, and dataclasses are compared item by item so an array inside one
    is compared the same way.
    """
    if isinstance(b, Digest):
        return digest.of(a) == b
    if isinstance(a, Digest):
        return digest.of(b) == a
    same = adapters.same(a, b, tol)
    if same is not None:
        return same
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if tol and (close := _close(a, b, tol)) is not None:
        return close
    kind = type(a)
    if kind is type(b):
        if kind is list or kind is tuple:
            return len(a) == len(b) and all(_item(x, y, tol) for x, y in zip(a, b, strict=True))
        if kind is dict:
            return a.keys() == b.keys() and all(_item(a[k], b[k], tol) for k in a)
    try:
        return bool(a == b)
    except (ValueError, RuntimeError):
        if kind is type(b) and dataclasses.is_dataclass(a) and not isinstance(a, type):
            names = [f.name for f in dataclasses.fields(a) if f.compare]
            return all(_item(getattr(a, n), getattr(b, n), tol) for n in names)
        raise


def _item(a: object, b: object, tol: Tolerance) -> bool:
    return a is b or equal(a, b, tol)


def _close(a: object, b: object, tol: Tolerance) -> bool | None:
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if not (isinstance(a, int | float | complex) and isinstance(b, int | float | complex)):
        return None
    if isinstance(a, int) and isinstance(b, int):
        return None
    return tol.close(a.real, b.real) and tol.close(a.imag, b.imag)
