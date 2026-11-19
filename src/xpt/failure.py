from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeVar, cast

from xpt.record import Observation, from_record, to_record
from xpt.value import Json, Value, from_json, to_json

STREAMS = ("stdout", "stderr", "log")

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Expected:
    observation: Observation


@dataclass(frozen=True, slots=True)
class Compared:
    left: Value
    right: Value


@dataclass(frozen=True, slots=True)
class Raised:
    type: str
    message: str


@dataclass(frozen=True, slots=True)
class Text:
    text: str


Detail = Expected | Compared | Raised | Text


@dataclass(frozen=True, slots=True)
class Frame:
    path: str
    line: int
    function: str | None


@dataclass(frozen=True, slots=True)
class Where:
    """Where a test failed: the deepest project frame and what went wrong there.

    `callers` are the project frames above it, outermost first, and `source` is its
    stripped source line. A `function` here or in a caller is None for the test itself.
    """

    path: str
    line: int | None
    function: str | None
    detail: Detail
    callers: tuple[Frame, ...] = ()
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed test phase, or one collection error when `when` is "collect"."""

    test: str
    when: str
    where: Where
    output: tuple[tuple[str, str], ...]
    traceback: str


def streams(sections: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Join pytest's "Captured <stream> <phase>" sections into one text per stream."""
    joined: dict[str, str] = {}
    for title, text in sections:
        words = title.split()
        if len(words) == 3 and words[0] == "Captured" and words[1] in STREAMS:
            joined[words[1]] = joined.get(words[1], "") + text
    return tuple((s, joined[s]) for s in STREAMS if joined.get(s, "").strip())


def where_json(w: Where) -> dict[str, Json]:
    return {
        "path": w.path,
        "line": w.line,
        "function": w.function,
        "callers": [{"path": f.path, "line": f.line, "function": f.function} for f in w.callers],
        "source": w.source,
        "detail": detail_json(w.detail),
    }


def where_from_json(data: object) -> Where:
    record = _object(data, "the xpt report payload")
    line = _optional(record, "line", int)
    function = _optional(record, "function", str)
    callers = record.get("callers")
    if not isinstance(callers, list):
        raise ValueError(f"callers must be a list, got {callers!r}.")
    return Where(
        _str(record, "path"),
        line,
        function,
        detail_from_json(record.get("detail")),
        tuple(_frame(c) for c in cast(list[object], callers)),
        _optional(record, "source", str),
    )


def _frame(data: object) -> Frame:
    record = _object(data, "a caller")
    line = record.get("line")
    if not isinstance(line, int):
        raise ValueError(f"line must be an integer, got {line!r}.")
    return Frame(_str(record, "path"), line, _optional(record, "function", str))


def detail_json(d: Detail) -> dict[str, Json]:
    match d:
        case Expected():
            return {"kind": "expect", "observation": to_record(d.observation)}
        case Compared():
            return {"kind": "compare", "left": to_json(d.left), "right": to_json(d.right)}
        case Raised():
            return {"kind": "raised", "type": d.type, "message": d.message}
        case Text():
            return {"kind": "text", "text": d.text}


def detail_from_json(data: object) -> Detail:
    record = _object(data, "detail")
    kind = record.get("kind")
    if kind == "expect":
        return Expected(from_record(record.get("observation")))
    if kind == "compare":
        return Compared(from_json(record.get("left")), from_json(record.get("right")))
    if kind == "raised":
        return Raised(_str(record, "type"), _str(record, "message"))
    if kind == "text":
        return Text(_str(record, "text"))
    raise ValueError(f'detail.kind must be "expect", "compare", "raised" or "text", got {kind!r}.')


def _object(data: object, name: str) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object, got {data!r}.")
    return cast(dict[str, object], data)


def _optional(data: dict[str, object], key: str, kind: type[T]) -> T | None:
    value = data.get(key)
    if value is not None and not isinstance(value, kind):
        raise ValueError(f"{key} must be {kind.__name__} or null, got {value!r}.")
    return value


def _str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got {value!r}.")
    return value
