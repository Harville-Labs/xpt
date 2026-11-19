from __future__ import annotations

import json
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

Json = dict[str, object]


def to_json(value: Value) -> Json:
    match value:
        case Str(v) | Int(v) | Bool(v):
            return {"t": type(v).__name__, "v": v}
        case Float(text):
            return {"t": "float", "v": text}
        case Null():
            return {"t": "none"}
        case Seq(kind, items) | Set(kind, items):
            return {"t": kind, "items": [to_json(i) for i in items]}
        case Dict(items):
            return {"t": "dict", "items": [[to_json(k), to_json(v)] for k, v in items]}
        case Dataclass(name, fields):
            return {"t": "dataclass", "type": name, "fields": [[f, to_json(v)] for f, v in fields]}


def canonical(value: Value) -> str:
    return json.dumps(to_json(value), sort_keys=True, separators=(",", ":"))


class InvalidValue(ValueError):
    pass


def from_json(data: object) -> Value:
    obj = _object(data)
    t = obj.get("t")
    match t:
        case "str":
            return Str(_str(obj, "v"))
        case "int":
            v = obj.get("v")
            if type(v) is not int:
                raise InvalidValue(f"int value needs an integer 'v', got {v!r}")
            return Int(v)
        case "bool":
            v = obj.get("v")
            if type(v) is not bool:
                raise InvalidValue(f"bool value needs a boolean 'v', got {v!r}")
            return Bool(v)
        case "float":
            return Float(_float_text(_str(obj, "v")))
        case "none":
            return Null()
        case "list" | "tuple":
            return Seq(
                "list" if t == "list" else "tuple", tuple(from_json(i) for i in _list(obj, "items"))
            )
        case "set" | "frozenset":
            items = [from_json(i) for i in _list(obj, "items")]
            return Set.of("set" if t == "set" else "frozenset", items)
        case "dict":
            pairs = (_pair(p) for p in _list(obj, "items"))
            return Dict(tuple((from_json(k), from_json(v)) for k, v in pairs))
        case "dataclass":
            pairs = (_pair(p) for p in _list(obj, "fields"))
            return Dataclass(_str(obj, "type"), tuple((_name(k), from_json(v)) for k, v in pairs))
    raise InvalidValue(f"unknown value type {t!r}")


def _object(data: object) -> Json:
    if not isinstance(data, dict):
        raise InvalidValue(f"expected a value object, got {type(data).__name__}")
    return cast(Json, data)


def _str(obj: Json, key: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str):
        raise InvalidValue(f"{obj.get('t')} value needs a string {key!r}, got {v!r}")
    return v


def _list(obj: Json, key: str) -> list[object]:
    v = obj.get(key)
    if not isinstance(v, list):
        raise InvalidValue(f"{obj.get('t')} value needs a list {key!r}, got {v!r}")
    return cast(list[object], v)


def _float_text(text: str) -> str:
    try:
        parsed = float(text)
    except ValueError:
        parsed = None
    if parsed is None or repr(parsed) != text:
        raise InvalidValue(f"float value must be a Python float repr like '1.5', got {text!r}")
    return text


def _name(data: object) -> str:
    if not isinstance(data, str):
        raise InvalidValue(f"dataclass field name must be a string, got {data!r}")
    return data


def _pair(data: object) -> tuple[object, object]:
    if not isinstance(data, list) or len(data) != 2:
        raise InvalidValue(f"expected a [key, value] pair, got {data!r}")
    return data[0], data[1]
