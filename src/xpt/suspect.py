from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from xpt.diff import norm
from xpt.path import Index, Key, Path, field_path, format_path
from xpt.value import Dataclass, Dict, Float, Int, Null, Seq, Set, Str, Value

Rule = Literal["exception", "none", "empty", "nan"]

_SAYS: dict[Rule, str] = {
    "exception": "looks like an exception or a traceback",
    "none": "is None where the expected value held data",
    "empty": "is empty where the expected value had items",
    "nan": "is nan where the expected value was a finite number",
}

_TRACEBACK = "Traceback (most recent call last):"
_EXCEPTION = re.compile(r"(?:[\w.]+\.)?(?:[A-Z]\w*)?(?:Error|Exception)(?:\(.*\)|: .*)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Suspicion:
    """Where an actual value looks like a failure rather than a result, and which rule says so."""

    path: Path
    rule: Rule

    @property
    def reason(self) -> str:
        return f"{format_path(self.path)} {_SAYS[self.rule]} (rule: {self.rule})."


def suspect(expected: Value | None, actual: Value) -> Suspicion | None:
    """The first part of `actual` that looks broken next to `expected`, None when nothing does.

    `expected` is None for a missing expectation, so only the exception rule can apply.
    """
    return next(_walk(expected, actual, ()), None)


def _walk(e: Value | None, a: Value, path: Path) -> Iterator[Suspicion]:
    rule = _rule(e, a)
    if rule is not None:
        yield Suspicion(path, rule)
        return
    for at, ec, ac in _children(e, a, path):
        yield from _walk(ec, ac, at)


def _rule(e: Value | None, a: Value) -> Rule | None:
    match a:
        case Str(text) if _exceptional(text) and not (isinstance(e, Str) and _exceptional(e.v)):
            return "exception"
        case Null() if e is not None and not isinstance(e, Null):
            return "none"
        case Seq(_, ()) | Set(_, ()) | Dict(()) if _filled(e):
            return "empty"
        case Float("nan") if isinstance(e, Int) or (isinstance(e, Float) and math.isfinite(e.v)):
            return "nan"
    return None


def _exceptional(text: str) -> bool:
    return _TRACEBACK in text or _EXCEPTION.fullmatch(text.strip()) is not None


def _filled(e: Value | None) -> bool:
    return isinstance(e, Seq | Set | Dict) and len(e.items) > 0


Child = tuple[Path, Value | None, Value]


def _children(e: Value | None, a: Value, path: Path) -> Iterator[Child]:
    """Each part of `a` with the part of `e` at the same place, or None when `e` has none."""
    match e, a:
        case Seq(ek, ei), Seq(ak, ai) if ek == ak:
            for i, v in enumerate(ai):
                yield (*path, Index(i)), ei[i] if i < len(ei) else None, v
        case Dict(ei), Dict(ai):
            theirs = {norm(k): v for k, v in ei}
            for k, v in ai:
                yield (*path, Key(k)), theirs.get(norm(k)), v
        case Dataclass(et, ef), Dataclass(at, af) if et == at:
            theirs_fields = dict(ef)
            for name, v in af:
                yield field_path(path, name), theirs_fields.get(name), v
        case _, Seq(_, ai):
            for i, v in enumerate(ai):
                yield (*path, Index(i)), None, v
        case _, Set(_, ai):
            for v in ai:
                yield path, None, v
        case _, Dict(ai):
            for k, v in ai:
                yield (*path, Key(k)), None, v
        case _, Dataclass(_, af):
            for name, v in af:
                yield field_path(path, name), None, v
