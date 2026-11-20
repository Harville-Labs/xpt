from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from xpt.accept import Accepted, AcceptResult
from xpt.decisions import Rejection, mark
from xpt.diff import Diff, diff
from xpt.failure import Compared, Expected, Failure, Raised, Text, Where, where_json
from xpt.group import Group, group
from xpt.render import accept_json, change_json, group_json, outcome_lines
from xpt.report import MAX_CHANGES, MAX_CHARS, changes, cut, show
from xpt.rewrite import Outcome
from xpt.site import CallSite, SiteKey, Unlocatable
from xpt.value import Json

SCHEMA_VERSION = 1
MAX_FRAMES = 4
_WRITTEN = ("accepted", "already_accepted")

Lines = list[str]

_CASE = re.compile(r"^(?P<base>.*?)(?P<case>\[.*\])$")


@dataclass(frozen=True, slots=True)
class Limits:
    """How much of each failure to print; None lifts a limit."""

    changes: int | None
    width: int | None
    output: int | None
    message: int | None
    traceback: bool


def limits(verbosity: int) -> Limits:
    if verbosity >= 2:
        return Limits(None, None, None, None, traceback=True)
    if verbosity == 1:
        return Limits(50, 400, 20, 1000, traceback=False)
    return Limits(MAX_CHANGES, MAX_CHARS, 5, 200, traceback=False)


@dataclass(frozen=True, slots=True)
class Session:
    """A finished pytest session, as the report needs it. `created` and `accepted` are what
    `--expect-create` and `--expect-accept` wrote after the session, None without the flag."""

    failures: tuple[Failure, ...]
    counts: tuple[str, ...]
    seconds: float
    exit_code: int
    run: str | None
    rejected: frozenset[Rejection] = frozenset()
    created: AcceptResult | None = None
    accepted: Accepted | None = None


def expect_groups(session: Session) -> dict[SiteKey, Group]:
    found = session.failures
    observations = [d.observation for f in found if isinstance(d := f.where.detail, Expected)]
    groups = mark(group(observations), session.rejected)
    return {g.site.key: g for g in groups if not isinstance(g.site, Unlocatable)}


def report_lines(session: Session, lim: Limits) -> Lines:
    groups = expect_groups(session)
    pending = _pending(session, groups)
    lines: Lines = []
    for first, cases in _entries(session.failures):
        lines += _block(first, cases, groups, pending, lim)
    if session.accepted is not None:
        lines += unaccepted_lines(session.accepted)
    return [*lines, footer(session, groups)]


def footer(session: Session, groups: dict[SiteKey, Group]) -> str:
    counts = ", ".join(session.counts) or "no tests ran"
    notes = _written_notes(session.created, session.accepted)
    line = " ".join([f"{counts} in {session.seconds:.2f}s.", *notes])
    observed = [
        d.observation for f in session.failures if isinstance(d := f.where.detail, Expected)
    ]
    if session.run is None or not observed:
        return line
    ids = sorted(g.id for g in _pending(session, groups).values() if g.id)
    if ids:
        return f"{line} Next: xpt accept {' '.join(ids)}"
    written = _written(session)
    if all(isinstance(o.site, CallSite) and o.site.key in written for o in observed):
        return f"{line} Next: run the tests."
    return f"{line} Next: xpt failures"


def after_lines(created: AcceptResult | None, accepted: Accepted | None) -> Lines:
    """What `--expect-create` and `--expect-accept` wrote, for pytest's own report."""
    lines = [] if accepted is None else unaccepted_lines(accepted)
    notes = _written_notes(created, accepted)
    return [*lines, " ".join(notes)] if notes else lines


def unaccepted_lines(accepted: Accepted) -> Lines:
    """Each id `--expect-accept` named but did not write, with the reason."""
    lines = outcome_lines(_unwritten(accepted))
    for id in accepted.unknown:
        lines += [
            f"{id}  unknown",
            "  No expect failure in this run has this id. A test that passes now has none.",
        ]
    return lines


def _pending(session: Session, groups: dict[SiteKey, Group]) -> dict[SiteKey, Group]:
    """The groups to suggest accepting: offered, recorded, and not written after the run."""
    if session.run is None:
        return {}
    written = _written(session)
    return {key: g for key, g in groups.items() if g.offered and key not in written}


