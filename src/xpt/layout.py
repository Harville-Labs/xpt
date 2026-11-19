from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

from xpt.source import INDENT, display_width, to_source
from xpt.value import Value

WIDTH = 100


@dataclass(frozen=True, slots=True)
class Literal:
    """The expected argument to print, with its `expected=` label when it is a keyword."""

    label: str
    value: Value


Part = str | Literal


@dataclass(frozen=True, slots=True)
class Call:
    """A call to print, with the room its first and last lines share with other code.

    `head` is the callee's source text. `parts` are the arguments in order, each either
    source text kept as it is or the literal to print. `indent` is the leading whitespace
    of the call's first line. `before` and `after` are the display widths of the text on
    that line before the call and on its last line after it.
    """

    head: str
    parts: tuple[Part, ...]
    indent: str
    before: int
    after: int
    exploded: bool
    names: Mapping[str, str]


def layout(call: Call) -> str:
    """Print a call the way `ruff format` lays it out at the line width."""
    flat = [_flat(p, call.names) for p in call.parts]
    one = f"{call.head}({', '.join(flat)})"
    if not call.exploded and "\n" not in one:
        if call.before + display_width(one) + call.after <= WIDTH:
            return one
        body = ", ".join(flat)
        inner = call.indent + " " * INDENT
        if display_width(inner + body) <= WIDTH:
            return f"{call.head}(\n{inner}{body}\n{call.indent})"
    inner = call.indent + " " * INDENT
    lines = [f"{inner}{_exploded(p, len(inner), call.names)}," for p in call.parts]
    return "\n".join([f"{call.head}(", *lines, f"{call.indent})"])


def _flat(part: Part, names: Mapping[str, str]) -> str:
    if isinstance(part, str):
        return part
    return part.label + to_source(part.value, width=sys.maxsize, names=names)


def _exploded(part: Part, indent: int, names: Mapping[str, str]) -> str:
    if isinstance(part, str):
        return part
    # The trailing comma counts toward the width of the literal's first line.
    column = indent + display_width(part.label) + 1
    return part.label + to_source(part.value, indent, WIDTH, column=column, names=names)
