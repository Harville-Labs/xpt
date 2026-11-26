from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeVar

from xpt.diff import norm
from xpt.keyed import entry, with_entry
from xpt.options import PLAIN, Options
from xpt.record import Observation
from xpt.report import show
from xpt.site import CallSite, SiteKey, Unlocatable, short_ids
from xpt.value import Value, canonical

if TYPE_CHECKING:
    from xpt.flaky import Volatile

GroupStatus = Literal["missing", "mismatch", "unsupported", "returned", "unlocatable", "conflict"]

ONE_EXPECT_PER_CASE = (
    " Give each case its own expect(), or pass key=case so one dict holds every case."
)

RETURNED = " Fix the code under test, or expect() the value it returns instead."

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Group:
    """Every failed observation of one call site in a run.

    `id` is the short display id, None for an unlocatable site. `actuals` holds the distinct
    actual values, one when the calls agree. `expected` is None when it was missing or when
    the calls disagree on it. `detail` says why a group is not acceptable, or for an
    acceptable one holds the calls' note, such as how expect.output normalized. `rejected` means
    its one actual value was rejected as a bug with `xpt reject`. `volatile` holds the paths
    whose value changed between stored runs with the same source (see `xpt.flaky`).
    """

    id: str | None
    site: CallSite | Unlocatable
    status: GroupStatus
    actuals: tuple[Value, ...]
    expected: Value | None
    tests: tuple[str, ...]
    detail: str
    rejected: bool = False
    options: Options = PLAIN
    volatile: tuple[Volatile, ...] = ()

    @property
    def acceptable(self) -> bool:
        return self.status in ("missing", "mismatch")

    @property
    def offered(self) -> bool:
        """Whether to suggest accepting it: acceptable, not rejected, and not flaky."""
        return self.acceptable and not self.rejected and not self.volatile


def missing(groups: Iterable[Group]) -> list[Group]:
    """The groups with no expected value for any failing call, and not rejected.

    A keyed group is missing when only new cases failed, so filling it adds entries.
    """
    return [g for g in groups if g.status == "missing" and not g.rejected]


def pick(groups: Iterable[Group], ids: Iterable[str]) -> tuple[list[Group], tuple[str, ...]]:
    """The groups with the given ids, in the order given without repeats, and the ids that
    name no group."""
    by_id = {g.id: g for g in groups if g.id is not None}
    wanted = list(dict.fromkeys(ids))
    return [by_id[i] for i in wanted if i in by_id], tuple(i for i in wanted if i not in by_id)


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

    def made(
        status: GroupStatus,
        detail: str = "",
        values: tuple[Value, ...] = values,
        one_expected: Value | None = one_expected,
    ) -> Group:
        tests = _tests(observations)
        how = observations[0].options
        return Group(id, site, status, values, one_expected, tests, detail, options=how)

    if len(sources) > 1:
        shown = [(f"source {sha[:8]}" if sha else "no expected argument", t) for sha, t in sources]
        return made(
            "conflict",
            _disagree("the expected argument's source text", "had", shown)
            + " The file changed during the run. Run the tests again.",
        )
    if returned := sorted({o.detail for o in observations if o.status == "returned"}):
        return made("returned", " ".join(returned) + RETURNED)
    if any(o.actual is None for o in observations):
        return made(
            "unsupported", " ".join(sorted({o.detail for o in observations if o.actual is None}))
        )
    is_keyed = any(o.key is not None for o in observations)
    if len(actuals) > 1 and not is_keyed:
        shown = [(show(v), t) for v, t in actuals]
        return made("conflict", _disagree("the actual value", "got", shown) + ONE_EXPECT_PER_CASE)
    if len(expected) > 1:
        shown = [("no value" if v is None else show(v), t) for v, t in expected]
        return made("conflict", _disagree("the expected value", "had", shown) + ONE_EXPECT_PER_CASE)
    if is_keyed:
        return _keyed(made, observations)
    return made(observations[0].status, _notes(observations))


def _keyed(made: Callable[..., Group], observations: list[Observation]) -> Group:
    """One group for every case of a keyed call, its actual value the merged dict of cases."""
    if any(o.key is None for o in observations):
        tests = _tests(o for o in observations if o.key is None)
        return made("conflict", f"calls disagree on key=. {', '.join(tests)} passed no key.")
    cases: dict[Hashable, list[tuple[Value, Observation]]] = defaultdict(list)
    for o in observations:
        if (key := o.key) is not None:
            cases[norm(key)].append((key, o))
    merged = observations[0].expected
    for members in cases.values():
        key = members[0][0]
        found = _buckets(
            [(entry(o.actual, key), o) for _, o in members], _same_or_none, _order_or_none
        )
        if len(found) > 1:
            shown = [("no value" if v is None else show(v), t) for v, t in found]
            what = f"the actual value for key={show(key)}"
            return made("conflict", _disagree(what, "got", shown) + " Give each case its own key.")
        if (value := found[0][0]) is not None:
            merged = with_entry(merged, key, value)
    status = "missing" if all(o.status == "missing" for o in observations) else "mismatch"
    return made(status, _notes(observations), values=() if merged is None else (merged,))


def _notes(observations: list[Observation]) -> str:
    return " ".join(sorted({o.detail for o in observations if o.detail}))


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
