from __future__ import annotations

from xpt.diff import Change, Diff
from xpt.path import format_path
from xpt.source import one_line
from xpt.value import Value

MAX_CHANGES = 10
MAX_CHARS = 80


def missing(actual: Value) -> str:
    return f"expect() has no expected value. Actual: {show(actual)}"


def mismatch(result: Diff) -> str:
    return "\n".join(["expect() mismatch:", *changes(result)])


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
    lines = [change(c, width) for c in shown]
    if hidden:
        lines.append(f"  ... {hidden} more changes")
    lines.append(f"  {len(result.changes)} changed, {result.unchanged} unchanged")
    return lines


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
