"""Find the parts of a recorded value that change between runs when the source did not.

A call site that records different actual values in two stored runs, with the same expected
argument and the same project source, is nondeterministic: a timestamp, an id, a list built
from a set, or a race. Each path that differs is `Volatile`, with the option that would keep
it out of the comparison.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path as FilePath
from typing import Literal

from xpt.diff import norm
from xpt.group import Group, group
from xpt.literal import quote
from xpt.path import Attr, Index, Key, Path, field_path, format_path
from xpt.record import RecordError, load_run, run_ids, source_digest
from xpt.site import CallSite, SiteKey
from xpt.value import Bool, Dataclass, Dict, Float, Int, Null, Seq, Str, Value

Kind = Literal["timestamp", "value", "order", "float"]

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}|$)|\d{2}:\d{2}:\d{2}")
_NOT_IGNORABLE = "Make it deterministic in the test, because ignore= removes only dict entries."


@dataclass(frozen=True, slots=True)
class Volatile:
    """A path whose value differed between `runs`, which share the call site's expected
    argument and the project source. `values` counts the distinct values, `spread` is the
    largest difference between them for numbers."""

    path: Path
    kind: Kind
    values: int
    runs: tuple[str, ...]
    spread: float | None = None

    @property
    def suggestion(self) -> str | None:
        """The expect() keyword that would keep this path from failing, None when none can."""
        if self.kind == "order":
            return "unordered=True"
        if self.kind == "float":
            return None if self.spread is None else f"atol={_round_up(self.spread):g}"
        pattern = ignore_pattern(self.path)
        return None if pattern is None else f"ignore=[{quote(pattern)}]"

    @property
    def reason(self) -> str:
        where, runs = format_path(self.path), f"{len(self.runs)} runs with no source change"
        match self.kind:
            case "timestamp":
                said = f"{where} looks like a timestamp and had {self.values} values in {runs}."
            case "order":
                said = f"{where} had its items in {self.values} orders in {runs}."
            case "float" if self.spread is not None:
                apart = f"{self.spread:.3g}"
                said = f"{where} had {self.values} values up to {apart} apart in {runs}."
            case _:
                said = f"{where} had {self.values} values in {runs}."
        if suggestion := self.suggestion:
            return f"{said} Consider {suggestion}."
        if self.kind == "float":
            return f"{said} Make it deterministic in the test."
        return f"{said} {_NOT_IGNORABLE}"


@dataclass(frozen=True, slots=True)
class Seen:
    """One stored run's actual value at a call site."""

    run: str
    site: CallSite
    source: str
    value: Value


History = Mapping[SiteKey, Sequence[Seen]]


def load_history(root: FilePath) -> dict[SiteKey, list[Seen]]:
    """Every stored run's single actual value per call site, oldest run first.

    Runs with no source digest, from an older xpt or outside pytest, and runs that do not
    parse are left out, since nothing can say whether their source matched.
    """
    history: dict[SiteKey, list[Seen]] = defaultdict(list)
    for run in run_ids(root):
        source = source_digest(root, run)
        if source is None:
            continue
        try:
            groups = group(load_run(root, run))
        except RecordError:
            continue
        for g in groups:
            if g.acceptable and isinstance(g.site, CallSite) and len(g.actuals) == 1:
                history[g.site.key].append(Seen(run, g.site, source, g.actuals[0]))
    return dict(history)


def volatile(history: History, key: SiteKey, run: str) -> tuple[Volatile, ...]:
    """The paths of the site's value in `run` that differ in another stored run with the same
    expected argument and source.

    Every such run counts, before or after `run`, because run ids started in the same second
    do not sort by time.
    """
    seen = history.get(key, ())
    target = next((s for s in seen if s.run == run), None)
    if target is None:
        return ()
    same = [s for s in seen if s.site.arg_sha == target.site.arg_sha and s.source == target.source]
    return paths([s.value for s in same], tuple(s.run for s in same))


def find(history: History, run: str) -> dict[SiteKey, tuple[Volatile, ...]]:
    """Every call site of `run` with volatile paths."""
    return {key: found for key in history if (found := volatile(history, key, run))}


def mark(
    groups: Iterable[Group], found: Mapping[SiteKey, tuple[Volatile, ...]]
) -> tuple[Group, ...]:
    return tuple(
        replace(g, volatile=found.get(g.site.key, ())) if isinstance(g.site, CallSite) else g
        for g in groups
    )


def paths(values: Sequence[Value], runs: tuple[str, ...]) -> tuple[Volatile, ...]:
    """Each path where the values, one per run in order, differ, with its kind.

    A path that differs in more than one way is a value change. So is a path with changes
    inside it, which then stand for it alone: a list that was reordered in one run and
    changed an item in the next is one change, not an order and an item.
    """
    kinds: dict[str, tuple[Path, set[Kind]]] = {}
    for before, after in itertools.pairwise(values):
        for path, kind in _differ(before, after, ()):
            kinds.setdefault(format_path(path), (path, set()))[1].add(kind)
    every = [path for path, _ in kinds.values()]
    found = []
    for path, seen in kinds.values():
        if any(len(p) < len(path) and path[: len(p)] == p for p in every):
            continue
        inner = any(len(p) > len(path) and p[: len(path)] == path for p in every)
        kind: Kind = next(iter(seen)) if len(seen) == 1 and not inner else "value"
        parts = [_at(v, path) for v in values]
        distinct = len({None if p is None else ("value", norm(p)) for p in parts})
        spread = _spread(parts) if kind == "float" else None
        found.append(Volatile(path, kind, distinct, runs, spread))
    return tuple(found)


