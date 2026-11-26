from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from xpt import adapters, errors, native
from xpt.path import Attr, Index, Key, Path
from xpt.unsupported import UnsupportedValue, type_name
from xpt.value import Bool, Dataclass, Dict, Float, Int, Null, Seq, Set, Str, Value

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


__all__ = ["UnsupportedValue", "serialize"]


def serialize(obj: object) -> Value:
    return _serialize(obj, (), set())


def _serialize(obj: object, path: Path, active: set[int]) -> Value:
    exact = type(obj)
    match obj:
        case str() if exact is str:
            return Str(obj)
        case bool() if exact is bool:
            return Bool(obj)
        case int() if exact is int:
            return Int(obj)
        case float() if exact is float:
            return Float.of(obj)
        case complex() if exact is complex:
            return Dataclass(
                "builtins:complex", (("real", Float.of(obj.real)), ("imag", Float.of(obj.imag)))
            )
        case None:
            return Null()
    if id(obj) in active:
        raise UnsupportedValue(path, "contains itself")
    active.add(id(obj))
    try:
        return _container(obj, path, active)
    finally:
        active.discard(id(obj))


def _container(obj: object, path: Path, active: set[int]) -> Value:
    exact = type(obj)
    match obj:
        case list() | tuple() if exact in (list, tuple):
            items = tuple(_serialize(x, (*path, Index(i)), active) for i, x in enumerate(obj))
            return Seq("list" if exact is list else "tuple", items)
        case set() | frozenset() if exact in (set, frozenset):
            members = [_serialize(x, path, active) for x in obj]
            return Set.of("set" if exact is set else "frozenset", members)
        case dict() if exact is dict:
            pairs = []
            for k, v in obj.items():
                key = _serialize(k, path, active)
                pairs.append((key, _serialize(v, (*path, Key(key)), active)))
            return Dict(tuple(pairs))
    if adapter := adapters.find(exact):
        return adapter(obj, path, lambda o, p: _serialize(o, p, active))
    if (found := native.record(obj, path, lambda o, p: _serialize(o, p, active))) is not None:
        return found
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _dataclass(obj, path, active)
    if isinstance(obj, BaseException):
        return errors.record(obj, path, lambda o, p: _serialize(o, p, active))
    raise UnsupportedValue(path, f"is a {type_name(exact)}")


def _dataclass(obj: DataclassInstance, path: Path, active: set[int]) -> Dataclass:
    cls = type(obj)
    params = getattr(cls, "__dataclass_params__", None)
    if params is not None and not params.eq:
        raise UnsupportedValue(path, f"is a {type_name(cls)} with eq=False, so == is identity")
    fields = dataclasses.fields(obj)
    for f in fields:
        if not f.init or not f.compare:
            raise UnsupportedValue(
                path, f"is a {type_name(cls)} whose field {f.name!r} has init or compare off"
            )
    return Dataclass(
        type_name(cls),
        tuple(
            (f.name, _serialize(getattr(obj, f.name), (*path, Attr(f.name)), active))
            for f in fields
        ),
    )
