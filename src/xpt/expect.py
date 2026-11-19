from __future__ import annotations

import sys

from xpt import record, report
from xpt.diff import diff
from xpt.record import Observation, Status
from xpt.serialize import UnsupportedValue, serialize
from xpt.site import locate
from xpt.value import Value

_MISSING = object()


def expect(actual: object, expected: object = _MISSING, *, _depth: int = 0) -> None:
    """Fail unless `actual == expected`, explaining a mismatch path by path.

    Leaving out `expected` always fails, so a new expectation never passes unreviewed.
    A failure is recorded under `.xpt/` for review. A helper that calls `expect` passes
    `_depth=1` so the record points at the helper's caller.
    """
    __tracebackhide__ = True
    if expected is not _MISSING and actual == expected:
        return
    frame = sys._getframe(1 + _depth)

    def save(status: Status, want: Value | None, got: Value | None, detail: str = "") -> str:
        rec = record.recorder
        if not rec.enabled:
            return ""
        site = locate(frame, rec.root)
        test = rec.test or frame.f_code.co_qualname
        try:
            rec.record(Observation(rec.run, test, site, status, want, got, detail))
        except OSError as error:
            return f"xpt could not record this failure: {error}"
        return ""

    try:
        want = None if expected is _MISSING else serialize(expected)
        got = serialize(actual)
    except UnsupportedValue as error:
        if note := save("unsupported", None, None, str(error)):
            error.add_note(note)
        raise
    if want is None:
        status, message = "missing", report.missing(got)
    else:
        status, message = "mismatch", report.mismatch(diff(want, got))
    note = save(status, want, got)
    raise AssertionError(f"{message}\n{note}" if note else message)
