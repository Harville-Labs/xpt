from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from types import TracebackType
from typing import NoReturn, overload

from xpt import errors, keyed, record, report
from xpt.digest import Digest
from xpt.equal import equal
from xpt.extensions import with_extensions
from xpt.options import Options, options
from xpt.output import output
from xpt.record import Observation, Status
from xpt.serialize import UnsupportedValue, serialize
from xpt.site import Frame, WithItem, called_text, locate
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

    @overload
    def raises(
        self,
        fn: Callable[[], object],
        expected: object = ...,
        *,
        subclass: bool = False,
        ignore: Iterable[str] = (),
        _depth: int = 0,
    ) -> None: ...

    @overload
    def raises(
        self,
        expected: BaseException = ...,
        /,
        *,
        subclass: bool = False,
        ignore: Iterable[str] = (),
        _depth: int = 0,
    ) -> Raises: ...

    def raises(
        self,
        fn: object = _MISSING,
        expected: object = _MISSING,
        *,
        subclass: bool = False,
        ignore: Iterable[str] = (),
        _depth: int = 0,
    ) -> Raises | None:
        """Fail unless `fn()`, or the `with` block, raises an exception equal to `expected`.

        `expect.raises(fn, expected)` calls `fn`. `with expect.raises(expected):` runs the
        block instead, and the expected exception is the first argument.

        Exceptions are equal when they have the same type, `args`, and `__notes__`. With
        `subclass=True`, a subclass of `expected`'s type matches too. `ignore` works as it
        does for `expect()`, on dict entries inside the exception's args.

        Leaving out `expected` always fails and records what was raised, so `xpt accept` can
        write it as a constructor call such as `ValueError("bad")`. A call that returns, or a
        block that finishes, instead fails. `KeyboardInterrupt`, `GeneratorExit`, and other
        `BaseException`s outside `Exception` and `SystemExit` pass through unless `expected`
        is one, and a failed `expect()` inside always does.
        """
        __tracebackhide__ = True
        how = options(0.0, 0.0, ignore, False)
        if not isinstance(subclass, bool):
            raise TypeError(f"subclass must be True or False, got {type(subclass).__name__}.")
        block = fn is _MISSING or isinstance(fn, BaseException)
        if block and fn is not _MISSING:
            if expected is not _MISSING:
                raise TypeError(
                    "with expect.raises() takes the expected exception once, as its first argument."
                )
            expected = fn
        _check_expected(fn if _is_class(fn) else expected)
        frame = sys._getframe(1 + _depth)
        if block:
            return Raises(expected, subclass, how, WithItem.of(frame))
        if not callable(fn):
            raise TypeError(
                "expect.raises() takes a function to call first, such as "
                f"lambda: parse('x'), or no function in a with statement, got "
                f"{type(fn).__name__}."
            )
        raised, caught = _call(fn, expected)
        if not raised:
            _returned(caught, expected, how, frame)
        _compare(caught, expected, subclass, how, frame)
        return None


class Raises:
    """`with expect.raises(expected):`, which compares what the block raised on exit."""

    def __init__(self, expected: object, subclass: bool, how: Options, where: WithItem) -> None:
        self._expected = expected
        self._subclass = subclass
        self._how = how
        self._where = where

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        __tracebackhide__ = True
        expected, how, where = self._expected, self._how, self._where
        if error is None:
            _finished(expected, how, where)
        if not _catches(error, expected):
            return False
        try:
            _compare(error, expected, self._subclass, how, where)
        except ExpectationFailed as failed:
            raise failed from None
        return True


expect = Expect()


def _is_class(value: object) -> bool:
    return isinstance(value, type) and issubclass(value, BaseException)


def _check_expected(expected: object) -> None:
    """Refuse an expected value that is not an exception instance, with how to write one."""
    if expected is _MISSING or isinstance(expected, BaseException):
        return
    hint = (
        f"{expected.__name__}('message')" if isinstance(expected, type) else "ValueError('message')"
    )
    raise TypeError(
        f"expect.raises() expects an exception instance such as {hint}, "
        f"got {expected!r}. Leave it out to record the exception and accept it."
    )


def _call(fn: Callable[[], object], expected: object) -> tuple[bool, object]:
    """Whether `fn` raised, and what it raised or returned."""
    try:
        return False, fn()
    except BaseException as error:
        if _catches(error, expected):
            return True, error
        raise


def _catches(error: BaseException, expected: object) -> bool:
    """Whether expect.raises() compares `error` rather than letting it pass through."""
    if isinstance(error, ExpectationFailed):
        return False
    wanted = isinstance(expected, BaseException) and isinstance(error, type(expected))
    return wanted or isinstance(error, Exception | SystemExit)


def _compare(caught: object, expected: object, subclass: bool, how: Options, frame: Frame) -> None:
    """Return when `caught` equals `expected`, and fail with the difference otherwise."""
    __tracebackhide__ = True
    if isinstance(caught, BaseException) and isinstance(expected, BaseException):
        kind = type(expected)
        if subclass and type(caught) is not kind and isinstance(caught, kind):
            caught = errors.retyped(caught, kind)
        if equal(caught, expected):
            return
    _fail(caught, expected, _MISSING, how, frame, report.RAISES)


def _observe(
    how: Options,
    frame: Frame,
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
    frame: Frame,
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


def _returned(value: object, expected: object, how: Options, frame: Frame) -> NoReturn:
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
    _not_raised(message, want, how, frame, got)


def _finished(expected: object, how: Options, where: WithItem) -> NoReturn:
    """Fail a `with expect.raises():` block that finished without raising."""
    __tracebackhide__ = True
    want = None if expected is _MISSING else how.prepare(serialize(expected))
    wanted = "" if want is None else f", expected {report.show(want)}"
    message = f"{report.RAISES}: the with block finished without raising{wanted}."
    _not_raised(message, want, how, where, None)


def _not_raised(
    message: str, want: Value | None, how: Options, frame: Frame, got: Value | None = None
) -> NoReturn:
    __tracebackhide__ = True
    observation = _observe(how, frame, "returned", want, got, message)
    note = _save(observation)
    raise ExpectationFailed(f"{message}\n{note}" if note else message, observation)
