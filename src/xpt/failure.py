from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from xpt.record import Observation, from_record, to_record
from xpt.value import Json, Value, from_json, to_json

STREAMS = ("stdout", "stderr", "log")


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
class Where:
    """Where a test failed: the deepest project frame and what went wrong there.

    `function` is None when the frame is the test function itself.
    """

    path: str
    line: int | None
    function: str | None
    detail: Detail


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
    return {"path": w.path, "line": w.line, "function": w.function, "detail": detail_json(w.detail)}


def where_from_json(data: object) -> Where:
    record = _object(data, "the xpt report payload")
    line = record.get("line")
    function = record.get("function")
    if line is not None and not isinstance(line, int):
        raise ValueError(f"line must be an integer or null, got {line!r}.")
    if function is not None and not isinstance(function, str):
        raise ValueError(f"function must be a string or null, got {function!r}.")
    return Where(_str(record, "path"), line, function, detail_from_json(record.get("detail")))


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


def _str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got {value!r}.")
    return value