def _written(session: Session) -> set[SiteKey]:
    accepted = None if session.accepted is None else session.accepted.result
    results = [r for r in (session.created, accepted) if r is not None]
    return {s.key for r in results for s in _sites(r, *_WRITTEN)}


def _unwritten(accepted: Accepted) -> list[Outcome]:
    return [o for o in accepted.result.outcomes if o.status not in _WRITTEN]


def _written_notes(created: AcceptResult | None, accepted: Accepted | None) -> list[str]:
    """`Filled N missing expectations: path:line ...`, `Accepted N expectations: ...`, and
    `Not accepted: id status, ...`, each only when it has something to say."""
    notes = []
    if created is not None:
        notes.append(_wrote("Filled", "missing ", _sites(created, "accepted")))
    if accepted is not None:
        notes.append(_wrote("Accepted", "", _sites(accepted.result, *_WRITTEN)))
        kept = [f"{o.group.id} {o.status}" for o in _unwritten(accepted)]
        kept += [f"{id} unknown" for id in accepted.unknown]
        notes.append(f"Not accepted: {', '.join(kept)}." if kept else "")
    return [n for n in notes if n]


def _wrote(verb: str, kind: str, sites: list[CallSite]) -> str:
    if not sites:
        return ""
    noun = "expectation" if len(sites) == 1 else "expectations"
    where = " ".join(f"{s.path}:{s.line}" for s in sites)
    return f"{verb} {len(sites)} {kind}{noun}: {where}."


def _sites(created: AcceptResult, *statuses: str) -> list[CallSite]:
    return [
        o.group.site
        for o in created.outcomes
        if o.status in statuses and isinstance(o.group.site, CallSite)
    ]


def _entries(failures: Sequence[Failure]) -> list[tuple[Failure, list[Failure]]]:
    """Fold parametrized cases that fail the same way into the first case."""
    entries: dict[tuple[object, ...], tuple[Failure, list[Failure]]] = {}
    for f in failures:
        match = _CASE.match(f.test)
        if match is None:
            entries[(f.test, f.when)] = (f, [])
            continue
        key = (match["base"], f.when, f.where.path, f.where.line, _kind(f))
        if key in entries:
            entries[key][1].append(f)
        else:
            entries[key] = (f, [])
    return list(entries.values())


def _kind(f: Failure) -> str:
    detail = f.where.detail
    return detail.type if isinstance(detail, Raised) else type(detail).__name__


def _block(
    f: Failure,
    cases: Sequence[Failure],
    groups: dict[SiteKey, Group],
    pending: dict[SiteKey, Group],
    lim: Limits,
) -> Lines:
    label = "FAILED" if f.when == "call" else "ERROR"
    phase = "" if f.when == "call" else f" ({f.when})"
    at = _at(f.where)
    lines = [f"{label} {f.test}{phase}" + ("" if at == f.test else f"  {at}")]
    if f.where.source:
        lines.append(f"  > {cut(f.where.source, lim.width)}")
    detail = _detail(f, groups, lim)
    lines += detail
    lines += _accept_command(f, pending)
    if f.where.cause:
        lines.append(f"  caused by: {cut(f.where.cause, lim.message)}")
    for stream, text in f.output:
        lines += [f"  {stream}: {cut(line, lim.width)}" for line in _tail(text, lim.output)]
    lines += _cases(detail, cases, groups, lim)
    if lim.traceback and f.traceback:
        lines += ["  traceback:", *(f"    {line}" for line in f.traceback.splitlines())]
    return lines


def _accept_command(f: Failure, pending: dict[SiteKey, Group]) -> Lines:
    detail = f.where.detail
    if not isinstance(detail, Expected) or not isinstance(
        site := detail.observation.site, CallSite
    ):
        return []
    g = pending.get(site.key)
    return [] if g is None else [f"  accept: xpt accept {g.id}"]


def _cases(
    first: Lines, cases: Sequence[Failure], groups: dict[SiteKey, Group], lim: Limits
) -> Lines:
    """Each folded case by node id, with its first detail line that differs from the first's.

    Folded cases share the exception type, so the brief drops it.
    """
    limit = len(cases) if lim.changes is None else lim.changes
    lines = []
    for case in cases[:limit]:
        pairs = zip(_detail(case, groups, lim), first, strict=False)
        brief = next((mine.strip() for mine, theirs in pairs if mine != theirs), None)
        if brief and isinstance(detail := case.where.detail, Raised):
            brief = brief.removeprefix(f"{detail.type}: ")
        lines.append(f"  also fails: {case.test}" + (f"  {brief}" if brief else ""))
    hidden = len(cases) - limit
    if hidden > 0:
        lines.append(f"  ... {hidden} more case{'' if hidden == 1 else 's'}")
    return lines


