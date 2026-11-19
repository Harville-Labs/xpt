from __future__ import annotations

import dataclasses
from typing import Any

from xpt import adapters


def equal(a: Any, b: Any) -> bool:
    """Python's ==, except that arrays and frames must match in type, shape, and dtype.

    Lists, tuples, dicts, and dataclasses are compared item by item so an array inside one
    is compared the same way.
    """
    same = adapters.same(a, b)
    if same is not None:
        return same
    kind = type(a)
    if kind is type(b):
        if kind is list or kind is tuple:
            return len(a) == len(b) and all(_item(x, y) for x, y in zip(a, b, strict=True))
        if kind is dict:
            return a.keys() == b.keys() and all(_item(a[k], b[k]) for k in a)
    try:
        return bool(a == b)
    except (ValueError, RuntimeError):
        if kind is type(b) and dataclasses.is_dataclass(a) and not isinstance(a, type):
            names = [f.name for f in dataclasses.fields(a) if f.compare]
            return all(_item(getattr(a, n), getattr(b, n)) for n in names)
        raise


def _item(a: object, b: object) -> bool:
    return a is b or equal(a, b)