def suggest(found: Sequence[Volatile]) -> str:
    """One combined suggestion for every path, as expect() keywords, "" when none applies."""
    patterns = [
        p for v in found if v.kind in ("timestamp", "value") and (p := ignore_pattern(v.path))
    ]
    keywords = (
        [f"ignore=[{', '.join(quote(p) for p in dict.fromkeys(patterns))}]"] if patterns else []
    )
    if any(v.kind == "order" for v in found):
        keywords.append("unordered=True")
    spreads = [v.spread for v in found if v.kind == "float" and v.spread is not None]
    if spreads:
        keywords.append(f"atol={_round_up(max(spreads)):g}")
    return ", ".join(keywords)


def accept_reason(found: Sequence[Volatile]) -> str:
    """Why accept refuses a group with volatile paths, in the suspect guard's form."""
    where = ", ".join(format_path(v.path) for v in found)
    runs = max(len(v.runs) for v in found)
    said = f"{where} changed between {runs} runs with no source change (rule: flaky)."
    suggestion = suggest(found)
    fix = f"Consider {suggestion}" if suggestion else "Make the test deterministic"
    return f"{said} {fix}, or accept it with --force if it is right."


def ignore_pattern(path: Path) -> str | None:
    """The ignore= path for a dict entry, with list indexes widened to `[...]`, or None when
    the path does not end at a dict entry with a literal key."""
    if not path or not isinstance(path[-1], Key):
        return None
    text = "value"
    for step in path:
        match step:
            case Index():
                text += "[...]"
            case Key(Str() | Int() | Float() | Bool() | Null()):
                text += format_path((step,), "")
            case Attr():
                text += format_path((step,), "")
            case _:
                return None
    return text


def _differ(a: Value, b: Value, path: Path) -> Iterator[tuple[Path, Kind]]:
    if norm(a) == norm(b):
        return
    match a, b:
        case Seq(ak, ai), Seq(bk, bi) if ak == bk:
            if Counter(map(norm, ai)) == Counter(map(norm, bi)):
                yield path, "order"
            elif len(ai) == len(bi):
                for i, (x, y) in enumerate(zip(ai, bi, strict=True)):
                    yield from _differ(x, y, (*path, Index(i)))
            else:
                yield path, "value"
        case Dict(ai), Dict(bi):
            theirs = {norm(k): v for k, v in bi}
            mine = {norm(k) for k, _ in ai}
            for k, v in ai:
                if norm(k) in theirs:
                    yield from _differ(v, theirs[norm(k)], (*path, Key(k)))
                else:
                    yield (*path, Key(k)), "value"
            yield from (((*path, Key(k)), "value") for k, _ in bi if norm(k) not in mine)
        case Dataclass(at, af), Dataclass(bt, bf) if at == bt:
            theirs_fields = dict(bf)
            for name, v in af:
                other = theirs_fields.get(name)
                if other is None:
                    yield field_path(path, name), "value"
                else:
                    yield from _differ(v, other, field_path(path, name))
        case (Float() | Int(), Float() | Int()) if Float in (type(a), type(b)):
            yield path, "float"
        case Str(x), Str(y) if _TIMESTAMP.search(x) and _TIMESTAMP.search(y):
            yield path, "timestamp"
        case _:
            yield path, "value"


def _at(value: Value, path: Path) -> Value | None:
    """The part of `value` at `path`, None when it has none."""
    for step in path:
        if isinstance(value, Dataclass) and not isinstance(step, Attr):
            positional = dict(value.fields).get("")
            if positional is None:
                return None
            value = positional
        match step, value:
            case Index(i), Seq(_, items) if i < len(items):
                value = items[i]
            case Key(k), Dict(items):
                found = next((v for key, v in items if norm(key) == norm(k)), None)
                if found is None:
                    return None
                value = found
            case Attr(name), Dataclass(_, fields):
                found = dict(fields).get(name)
                if found is None:
                    return None
                value = found
            case _:
                return None
    return value


def _spread(parts: Sequence[Value | None]) -> float | None:
    numbers = [float(p.v) for p in parts if isinstance(p, Float | Int)]
    if len(numbers) < 2 or not all(math.isfinite(n) for n in numbers):
        return None
    gap = max(numbers) - min(numbers)
    return gap if gap > 0 else None


def _round_up(gap: float) -> float:
    """`gap` rounded up to one significant digit, and past it when it has only one, so a
    tolerance of it covers the spread despite float rounding."""
    scale = 10.0 ** math.floor(math.log10(gap))
    return math.ceil(gap / scale * (1 + 1e-9)) * scale
