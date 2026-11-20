from __future__ import annotations

import sys
from collections.abc import Iterable
from types import FrameType

from xpt import record, report
from xpt.digest import Digest
from xpt.equal import equal
from xpt.extensions import with_extensions
from xpt.options import Options, options
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


class Expect:
    """`expect` itself, with the helpers that recorded values call as `expect.<name>`."""

    digest = Digest
    with_extensions = staticmethod(with_extensions)

    def __call__(
        self,
        actual: object,
        expected: object = _MISSING,
        *,
        atol: float = 0.0,
        rtol: float = 0.0,
        ignore: Iterable[str] = (),
        unordered: bool = False,
        _depth: int = 0,
    ) -> None:
        """Fail unless `actual == expected`, explaining a mismatch path by path.

        Arrays, tensors, and frames must also match in type, shape, and dtype. A float, or a
        float element, passes within `|actual - expected| <= atol + rtol * |expected|`.
        `ignore` lists dict entries to leave out of both sides, written the way a diff
        prints their path, such as `'value["id"]'` or `'value["rows"][...]["id"]'`.
        `unordered=True` compares every list and tuple as a multiset.

        Leaving out `expected` always fails, so a new expectation never passes unreviewed.
        A failure is recorded under `.xpt/` for review. A helper that calls `expect` passes
        `_depth=1` so the record points at the helper's caller.
        """
        __tracebackhide__ = True
        how = options(atol, rtol, ignore, unordered)
        if expected is not _MISSING and equal(actual, expected, how.tolerance):
            return
        _fail(actual, expected, how, sys._getframe(1 + _depth))


expect = Expect()


def _fail(actual: object, expected: object, how: Options, frame: FrameType) -> None:
    __tracebackhide__ = True
    rec = record.recorder

    def observe(
        status: Status, want: Value | None, got: Value | None, detail: str = ""
    ) -> Observation:
        test = rec.test or frame.f_code.co_qualname
        site = locate(frame, rec.root)
        return Observation(rec.run, test, site, status, want, got, detail, how)

    def save(observation: Observation) -> str:
        try:
            rec.record(observation)
        except OSError as error:
            return f"xpt could not record this failure: {error}"
        return ""

    try:
        want = None if expected is _MISSING else how.prepare(serialize(expected))
        got = how.prepare(serialize(actual))
    except UnsupportedValue as error:
        if rec.enabled and (note := save(observe("unsupported", None, None, str(error)))):
            error.add_note(note)
        raise
    if want is None:
        status, message = "missing", report.missing(got)
    elif not (found := how.diff(want, got)).changes and how.reshapes:
        return
    else:
        status, message = "mismatch", report.mismatch(found, how)
    observation = observe(status, want, got)
    note = save(observation)
    raise ExpectationFailed(f"{message}\n{note}" if note else message, observation)
