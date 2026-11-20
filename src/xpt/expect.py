from __future__ import annotations

import sys
from collections.abc import Iterable
from types import FrameType

from xpt import keyed, record, report
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
        key: object = _MISSING,
        _depth: int = 0,
    ) -> None:
        """Fail unless `actual == expected`, explaining a mismatch path by path.

        Arrays, tensors, and frames must also match in type, shape, and dtype. A float, or a
        float element, passes within `|actual - expected| <= atol + rtol * |expected|`.
        `ignore` lists dict entries to leave out of both sides, written the way a diff
        prints their path, such as `'value["id"]'` or `'value["rows"][...]["id"]'`.
        `unordered=True` compares every list and tuple as a multiset.

        With `key`, `expected` is a dict from each case's key to its expected value, so one
        call in a parametrized test holds every case: `expect(double(x), {1: 2}, key=x)`.

        Leaving out `expected` always fails, so a new expectation never passes unreviewed.
        A failure is recorded under `.xpt/` for review. A helper that calls `expect` passes
        `_depth=1` so the record points at the helper's caller.
        """
        __tracebackhide__ = True
        how = options(atol, rtol, ignore, unordered)
        want = expected
        if key is not _MISSING and expected is not _MISSING:
            want = keyed.check(expected).get(key, _MISSING)
        if want is not _MISSING and equal(actual, want, how.tolerance):
            return
        _fail(actual, expected, key, how, sys._getframe(1 + _depth))


expect = Expect()


def _fail(actual: object, expected: object, key: object, how: Options, frame: FrameType) -> None:
    __tracebackhide__ = True
    rec = record.recorder
    case: Value | None = None

    def observe(
        status: Status, want: Value | None, got: Value | None, detail: str = ""
    ) -> Observation:
        test = rec.test or frame.f_code.co_qualname
        site = locate(frame, rec.root)
        return Observation(rec.run, test, site, status, want, got, detail, how, case)

    def save(observation: Observation) -> str:
        try:
            rec.record(observation)
        except OSError as error:
            return f"xpt could not record this failure: {error}"
        return ""

    try:
        got = how.prepare(serialize(actual))
        if key is _MISSING:
            want = stored = None if expected is _MISSING else how.prepare(serialize(expected))
        else:
            case = serialize(key)
            stored = None if expected is _MISSING else keyed.cases(serialize(expected), how)
            want = keyed.entry(stored, case)
    except UnsupportedValue as error:
        if rec.enabled and (note := save(observe("unsupported", None, None, str(error)))):
            error.add_note(note)
        raise
    seen = got if case is None else keyed.with_entry(stored, case, got)
    if want is None:
        status, message = "missing", report.missing(got, case)
    elif not (found := how.diff(want, got)).changes and how.reshapes:
        return
    elif case is None:
        status, message = "mismatch", report.mismatch(found, how)
    else:
        one = how.diff(keyed.with_entry(None, case, want), keyed.with_entry(None, case, got))
        status, message = "mismatch", report.mismatch(one, how)
    observation = observe(status, stored, seen)
    note = save(observation)
    raise ExpectationFailed(f"{message}\n{note}" if note else message, observation)
