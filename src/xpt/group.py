from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import Literal, TypeVar

from xpt.diff import norm
from xpt.record import Observation
from xpt.report import show
from xpt.site import CallSite, SiteKey, Unlocatable, short_ids
from xpt.tolerance import EXACT, Tolerance
from xpt.value import Value, canonical

GroupStatus = Literal["missing", "mismatch", "unsupported", "unlocatable", "conflict"]

ONE_EXPECT_PER_CASE = " Give each case its own expect()."

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Group:
    """Every failed observation of one call site in a run.

    `id` is the short display id, None for an unlocatable site. `actuals` holds the distinct
    actual values, one when the calls agree. `expected` is None when it was missing or when
    the calls disagree on it. `detail` says why a group is not acceptable. `rejected` means
    its one actual value was rejected as a bug with `xpt reject`.
    """

    id: str | None
    site: CallSite | Unlocatable
    status: GroupStatus
    actuals: tuple[Value, ...]
    expected: Value | None
    tests: tuple[str, ...]
    detail: str
    rejected: bool = False
    tolerance: Tolerance = EXACT

    @property
    def acceptable(self) -> bool:
        return self.status in ("missing", "mismatch")

    @property
    def offered(self) -> bool:
        """Whether to suggest accepting it: acceptable and not rejected."""
        return self.acceptable and not self.rejected


def missing(groups: Iterable[Group]) -> list[Group]:
    """The groups whose expected argument is empty and whose value was not rejected."""
    return [g for g in groups if g.status == "missing" and not g.rejected]


def group(observations: Iterable[Observation]) -> tuple[Group, ...]:
    """Group a run's observations by call site, in path, line, and ordinal order."""
    located: dict[SiteKey, list[tuple[CallSite, Observation]]] = defaultdict(list)
    lost: dict[Unlocatable, list[Observation]] = defaultdict(list)
    for o in observations:
        site = o.site
        if isinstance(site, Unlocatable):
            lost[site].append(o)
        else:
            located[site.key].append((site, o))
    ids = short_ids(site for seen in located.values() for site, _ in seen)
    groups = [_located(ids[key], seen) for key, seen in located.items()]
    groups += [_unlocatable(site, seen) for site, seen in lost.items()]
    return tuple(sorted(groups, key=_order))


def _located(id: str, seen: list[tuple[CallSite, Observation]]) -> Group:
    site = min((s for s, _ in seen), key=lambda s: (s.line, s.arg_sha))
    observations = [o for _, o in seen]
    actuals, expected = _values(observations)
    sources = _buckets([(s.arg_sha, o) for s, o in seen], str, str)
    values = tuple(v for v, _ in actuals)
    one_expected = expected[0][0] if len(expected) == 1 else None

    def made(status: GroupStatus, detail: str = "") -> Group:
        tests = _tests(observations)
        tol = observations[0].tolerance
        return Group(id, site, status, values, one_expected, tests, detail, tolerance=tol)

    if len(sources) > 1:
        shown = [(f"source {sha[:8]}" if sha else "no expected argument", t) for sha, t in sources]
        return made(
            "conflict",
            _disagree("the expected argument's source text", "had", shown)
            + " The file changed during the run. Run the tests again.",
        )
    if any(o.actual is None for o in observations):
        return made(
            "unsupported", " ".join(sorted({o.detail for o in observations if o.actual is None}))
        )
    if len(actuals) > 1:
        shown = [(show(v), t) for v, t in actuals]
        return made("conflict", _disagree("the actual value", "got", shown) + ONE_EXPECT_PER_CASE)
    if len(expected) > 1:
        shown = [("no value" if v is None else show(v), t) for v, t in expected]
        return made("conflict", _disagree("the expected value", "had", shown) + ONE_EXPECT_PER_CASE)
    return made(observations[0].status)


def _unlocatable(site: Unlocatable, observations: list[Observation]) -> Group:
    actuals, expected = _values(observations)
    values = tuple(v for v, _ in actuals)
    one_expected = expected[0][0] if len(expected) == 1 else None
    tests = _tests(observations)
    return Group(None, site, "unlocatable", values, one_expected, tests, site.reason)


Buckets = list[tuple[T, tuple[str, ...]]]


def _values(observations: list[Observation]) -> tuple[Buckets[Value], Buckets[Value | None]]:
    valued = [(o.actual, o) for o in observations if o.actual is not None]
    actuals = _buckets(valued, norm, canonical)
    expected = _buckets([(o.expected, o) for o in observations], _same_or_none, _order_or_none)
    return actuals, expected


def _buckets(
    pairs: Iterable[tuple[T, Observation]],
    same: Callable[[T], Hashable],
    order: Callable[[T], str],
) -> Buckets[T]:
    """Split observations into classes of equal values, each with its smallest value and tests."""
    classes: dict[Hashable, list[tuple[T, Observation]]] = defaultdict(list)
    for value, o in pairs:
        classes[same(value)].append((value, o))
    found = [
        (min((v for v, _ in members), key=order), _tests(o for _, o in members))
        for members in classes.values()
    ]
    return sorted(found, key=lambda bucket: order(bucket[0]))


def _same_or_none(value: Value | None) -> Hashable:
    return None if value is None else ("value", norm(value))


def _order_or_none(value: Value | None) -> str:
    return "" if value is None else canonical(value)


def _tests(observations: Iterable[Observation]) -> tuple[str, ...]:
    return tuple(sorted({o.test for o in observations}))


def _disagree(what: str, verb: str, shown: list[tuple[str, tuple[str, ...]]]) -> str:
    each = " ".join(f"{', '.join(tests)} {verb} {label}." for label, tests in shown)
    return f"calls disagree on {what}. {each}"


def _order(g: Group) -> tuple[str, int, int, str, str]:
    site = g.site
    if isinstance(site, Unlocatable):
        return (site.path, site.line, -1, "", site.reason)
    return (site.path, site.line, site.ordinal, site.qualname, "")
