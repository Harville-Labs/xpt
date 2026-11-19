from __future__ import annotations

from dataclasses import dataclass

from xpt.value import Bool, Float, Int, Null, Str, Value


@dataclass(frozen=True, slots=True)
class Attr:
    name: str


@dataclass(frozen=True, slots=True)
class Index:
    i: int


@dataclass(frozen=True, slots=True)
class Key:
    key: Value


Step = Attr | Index | Key
Path = tuple[Step, ...]


def format_path(path: Path, root: str = "value") -> str:
    return root + "".join(_format_step(s) for s in path)


def _format_step(step: Step) -> str:
    match step:
        case Attr(name):
            return f".{name}"
        case Index(i):
            return f"[{i}]"
        case Key(Str(v) | Int(v) | Bool(v)):
            return f"[{v!r}]"
        case Key(Float(text)):
            return f"[{text}]"
        case Key(Null()):
            return "[None]"
        case Key(key):
            return f"[<{type(key).__name__.lower()} key>]"
