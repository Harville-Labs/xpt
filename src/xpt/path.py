from __future__ import annotations

from dataclasses import dataclass

from xpt.literal import float_literal, quote
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


POSITIONAL = "*"
"""The field of a constructor call that holds its positional arguments, as a tuple.

An exception's arguments are recorded this way, and written as `ValueError("bad")`. The
field's path step is `.args`, the attribute that holds them.
"""


def field_name(name: str) -> str:
    """The attribute a field's path step names: `args` for the positional arguments."""
    return "args" if name == POSITIONAL else name


def field_path(path: Path, name: str) -> Path:
    """The path to a field; the positional field "" of a constructor call adds no step."""
    return (*path, Attr(field_name(name))) if name else path


def format_path(path: Path, root: str = "value") -> str:
    return root + "".join(_format_step(s) for s in path)


def _format_step(step: Step) -> str:
    match step:
        case Attr(name):
            return f".{name}"
        case Index(i):
            return f"[{i}]"
        case Key(Str(v)):
            return f"[{quote(v)}]"
        case Key(Int(v) | Bool(v)):
            return f"[{v!r}]"
        case Key(Float(text)):
            return f"[{float_literal(text)}]"
        case Key(Null()):
            return "[None]"
        case Key(key):
            return f"[<{type(key).__name__.lower()} key>]"
