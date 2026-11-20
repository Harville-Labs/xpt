from __future__ import annotations

from xpt.diff import ArrayCount, Change, Diff
from xpt.options import PLAIN, Options
from xpt.path import format_path
from xpt.source import one_line
from xpt.value import Value

MAX_CHANGES = 10
MAX_CHARS = 80


def missing(actual: Value, key: Value | None = None) -> str:
    case = "" if key is None else f" for key={show(key)}"
    return f"expect() has no expected value{case}. Actual: {show(actual)}"


def mismatch(result: Diff, how: Options = PLAIN) -> str:
    header = f"expect() mismatch ({label}):" if (label := how.label()) else "expect() mismatch:"
    return "\n".join([header, *changes(result)])


def changes(
    result: Diff,
    *,
    limit: int | None = MAX_CHANGES,
    width: int | None = MAX_CHARS,
    labeled: bool = False,
) -> list[str]:
    """The diff's changes as indented lines, cut to `limit` changes; None lifts a limit.

    `labeled` names the sides left and right, for a diff of `assert left == right`.
    """
    shown = result.changes if limit is None else result.changes[:limit]
    hidden = len(result.changes) - len(shown)
    change = _sides if labeled else _change
    lines = [*map(_count, result.arrays), *(change(c, width) for c in shown)]
    if hidden:
        lines.append(f"  ... {hidden} more changes")
    lines.append(f"  {len(result.changes)} changed, {result.unchanged} unchanged")
    return lines


def _count(a: ArrayCount) -> str:
    line = f"  {format_path(a.path)}: {a.differ} of {a.total} elements differ"
    if a.largest is None or a.at is None:
        return line
    return f"{line}, the largest by {a.largest:g} at {format_path(a.at)}"


def _change(c: Change, width: int | None) -> str:
    expected, actual = c.expected, c.actual
    if expected is not None and actual is not None:
        text = f"{show(expected, width)} -> {show(actual, width)}"
    elif expected is not None:
        text = f"removed {show(expected, width)}"
    elif actual is not None:
        text = f"added {show(actual, width)}"
    else:
        raise AssertionError(f"a change needs at least one side: {c!r}")
    return f"  {format_path(c.path)}: {text}"


def _sides(c: Change, width: int | None) -> str:
    left = "missing" if c.expected is None else show(c.expected, width)
    right = "missing" if c.actual is None else show(c.actual, width)
    return f"  {format_path(c.path)}: left {left}, right {right}"


def show(value: Value, width: int | None = MAX_CHARS) -> str:
    return cut(one_line(value), width)


def cut(text: str, width: int | None) -> str:
    if width is None or len(text) <= width:
        return text
    return f"{text[:width]}... ({len(text)} chars)"