def _at(w: Where) -> str:
    """The failing frame as `path:line`, or the chain of project frames from the test down."""
    at = w.path if w.line is None else f"{w.path}:{w.line}"
    if not w.callers:
        return f"{at} in {w.function}" if w.function else at
    frames = [_frame(f"{c.path}:{c.line}", c.function) for c in w.callers]
    frames.append(_frame(at, w.function))
    if len(frames) > MAX_FRAMES:
        frames = [frames[0], "...", *frames[1 - MAX_FRAMES :]]
    return " > ".join(frames)


def _frame(at: str, function: str | None) -> str:
    return f"{at} {function}" if function else at


def _detail(f: Failure, groups: dict[SiteKey, Group], lim: Limits) -> Lines:
    detail = f.where.detail
    match detail:
        case Expected():
            o = detail.observation
            site = o.site
            g = None if isinstance(site, Unlocatable) else groups.get(site.key)
            rejected = "  rejected" if g and g.rejected else ""
            lines = [f"  {(g.id if g else None) or '-'}  {g.status if g else o.status}{rejected}"]
            if o.expected is not None and o.actual is not None:
                return lines + _changes(diff(o.expected, o.actual, o.tolerance), lim)
            if o.actual is not None:
                return [*lines, f"  actual: {show(o.actual, lim.width)}"]
            return [*lines, f"  {cut(o.detail, lim.message)}"]
        case Compared():
            found = diff(detail.left, detail.right)
            return _changes(found, lim, labeled=True)
        case Raised():
            return _raised(detail, lim)
        case Text():
            return _text(detail.text, lim)


def _changes(found: Diff, lim: Limits, labeled: bool = False) -> Lines:
    return changes(found, limit=lim.changes, width=lim.width, labeled=labeled)


def _raised(r: Raised, lim: Limits) -> Lines:
    if r.type != "AssertionError":
        message = " ".join(r.message.split())
        return [f"  {cut(f'{r.type}: {message}' if message else r.type, lim.message)}"]
    first, *rest = r.message.splitlines() or [""]
    lines = [f"AssertionError: {first}" if first else "AssertionError", *rest]
    return [f"  {cut(line, lim.message)}" for line in _head(lines, lim.changes)]


def _text(text: str, lim: Limits) -> Lines:
    lines = text.splitlines()
    errors = [line for line in lines if line.startswith("E ")]
    shown = errors or _tail(text, lim.changes)
    return [f"  {cut(line, lim.message)}" for line in shown]


def _head(lines: Sequence[str], limit: int | None) -> list[str]:
    if limit is None or len(lines) <= limit:
        return list(lines)
    return [*lines[:limit], f"... {len(lines) - limit} more lines"]


def _tail(text: str, limit: int | None) -> list[str]:
    lines = text.rstrip("\n").splitlines()
    return lines if limit is None else lines[-limit:]


def report_json(session: Session) -> dict[str, Json]:
    groups = expect_groups(session)
    return {
        "schema_version": SCHEMA_VERSION,
        "exit_code": session.exit_code,
        "summary": footer(session, groups),
        "counts": list(session.counts),
        "duration": session.seconds,
        "run": session.run,
        "created": None if session.created is None else accept_json(session.created),
        "accepted": None
        if session.accepted is None
        else accept_json(session.accepted.result) | {"unknown": list(session.accepted.unknown)},
        "failures": [_failure_json(f, groups) for f in session.failures],
    }


def _failure_json(f: Failure, groups: dict[SiteKey, Group]) -> dict[str, Json]:
    data: dict[str, Json] = {
        "test": f.test,
        "when": f.when,
        **where_json(f.where),
        "output": dict(f.output),
        "traceback": f.traceback,
    }
    detail = f.where.detail
    if isinstance(detail, Expected) and not isinstance(detail.observation.site, Unlocatable):
        g = groups.get(detail.observation.site.key)
        if g is not None:
            data["group"] = group_json(g)
    if isinstance(detail, Compared):
        found = diff(detail.left, detail.right)
        data["changes"] = [change_json(c, labeled=True) for c in found.changes]
        data["unchanged"] = found.unchanged
    return data
