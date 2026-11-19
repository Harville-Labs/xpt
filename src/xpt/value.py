from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class Str:
    v: str


@dataclass(frozen=True, slots=True)
class Int:
    v: int


@dataclass(frozen=True, slots=True)
class Float:
    text: str

    @classmethod
    def of(cls, v: float) -> Float:
        return cls(repr(v))

    @property
    def v(self) -> float:
        return float(self.text)


@dataclass(frozen=True, slots=True)
class Bool:
    v: bool


@dataclass(frozen=True, slots=True)
class Null:
    pass


@dataclass(frozen=True, slots=True)
class Seq:
    kind: Literal["list", "tuple"]
    items: tuple[Value, ...]


@dataclass(frozen=True, slots=True)
class Set:
    kind: Literal["set", "frozenset"]
    items: tuple[Value, ...]

    @classmethod
    def of(cls, kind: Literal["set", "frozenset"], items: list[Value]) -> Set:
        return cls(kind, tuple(sorted(items, key=canonical)))


@dataclass(frozen=True, slots=True)
class Dict:
    items: tuple[tuple[Value, Value], ...]


@dataclass(frozen=True, slots=True)
class Dataclass:
    type: str
    fields: tuple[tuple[str, Value], ...]


Value = Str | Int | Float | Bool | Null | Seq | Set | Dict | Dataclass

Json = object

SPECIAL_FLOATS = ("nan", "inf", "-inf")


def to_json(value: Value) -> Json:
    match value:
        case Str(v) | Int(v) | Bool(v):
            return v
        case Float(text) if text in SPECIAL_FLOATS:
            return {"float": text}
        case Float(text):
            return float(text)
        case Null():
            return None
        case Seq("list", items):
            return [to_json(i) for i in items]
        case Seq(kind, items) | Set(kind, items):
            return {kind: [to_json(i) for i in items]}
        case Dict(items) if all(isinstance(k, Str) for k, _ in items):
            return {"dict": {cast(Str, k).v: to_json(v) for k, v in items}}
        case Dict(items):
            return {"dict": [[to_json(k), to_json(v)] for k, v in items]}
        case Dataclass(name, fields):
            return {name: {f: to_json(v) for f, v in fields}}


def canonical(value: Value) -> str:
    return json.dumps(to_json(value), sort_keys=True, separators=(",", ":"))


class InvalidValue(ValueError):
    pass


def from_json(data: object) -> Value:
    match data:
        case bool():
            return Bool(data)
        case int():
            return Int(data)
        case float() if math.isfinite(data):
            return Float(repr(data))
        case str():
            return Str(data)
        case None:
            return Null()
        case list():
            return Seq("list", _items(data))
        case dict() if len(data) == 1:
            [(tag, payload)] = data.items()
            return _tagged(tag, payload)
    raise InvalidValue(f"expected a JSON value or a one-key tagged object, got {data!r}")


def _tagged(tag: object, payload: object) -> Value:
    match tag, payload:
        case "tuple", list():
            return Seq("tuple", _items(payload))
        case "set", list():
            return Set.of("set", list(_items(payload)))
        case "frozenset", list():
            return Set.of("frozenset", list(_items(payload)))
        case "float", str() if payload in SPECIAL_FLOATS:
            return Float(payload)
        case "dict", dict():
            return Dict(tuple((Str(k), from_json(v)) for k, v in _fields(payload)))
        case "dict", list():
            return Dict(tuple((from_json(k), from_json(v)) for k, v in map(_pair, payload)))
        case str(), dict() if ":" in tag:
            return Dataclass(tag, tuple((k, from_json(v)) for k, v in _fields(payload)))
    raise InvalidValue(
        f"unknown tagged value {{{tag!r}: {payload!r}}}. Tags are tuple, set, frozenset, "
        'float, dict, or a dataclass type like "module:Qualname".'
    )


def _items(data: list[object]) -> tuple[Value, ...]:
    return tuple(from_json(i) for i in data)


def _fields(data: dict[object, object]) -> list[tuple[str, object]]:
    if not all(isinstance(k, str) for k in data):
        raise InvalidValue(f"object keys must be strings, got {list(data)!r}")
    return cast(list[tuple[str, object]], list(data.items()))


def _pair(data: object) -> tuple[object, object]:
    if not isinstance(data, list) or len(data) != 2:
        raise InvalidValue(f"expected a [key, value] pair, got {data!r}")
    return data[0], data[1]
