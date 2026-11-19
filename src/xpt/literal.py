from __future__ import annotations

from xpt.value import SPECIAL_FLOATS


def float_literal(text: str) -> str:
    if text in SPECIAL_FLOATS:
        return f'float("{text}")'
    return text.replace("e+", "e")


def quote(s: str) -> str:
    q = "'" if s.count('"') > s.count("'") else '"'
    return q + "".join(_escape(c, q) for c in s) + q


def _escape(c: str, q: str) -> str:
    if c == "\\" or c == q:
        return "\\" + c
    if c.isprintable() or c == " ":
        return c
    return repr(c)[1:-1]
