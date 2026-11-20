from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from xpt.accept import AcceptResult
from xpt.decisions import FILE, RejectOutcome
from xpt.diff import Change, Diff, diff
from xpt.group import Group
from xpt.path import format_path
from xpt.report import MAX_CHANGES, MAX_CHARS, changes, show
from xpt.rewrite import Outcome
from xpt.site import CallSite, Unlocatable
from xpt.value import Json, to_json

Lines = list[str]


def group_diff(g: Group) -> Diff | None:
    if g.status != "mismatch" or g.expected is None or len(g.actuals) != 1:
        return None
    return diff(g.expected, g.actuals[0], g.tolerance)


def heading(id: str | None, site: CallSite | Unlocatable, status: str) -> str:
    return f"{id or '-'}  {site.path}:{site.line}  {status}"


def group_lines(g: Group, *, verbose: bool = False, full: bool = False) -> Lines:
    """A heading line and the group's body; `verbose` adds the function and tests, `full`
    lifts the limits and prints both whole values."""
    limit, width = (None, None) if full else (MAX_CHANGES, MAX_CHARS)
    rejected = "  rejected" if g.rejected else ""
    lines = [f"{heading(g.id, g.site, g.status)}{rejected}  {_count(len(g.tests), 'test')}"]
    if verbose:
        if isinstance(g.site, CallSite):
            lines.append(f"  function: {g.site.qualname}")
        lines += [f"  test: {t}" for t in g.tests]
    found = group_diff(g)
    if found is not None:
        if full and g.expected is not None:
            lines.append(f"  expected: {show(g.expected, width)}")
            lines.append(f"  actual: {show(g.actuals[0], width)}")
        return lines + changes(found, limit=limit, width=width)
    if g.status == "missing":
        return [*lines, f"  actual: {show(g.actuals[0], width)}"]
    return [*lines, f"  {g.detail}"]


def failures_lines(run: str, groups: Sequence[Group], run_option: str) -> Lines:
    if not groups:
        return [f"No failures in run {run}."]
    lines = [line for g in groups for line in group_lines(g)]
    ids = [g.id for g in groups if g.offered and g.id is not None]
    rejected = sum(g.rejected for g in groups)
    summary = (
        f"{_count(len(groups), 'failure')} in run {run} ({_statuses(g.status for g in groups)}), "
        f"{len(ids)} acceptable" + (f", {rejected} rejected." if rejected else ".")
    )
    if ids:
        return [*lines, f"{summary} Next: xpt accept{run_option} {' '.join(ids)}"]
    return [*lines, f"{summary} Fix the tests and run them again."]


def show_lines(g: Group, full: bool, run_option: str) -> Lines:
    lines = group_lines(g, verbose=True, full=full)
    if g.rejected:
        return [
            *lines,
            f"{g.id} was rejected as a bug. Fix the code under test and run the tests again, "
            f"or run xpt accept{run_option} {g.id} to accept it anyway.",
        ]
    if g.acceptable:
        return [*lines, f"Next: xpt accept{run_option} {g.id}"]
    return [*lines, f"{g.id} cannot be accepted. Fix the test and run it again."]


def accept_lines(result: AcceptResult, dry_run: bool, run_option: str, target: str) -> Lines:
    lines = _outcome_lines(result.outcomes)
    counts = f"({_statuses(o.status for o in result.outcomes)})"
    files = ", ".join(result.changed)
    if dry_run:
        done = f"Dry run, would change {files}" if files else "Dry run, no files would change"
        return [*lines, f"{done} {counts}. Next: xpt accept{run_option} {target}"]
    done = f"Changed {files}" if files else "No files changed"
    suspects = [o.group.id for o in result.outcomes if o.status == "suspect" and o.group.id]
    if any(o.status == "stale" for o in result.outcomes):
        return [*lines, f"{done} {counts}. Next: run the tests, then xpt failures."]
    if suspects:
        force = f"xpt accept{run_option} --force {' '.join(suspects)}"
        return [*lines, f"{done} {counts}. Next: fix the code under test, or run {force}."]
    return [*lines, f"{done} {counts}. Next: run the tests."]


def reject_lines(outcomes: Sequence[RejectOutcome]) -> Lines:
    lines = _outcome_lines(outcomes)
    added = sum(o.status == "rejected" for o in outcomes)
    done = (
        f"Recorded {_count(added, 'rejection')} in .xpt/{FILE}"
        if added
        else "Nothing new to record"
    )
    counts = _statuses(o.status for o in outcomes)
    return [*lines, f"{done} ({counts}). Next: fix the code under test, then run the tests."]


def _outcome_lines(outcomes: Sequence[Outcome | RejectOutcome]) -> Lines:
    lines = []
    for o in outcomes:
        lines.append(heading(o.group.id, o.group.site, o.status))
        if o.reason:
            lines.append(f"  {o.reason}")
    return lines


def runs_lines(runs: Sequence[tuple[str, bool, int]]) -> Lines:
    if not runs:
        return ["No runs under .xpt/. Run the tests to record one."]
    lines = [
        f"{id}  {_count(n, 'failure')}" + ("  latest" if latest else "") for id, latest, n in runs
    ]
    return [*lines, f"{_count(len(runs), 'run')}. Next: xpt failures --run ID"]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _statuses(statuses: Iterable[str]) -> str:
    return ", ".join(f"{n} {status}" for status, n in Counter(statuses).items())


def site_json(site: CallSite | Unlocatable) -> dict[str, Json]:
    qualname = site.qualname if isinstance(site, CallSite) else None
    return {"path": site.path, "line": site.line, "qualname": qualname}


def group_json(g: Group) -> dict[str, Json]:
    data: dict[str, Json] = {
        "id": g.id,
        "site": site_json(g.site),
        "status": g.status,
        "acceptable": g.acceptable,
        "rejected": g.rejected,
        "tests": list(g.tests),
        "detail": g.detail,
        "actuals": [to_json(v) for v in g.actuals],
    }
    if g.expected is not None:
        data["expected"] = to_json(g.expected)
    found = group_diff(g)
    if found is not None:
        data["changes"] = [change_json(c) for c in found.changes]
        data["unchanged"] = found.unchanged
    return data


def change_json(c: Change, *, labeled: bool = False) -> dict[str, Json]:
    """`labeled` names the sides left and right, for a diff of `assert left == right`."""
    first, second = ("left", "right") if labeled else ("expected", "actual")
    data: dict[str, Json] = {"path": format_path(c.path), "op": c.op}
    if c.expected is not None:
        data[first] = to_json(c.expected)
    if c.actual is not None:
        data[second] = to_json(c.actual)
    return data


def accept_json(result: AcceptResult) -> dict[str, Json]:
    return {"outcomes": [outcome_json(o) for o in result.outcomes], "changed": list(result.changed)}


def outcome_json(o: Outcome | RejectOutcome) -> dict[str, Json]:
    return {
        "id": o.group.id,
        "site": site_json(o.group.site),
        "status": o.status,
        "reason": o.reason,
    }
