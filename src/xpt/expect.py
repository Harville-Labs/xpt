from __future__ import annotations

import sys

from xpt import record, report
from xpt.diff import diff
from xpt.record import Observation, Status
from xpt.serialize import UnsupportedValue, serialize
from xpt.site import locate
from xpt.value import Value

_MISSING = object()


class ExpectationFailed(AssertionError):
    """A failed expect() call, carrying its observation for the pytest report."""

    def __init__(self, message: str, observation: Observation) -> None:
        super().__init__(message)
        self.observation = observation


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
    rec = record.recorder

    def observe(
        status: Status, want: Value | None, got: Value | None, detail: str = ""
    ) -> Observation:
        test = rec.test or frame.f_code.co_qualname
        return Observation(rec.run, test, locate(frame, rec.root), status, want, got, detail)

    def save(observation: Observation) -> str:
        try:
            rec.record(observation)
        except OSError as error:
            return f"xpt could not record this failure: {error}"
        return ""

    try:
        want = None if expected is _MISSING else serialize(expected)
        got = serialize(actual)
    except UnsupportedValue as error:
        if rec.enabled and (note := save(observe("unsupported", None, None, str(error)))):
            error.add_note(note)
        raise
    if want is None:
        status, message = "missing", report.missing(got)
    else:
        status, message = "mismatch", report.mismatch(diff(want, got))
    observation = observe(status, want, got)
    note = save(observation)
    raise ExpectationFailed(f"{message}\n{note}" if note else message, observation)
