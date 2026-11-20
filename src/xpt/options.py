"""How one expect() call compares: a float tolerance, ignored paths, and list order."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from xpt.diff import Diff, diff, norm
from xpt.path import Attr, Index, Key, field_path, format_path
from xpt.path import Path as ValuePath
from xpt.serialize import serialize
from xpt.tolerance import EXACT, Tolerance, tolerance
from xpt.value import Dataclass, Dict, Int, Seq, Value, canonical


@dataclass(frozen=True, slots=True)
class Every:
    """`[...]` in an ignore path: every item of a list or tuple, or every value of a dict."""


Step = Attr | Key | Every
Pattern = tuple[Step, ...]

_KEEP_ORDER = ("numpy:", "torch:", "pandas:", "xpt:expect.digest")


@dataclass(frozen=True, slots=True)
class Options:
    """The keyword arguments of one expect() call that change how it compares.

    `ignore` holds parsed paths whose dict entries are removed from both values before they
    are compared or recorded. `unordered` sorts every list and tuple, so they compare as
    multisets and are written in one order.
    """

    tolerance: Tolerance = EXACT
    ignore: tuple[Pattern, ...] = ()
    unordered: bool = False

    @property
    def reshapes(self) -> bool:
        """Whether `prepare` can change a value, so `==` alone cannot decide a failure."""
        return bool(self.ignore) or self.unordered

    def prepare(self, value: Value) -> Value:
        for pattern in self.ignore:
            value = _strip(value, pattern, ())
        return _sorted(value) if self.unordered else value

    def diff(self, expected: Value, actual: Value) -> Diff:
        return diff(expected, actual, self.tolerance, unordered=self.unordered)

    def label(self) -> str:
        parts = [self.tolerance.label()] if self.tolerance else []
        if self.unordered:
            parts.append("unordered")
        if self.ignore:
            parts.append(f"ignore={', '.join(map(format_pattern, self.ignore))}")
        return ", ".join(parts)

    def fields(self) -> dict[str, object]:
        """The record fields that differ from an exact comparison."""
        found: dict[str, object] = dict(self.tolerance.fields())
        if self.ignore:
            found["ignore"] = [format_pattern(p) for p in self.ignore]
        if self.unordered:
            found["unordered"] = True
        return found


PLAIN = Options()


def options(atol: object, rtol: object, ignore: object, unordered: object) -> Options:
    """Parse expect()'s keyword arguments, raising with the argument's name when one is wrong."""
    if isinstance(ignore, str | bytes) or not isinstance(ignore, Iterable):
        raise TypeError(
            f"ignore must be a list of paths such as ['value[\"id\"]'], "
            f"got {type(ignore).__name__}."
        )
    if not isinstance(unordered, bool):
        raise TypeError(f"unordered must be True or False, got {type(unordered).__name__}.")
    return Options(tolerance(atol, rtol), tuple(map(parse_path, ignore)), unordered)


def parse_path(text: object) -> Pattern:
    """Parse a path the way a diff prints it, such as `value["items"][0]["id"]`."""
    if not isinstance(text, str):
        raise TypeError(f"each ignore path must be a str, got {type(text).__name__}.")
    try:
        node = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        raise ValueError(_unparsable(text)) from None
    steps: list[Step] = []
    while not isinstance(node, ast.Name):
        match node:
            case ast.Attribute(value=inner, attr=name):
                steps.append(Attr(name))
            case ast.Subscript(value=inner, slice=index):
                steps.append(_subscript(index, text))
            case _:
                raise ValueError(_unparsable(text))
        node = inner
    if node.id != "value":
        raise ValueError(_unparsable(text))
    if not steps:
        raise ValueError("ignore path 'value' would ignore the whole value. Name a key inside it.")
    return tuple(reversed(steps))


def format_pattern(pattern: Pattern) -> str:
    return "value" + "".join(
        "[...]" if isinstance(s, Every) else format_path((s,), "") for s in pattern
    )


def _subscript(node: ast.expr, text: str) -> Key | Every:
    try:
        key = ast.literal_eval(node)
    except ValueError:
        raise ValueError(_unparsable(text)) from None
    if key is Ellipsis:
        return Every()
    if key is None or type(key) in (str, int, float, bool):
        return Key(serialize(key))
    raise ValueError(_unparsable(text))


def _unparsable(text: str) -> str:
    return (
        f"ignore path {text!r} is not a path xpt can read. Write it the way a diff prints it, "
        "starting at value, such as 'value[\"created\"]', 'value.user[\"id\"]', or "
        '\'value["rows"][...]["id"]\' for every row.'
    )


def _strip(value: Value, pattern: Pattern, at: ValuePath) -> Value:
    step, rest = pattern[0], pattern[1:]
    match value:
        case Dict(items) if not isinstance(step, Attr):
            if not rest:
                return Dict(tuple((k, v) for k, v in items if not _hits(step, k)))
            return Dict(
                tuple(
                    (k, _strip(v, rest, (*at, Key(k))) if _hits(step, k) else v) for k, v in items
                )
            )
        case Seq(kind, items) if not isinstance(step, Attr):
            hits = [i for i in range(len(items)) if step == Every() or _index(step) == i]
            if hits and not rest:
                shown = format_path((*at, Index(hits[0])))
                raise ValueError(
                    f"ignore path {shown} names an item of a {kind}, and ignore removes only "
                    "dict entries. Pass unordered=True for order, or ignore a key inside the items."
                )
            return Seq(
                kind,
                tuple(
                    _strip(v, rest, (*at, Index(i))) if i in hits else v
                    for i, v in enumerate(items)
                ),
            )
        case Dataclass(type_id, fields):
            name = step.name if isinstance(step, Attr) else ""
            inner = rest if name else pattern
            at_field = field_path(at, name)
            if name and not rest and any(n == name for n, _ in fields):
                shown = format_path(at_field)
                raise ValueError(
                    f"ignore path {shown} names a field of {type_id.partition(':')[2]}, which "
                    "xpt must write to rebuild it. Ignore a dict key, or expect a dict of the "
                    "fields, such as dataclasses.asdict(value)."
                )
            return Dataclass(
                type_id,
                tuple((n, _strip(v, inner, at_field) if n == name else v) for n, v in fields),
            )
    return value


def _hits(step: Key | Every, key: Value) -> bool:
    return step == Every() or (isinstance(step, Key) and norm(step.key) == norm(key))


def _index(step: Key | Every) -> int | None:
    if isinstance(step, Key) and isinstance(step.key, Int):
        return step.key.v
    return None


def _sorted(value: Value) -> Value:
    match value:
        case Seq(kind, items):
            return Seq(kind, tuple(sorted(map(_sorted, items), key=canonical)))
        case Dict(items):
            return Dict(tuple((k, _sorted(v)) for k, v in items))
        case Dataclass(type_id, fields) if not type_id.startswith(_KEEP_ORDER):
            return Dataclass(type_id, tuple((n, _sorted(v)) for n, v in fields))
    return value
