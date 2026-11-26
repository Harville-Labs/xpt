from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from types import FrameType
from typing import NoReturn

from xpt import errors, keyed, record, report
from xpt.digest import Digest
from xpt.equal import equal
from xpt.extensions import with_extensions
from xpt.options import Options, options
from xpt.output import output
from xpt.record import Observation, Status
from xpt.serialize import UnsupportedValue, serialize
from xpt.site import called_text, locate
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
    output = staticmethod(output)
    exception = staticmethod(errors.build)

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
        _note: str = "",
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
        `_depth=1` so the record points at the helper's caller, and may pass `_note`, a line
        that the failure message and the record add.
        """
        __tracebackhide__ = True
        how = options(atol, rtol, ignore, unordered)
        want = expected
        if key is not _MISSING and expected is not _MISSING:
            want = keyed.check(expected).get(key, _MISSING)
        if want is not _MISSING and equal(actual, want, how.tolerance):
            return
        _fail(actual, expected, key, how, sys._getframe(1 + _depth), note=_note)

    def raises(
        self,
        fn: Callable[[], object],
        expected: object = _MISSING,
        *,
        subclass: bool = False,
        ignore: Iterable[str] = (),
        _depth: int = 0,
    ) -> None:
        """Call `fn` and fail unless it raises an exception equal to `expected`.

        Exceptions are equal when they have the same type, `args`, and `__notes__`. With
        `subclass=True`, a subclass of `expected`'s type matches too. `ignore` works as it
        does for `expect()`, on dict entries inside the exception's args.

        Leaving out `expected` always fails and records what was raised, so `xpt accept` can
        write it as a constructor call such as `ValueError("bad")`. A call that returns
        instead fails with what it returned. `KeyboardInterrupt`, `GeneratorExit`, and other
        `BaseException`s outside `Exception` and `SystemExit` pass through unless `expected`
        is one, and a failed `expect()` inside `fn` always does.
        """
        __tracebackhide__ = True
        how = options(0.0, 0.0, ignore, False)
        if not isinstance(subclass, bool):
            raise TypeError(f"subclass must be True or False, got {type(subclass).__name__}.")
        if not callable(fn):
            raise TypeError(
                "expect.raises() takes a function to call first, such as "
                f"lambda: parse('x'), got {type(fn).__name__}."
            )
        if expected is not _MISSING and not isinstance(expected, BaseException):
            hint = (
                f"{expected.__name__}('message')"
                if isinstance(expected, type)
                else "ValueError('message')"
            )
            raise TypeError(
                f"expect.raises() expects an exception instance such as {hint}, "
                f"got {expected!r}. Leave it out to record the exception and accept it."
            )
        frame = sys._getframe(1 + _depth)
        raised, caught = _call(fn, expected)
        if not raised:
            _returned(caught, expected, how, frame)
        if isinstance(caught, BaseException) and isinstance(expected, BaseException):
            kind = type(expected)
            if subclass and type(caught) is not kind and isinstance(caught, kind):
                caught = errors.retyped(caught, kind)
            if equal(caught, expected):
                return
        _fail(caught, expected, _MISSING, how, frame, report.RAISES)


expect = Expect()


def _call(fn: Callable[[], object], expected: object) -> tuple[bool, object]:
    """Whether `fn` raised, and what it raised or returned."""
    try:
        return False, fn()
    except ExpectationFailed:
        raise
    except BaseException as error:
        wanted = isinstance(expected, BaseException) and isinstance(error, type(expected))
        if wanted or isinstance(error, Exception | SystemExit):
            return True, error
        raise


def _observe(
    how: Options,
    frame: FrameType,
    status: Status,
    want: Value | None,
    got: Value | None,
    detail: str = "",
    case: Value | None = None,
) -> Observation:
    rec = record.recorder
    test = rec.test or frame.f_code.co_qualname
    site = locate(frame, rec.root)
    return Observation(rec.run, test, site, status, want, got, detail, how, case)


def _save(observation: Observation) -> str:
    """Record `observation`, returning a line about the error when it cannot be written."""
    try:
        record.recorder.record(observation)
    except OSError as error:
        return f"xpt could not record this failure: {error}"
    return ""


def _fail(
    actual: object,
    expected: object,
    key: object,
    how: Options,
    frame: FrameType,
    call: str = "expect()",
    note: str = "",
) -> None:
    __tracebackhide__ = True
    rec = record.recorder
    case: Value | None = None
    try:
        got = how.prepare(serialize(actual))
        if key is _MISSING:
            want = stored = None if expected is _MISSING else how.prepare(serialize(expected))
        else:
            case = serialize(key)
            stored = None if expected is _MISSING else keyed.cases(serialize(expected), how)
            want = keyed.entry(stored, case)
    except UnsupportedValue as error:
        unsupported = _observe(how, frame, "unsupported", None, None, str(error), case)
        if rec.enabled and (note := _save(unsupported)):
            error.add_note(note)
        raise
    seen = got if case is None else keyed.with_entry(stored, case, got)
    if want is None:
        status, message = "missing", report.missing(got, case, call)
    elif not (found := how.diff(want, got)).changes and how.reshapes:
        return
    elif case is None:
        status, message = "mismatch", report.mismatch(found, how, call)
    else:
        one = how.diff(keyed.with_entry(None, case, want), keyed.with_entry(None, case, got))
        status, message = "mismatch", report.mismatch(one, how, call)
    if note:
        message = f"{message}\n  {note}"
    observation = _observe(how, frame, status, stored, seen, note, case)
    problem = _save(observation)
    raise ExpectationFailed(f"{message}\n{problem}" if problem else message, observation)


def _returned(value: object, expected: object, how: Options, frame: FrameType) -> NoReturn:
    """Fail an expect.raises() whose function returned `value`, recording what it returned."""
    __tracebackhide__ = True
    called = called_text(frame, record.recorder.root) or "the function"
    try:
        got: Value | None = serialize(value)
        shown = report.show(got)
    except UnsupportedValue as error:
        got, shown = None, f"a value xpt cannot record ({error.reason})"
    want = None if expected is _MISSING else how.prepare(serialize(expected))
    wanted = "" if want is None else f", expected {report.show(want)}"
    message = f"{report.RAISES}: {called} returned {shown} instead of raising{wanted}."
    observation = _observe(how, frame, "returned", want, got, message)
    note = _save(observation)
    raise ExpectationFailed(f"{message}\n{note}" if note else message, observation)
