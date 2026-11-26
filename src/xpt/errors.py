"""Exceptions as values: what xpt records of one, how it compares, and how it is rebuilt.

An exception is its exact type, its `args`, and its `__notes__`. The traceback, the cause,
and any other attribute are left out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from xpt.diff import norm
from xpt.path import POSITIONAL, Attr, Path
from xpt.unsupported import UnsupportedValue, type_name
from xpt.value import Dataclass, Ref, Seq, Value

HELPER = "xpt:expect.exception"

Recurse = Callable[[object, Path], Value]

E = TypeVar("E", bound=BaseException)


def build(kind: type[E], /, *args: object, notes: Iterable[str] = ()) -> E:
    """What `expect.exception(kind, *args, notes=[...])` writes: a `kind` holding `args`.

    It is made without calling `kind.__init__`, so a class whose constructor takes other
    arguments than the ones it stores in `args` can still be rebuilt and compared.
    """
    if not (isinstance(kind, type) and issubclass(kind, BaseException)):
        raise TypeError(f"expect.exception() takes an exception class first, got {kind!r}.")
    error = kind.__new__(kind, *args)
    error.args = args
    if noted := list(notes):
        error.__notes__ = noted
    return error


def plain(cls: type) -> bool:
    """Whether instances of `cls` compare by identity, so xpt compares them by value instead."""
    return cls.__eq__ is object.__eq__


def notes(error: BaseException) -> object:
    return getattr(error, "__notes__", None) or []


def record(error: BaseException, path: Path, recurse: Recurse) -> Value:
    """`ValueError("bad")` as a constructor call, or through `expect.exception` when calling
    the class with its args would not rebuild it, or when it has notes."""
    cls = type(error)
    args = recurse(error.args, (*path, Attr("args")))
    noted = getattr(error, "__notes__", None)
    if noted is not None and not isinstance(noted, list):
        raise UnsupportedValue(path, f"is a {type_name(cls)} whose __notes__ is not a list")
    positional = ((POSITIONAL, args),) if isinstance(args, Seq) and args.items else ()
    if not noted and _rebuilds(error, args, recurse):
        return Dataclass(type_name(cls), positional)
    extra = (("notes", recurse(noted, (*path, Attr("notes")))),) if noted else ()
    return Dataclass(HELPER, (("", Ref(type_name(cls))), *positional, *extra))


def _rebuilds(error: BaseException, args: Value, recurse: Recurse) -> bool:
    cls = type(error)
    try:
        again = cls(*error.args)
    except Exception:
        return False
    return type(again) is cls and norm(recurse(again.args, ())) == norm(args)


def retyped(error: BaseException, cls: type[BaseException]) -> BaseException:
    """`error`'s args and notes in an instance of `cls`, one of its base classes, for
    `subclass=True`."""
    noted = notes(error)
    return build(cls, *error.args, notes=noted if isinstance(noted, list) else ())
