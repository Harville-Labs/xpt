from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from xpt.path import Attr, Index, Key, Path, format_path
from xpt.value import Bool, Dataclass, Dict, Float, Int, Null, Seq, Set, Str, Value

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


class UnsupportedValue(TypeError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"{format_path(path)} {reason}; xpt cannot record it. "
            "Register an adapter or expect a field of it instead."
        )


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
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _dataclass(obj, path, active)
    raise UnsupportedValue(path, f"is a {_type_name(exact)}")


def _dataclass(obj: DataclassInstance, path: Path, active: set[int]) -> Dataclass:
    cls = type(obj)
    params = getattr(cls, "__dataclass_params__", None)
    if params is not None and not params.eq:
        raise UnsupportedValue(path, f"is a {_type_name(cls)} with eq=False, so == is identity")
    fields = dataclasses.fields(obj)
    for f in fields:
        if not f.init or not f.compare:
            raise UnsupportedValue(
                path, f"is a {_type_name(cls)} whose field {f.name!r} has init or compare off"
            )
    return Dataclass(
        _type_name(cls),
        tuple(
            (f.name, _serialize(getattr(obj, f.name), (*path, Attr(f.name)), active))
            for f in fields
        ),
    )


def _type_name(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"
