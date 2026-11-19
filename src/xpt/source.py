from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from xpt.path import Attr, Index, Key, Path
from xpt.serialize import UnsupportedValue
from xpt.value import Bool, Dataclass, Dict, Float, Int, Null, Seq, Set, Str, Value

INDENT = 4


def to_source(
    value: Value,
    indent: int = 0,
    width: int = 100,
    *,
    column: int | None = None,
    names: Mapping[str, str] | None = None,
) -> str:
    """Print a value tree as a Python expression that `ruff format` leaves unchanged.

    `indent` is the indentation of the statement holding the expression, and `column`
    is where the expression starts, which defaults to `indent`. `names` maps a dataclass
    type ("module:Qualname") to the name that refers to it where the source is written.
    """
    if isinstance(value, Str) and _triple_quotable(value.v):
        return f'"""{value.v}"""'
    printer = _Printer(width, names or {})
    return printer.render(value, (), indent, indent if column is None else column, "", "")


@dataclass(frozen=True, slots=True)
class _Child:
    label: str
    value: Value
    path: Path
    key: Value | None = None


@dataclass(frozen=True, slots=True)
class _Group:
    opener: str
    children: tuple[_Child, ...]
    closer: str
    single_comma: bool = False


class _Printer:
    def __init__(self, width: int, names: Mapping[str, str]) -> None:
        self.width = width
        self.names = names

    def render(
        self, value: Value, path: Path, indent: int, column: int, prefix: str, suffix: str
    ) -> str:
        flat = prefix + self.flat(value, path) + suffix
        group = self.group(value, path)
        if group is None or not group.children or column + _width(flat) <= self.width:
            return flat
        inner = indent + INDENT
        pad = " " * inner
        comma = "" if len(group.children) == 1 and not group.single_comma else ","
        body = [pad + self.child(c, path, inner, comma) for c in group.children]
        return "\n".join([prefix + group.opener, *body, " " * indent + group.closer + suffix])

    def child(self, c: _Child, parent: Path, indent: int, suffix: str) -> str:
        if c.key is not None and self.splits(c.key, parent) and not self.head_fits(c, indent):
            tail = ": " + self.flat(c.value, c.path) + suffix
            key = self.render(c.key, parent, indent, indent, "", tail)
            if "\n" in key:
                head, _, last = key[: -len(tail)].rpartition("\n")
                value = self.render(c.value, c.path, indent, _width(last), ": ", suffix)
                return head + "\n" + last + value
            return key
        return self.render(c.value, c.path, indent, indent, c.label, suffix)

    def head_fits(self, c: _Child, indent: int) -> bool:
        group = self.group(c.value, c.path)
        if group is None or not group.children:
            return False
        return indent + _width(c.label + group.opener) <= self.width

    def splits(self, value: Value, path: Path) -> bool:
        group = self.group(value, path)
        return group is not None and bool(group.children)

    def flat(self, value: Value, path: Path) -> str:
        group = self.group(value, path)
        if group is None:
            return _atom(value)
        parts = [c.label + self.flat(c.value, c.path) for c in group.children]
        comma = "," if group.single_comma else ""
        return group.opener + ", ".join(parts) + comma + group.closer

    def group(self, value: Value, path: Path) -> _Group | None:
        match value:
            case Seq("list", items):
                return _Group("[", _indexed(items, path), "]")
            case Seq("tuple", items):
                return _Group("(", _indexed(items, path), ")", single_comma=len(items) == 1)
            case Set(_, ()):
                return None
            case Set("set", items):
                return _Group("{", tuple(_Child("", v, path) for v in items), "}")
            case Set("frozenset", items):
                return _Group("frozenset(", (_Child("", Set("set", items), path),), ")")
            case Dict(items):
                labelled = tuple(
                    _Child(self.flat(k, path) + ": ", v, (*path, Key(k)), k) for k, v in items
                )
                return _Group("{", labelled, "}")
            case Dataclass(type_id, fields):
                name = self.names.get(type_id)
                if name is None:
                    raise UnsupportedValue(path, f"is a {type_id} that is not importable by name")
                labelled = tuple(_Child(f"{f}=", v, (*path, Attr(f))) for f, v in fields)
                return _Group(f"{name}(", labelled, ")")
        return None


def _indexed(items: tuple[Value, ...], path: Path) -> tuple[_Child, ...]:
    return tuple(_Child("", v, (*path, Index(i))) for i, v in enumerate(items))


def _atom(value: Value) -> str:
    match value:
        case Str(v):
            return quote(v)
        case Int(v) | Bool(v):
            return repr(v)
        case Float(text) if text in ("nan", "inf", "-inf"):
            return f'float("{text}")'
        case Float(text):
            return text.replace("e+", "e")
        case Null():
            return "None"
        case Set("set", ()):
            return "set()"
        case Set("frozenset", ()):
            return "frozenset()"
    raise AssertionError(f"not an atom: {value!r}")


def quote(s: str) -> str:
    q = "'" if s.count('"') > s.count("'") else '"'
    return q + "".join(_escape(c, q) for c in s) + q


def _escape(c: str, q: str) -> str:
    if c == "\\" or c == q:
        return "\\" + c
    if c.isprintable() or c == " ":
        return c
    return repr(c)[1:-1]


def _triple_quotable(s: str) -> bool:
    return (
        "\n" in s
        and '"""' not in s
        and not s.endswith('"')
        and all((c.isprintable() or c in " \n") and c != "\\" for c in s)
    )


def _width(s: str) -> int:
    return sum(_char_width(c) for c in s)


def _char_width(c: str) -> int:
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
