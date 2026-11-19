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
    shown = result.changes[:MAX_CHANGES]
    hidden = len(result.changes) - len(shown)
    lines = ["expect() mismatch:", *(_change(c) for c in shown)]
    if hidden:
        lines.append(f"  ... {hidden} more changes")
    lines.append(f"  {len(result.changes)} changed, {result.unchanged} unchanged")
    return "\n".join(lines)


def _change(c: Change) -> str:
    expected, actual = c.expected, c.actual
    if expected is not None and actual is not None:
        text = f"{show(expected)} -> {show(actual)}"
    elif expected is not None:
        text = f"removed {show(expected)}"
    elif actual is not None:
        text = f"added {show(actual)}"
    else:
        raise AssertionError(f"a change needs at least one side: {c!r}")
    return f"  {format_path(c.path)}: {text}"


def show(value: Value) -> str:
    text = one_line(value)
    if len(text) <= MAX_CHARS:
        return text
    return f"{text[:MAX_CHARS]}... ({len(text)} chars)"
