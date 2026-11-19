from __future__ import annotations

from xpt import report
from xpt.diff import diff
from xpt.serialize import serialize

_MISSING = object()


def expect(actual: object, expected: object = _MISSING) -> None:
    """Fail unless `actual == expected`, explaining a mismatch path by path.

    Leaving out `expected` always fails, so a new expectation never passes unreviewed.
    """
    __tracebackhide__ = True
    if expected is _MISSING:
        raise AssertionError(report.missing(serialize(actual)))
    if actual != expected:
        raise AssertionError(report.mismatch(diff(serialize(expected), serialize(actual))))
