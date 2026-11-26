from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from xpt.accept import AcceptResult
from xpt.changed import NO_TRACE, NONE_RAN, ChangedCode
from xpt.decisions import FILE, RejectOutcome
from xpt.diff import Change, Diff
from xpt.flaky import Volatile
from xpt.group import Group
from xpt.path import format_path
from xpt.report import MAX_CHANGES, MAX_CHARS, changes, cut, line_diff, show
from xpt.rewrite import Outcome
from xpt.site import CallSite, Unlocatable
from xpt.value import Json, to_json
from xpt.verify import Rerun

Lines = list[str]


def group_diff(g: Group) -> Diff | None:
    if not g.acceptable or g.expected is None or len(g.actuals) != 1:
        return None
    return g.options.diff(g.expected, g.actuals[0])


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
        lines += changes(found, limit=limit, width=width) + _note(g)
    elif g.status == "missing":
        lines += [f"  actual: {show(g.actuals[0], width)}", *_note(g)]
    else:
        lines.append(f"  {g.detail}")
    return lines + flaky_lines(g.volatile, limit)


def flaky_lines(found: Sequence[Volatile], limit: int | None = MAX_CHANGES) -> Lines:
    """One `flaky:` line per volatile path, with the option that would keep it stable."""
    shown = found if limit is None else found[:limit]
    lines = [f"  flaky: {v.reason}" for v in shown]
    if hidden := len(found) - len(shown):
        lines.append(f"  ... {_count(hidden, 'more flaky path')}")
    return lines


def _note(g: Group) -> Lines:
    return [f"  {g.detail}"] if g.detail else []


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


def show_lines(g: Group, full: bool, run_option: str, changed: ChangedCode | None = None) -> Lines:
    lines = group_lines(g, verbose=True, full=full)
    if changed is not None:
        lines += changed_lines(changed, len(g.tests), full=full)
    if g.rejected:
        return [
            *lines,
            f"{g.id} was rejected as a bug. Fix the code under test and run the tests again, "
            f"or run xpt accept{run_option} {g.id} to accept it anyway.",
        ]
    if g.acceptable:
        return [*lines, f"Next: xpt accept{run_option} {g.id}"]
    return [*lines, f"{g.id} cannot be accepted. Fix the test and run it again."]


def accept_lines(
    result: AcceptResult,
    dry_run: bool,
    run_option: str,
    target: str,
    *,
    verified: bool = False,
    changed: Mapping[str, ChangedCode] | None = None,
) -> Lines:
    """`verified` leaves out "Next: run the tests.", because --verify just ran them.
    `changed` holds the changed code each group's tests ran, by group id."""
    lines = outcome_lines(result.outcomes, changed)
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
        return [*lines, f"{done} {counts}. Next: {_fix(result.outcomes)}, or run {force}."]
    if verified:
        return [*lines, f"{done} {counts}."]
    return [*lines, f"{done} {counts}. Next: run the tests."]


def _fix(outcomes: Sequence[Outcome]) -> str:
    flaky = [bool(o.group.volatile) for o in outcomes if o.status == "suspect"]
    if all(flaky):
        return "make the tests deterministic"
    if any(flaky):
        return "fix the code under test or make the tests deterministic"
    return "fix the code under test"


FlakySite = tuple[str, CallSite, tuple[Volatile, ...]]


def flaky_sites_lines(sites: Sequence[FlakySite], runs: int) -> Lines:
    """`xpt flaky`: each call site with volatile paths, and a footer."""
    stored = _count(runs, "stored run")
    if not sites:
        return [f"No flaky call sites in {stored}."]
    lines = []
    for id, site, found in sites:
        compared = max(len(v.runs) for v in found)
        lines.append(f"{heading(id, site, 'flaky')}  {_count(compared, 'run')}")
        lines += flaky_lines(found, None)
    return [
        *lines,
        f"{_count(len(sites), 'flaky call site')} in {stored}. "
        "Next: add the suggested options to the tests, then run them again.",
    ]


def rerun_lines(rerun: Rerun) -> Lines:
    tests = f"the {_count(len(rerun.tests), 'accepted test')}"
    if rerun.passed is None:
        return ["Nothing to rerun."]
    if rerun.passed:
        return [f"Verified: {tests} {'passes' if len(rerun.tests) == 1 else 'pass'}."]
    return [
        *rerun.output.strip("\n").splitlines(),
        f"Verify failed: pytest exited {rerun.code} on {tests}.",
    ]


def rerun_json(rerun: Rerun) -> dict[str, Json]:
    return {
        "tests": list(rerun.tests),
        "exit_code": rerun.code,
        "passed": rerun.passed,
        "output": rerun.output,
    }


def reject_lines(outcomes: Sequence[RejectOutcome]) -> Lines:
    lines = outcome_lines(outcomes)
    added = sum(o.status == "rejected" for o in outcomes)
    done = (
        f"Recorded {_count(added, 'rejection')} in .xpt/{FILE}"
        if added
        else "Nothing new to record"
    )
    counts = _statuses(o.status for o in outcomes)
    return [*lines, f"{done} ({counts}). Next: fix the code under test, then run the tests."]


def outcome_lines(
    outcomes: Sequence[Outcome | RejectOutcome], changed: Mapping[str, ChangedCode] | None = None
) -> Lines:
    """A heading and reason per outcome. With `changed`, the changed code each group's tests
    ran, and once at the end why it is unknown when git could not diff. A group with no
    trace prints nothing, because `xpt show` already says how to trace it."""
    lines: Lines = []
    notes: Lines = []
    for o in outcomes:
        lines.append(heading(o.group.id, o.group.site, o.status))
        if o.reason:
            lines.append(f"  {o.reason}")
        found = (changed or {}).get(o.group.id or "")
        if found is None:
            continue
        shown = changed_lines(found, len(o.group.tests))
        if found.status in ("ran", "none_ran"):
            lines += shown
        elif found.status == "no_git" and (note := shown[0].strip()) not in notes:
            notes.append(note)
    return lines + notes


def changed_lines(c: ChangedCode, tests: int, *, full: bool = False) -> Lines:
    """The changed hunks the group's tests ran, grouped by function, cut to MAX_CHANGES lines
    of MAX_CHARS unless `full`."""
    limit, width = (None, None) if full else (MAX_CHANGES, MAX_CHARS)
    if c.status == "no_trace":
        return [f"  {NO_TRACE}"]
    if c.status == "no_git":
        return [f"  changed code: unknown, {c.note}."]
    stale = [f"  changed since the run, left out: {', '.join(c.stale)}"] if c.stale else []
    if c.status == "none_ran":
        return [f"  {NONE_RAN} (diff against {c.since})", *stale]
    who = "this test" if tests == 1 else "these tests"
    body: Lines = []
    for f in c.functions:
        where = ",".join(str(h.start) for h in f.hunks)
        body.append(f"    {f.path}:{where} {f.name or '<module>'}")
        text = [t for h in f.hunks for t in (*h.removed, *h.added) if t.strip()]
        indent = min((len(t) - len(t.lstrip()) for t in text), default=0)
        for h in f.hunks:
            body += [f"      - {cut(t[indent:].rstrip(), width)}" for t in h.removed]
            body += [f"      + {cut(t[indent:].rstrip(), width)}" for t in h.added]
    shown = body if limit is None else body[:limit]
    if hidden := len(body) - len(shown):
        shown.append(f"    ... {_count(hidden, 'more line')}")
    return [f"  changed code {who} ran (since {c.since}):", *shown, *stale]


def changed_json(c: ChangedCode) -> dict[str, Json]:
    return {
        "status": c.status,
        "since": c.since,
        "functions": [
            {
                "path": f.path,
                "function": f.name,
                "hunks": [
                    {
                        "line": h.start,
                        "count": h.count,
                        "removed": list(h.removed),
                        "added": list(h.added),
                    }
                    for h in f.hunks
                ],
            }
            for f in c.functions
        ],
        "stale": list(c.stale),
        "note": c.note,
    }


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
        "flaky": [volatile_json(v) for v in g.volatile],
    }
    if g.expected is not None:
        data["expected"] = to_json(g.expected)
    found = group_diff(g)
    if found is not None:
        data["changes"] = [change_json(c) for c in found.changes]
        data["unchanged"] = found.unchanged
    return data


def volatile_json(v: Volatile) -> dict[str, Json]:
    return {
        "path": format_path(v.path),
        "kind": v.kind,
        "values": v.values,
        "runs": list(v.runs),
        "spread": v.spread,
        "suggestion": v.suggestion,
    }


def flaky_site_json(id: str, site: CallSite, found: Sequence[Volatile]) -> dict[str, Json]:
    return {
        "id": id,
        "site": site_json(site),
        "run": found[0].runs[-1],
        "flaky": [volatile_json(v) for v in found],
    }


def change_json(c: Change, *, labeled: bool = False) -> dict[str, Json]:
    """`labeled` names the sides left and right, for a diff of `assert left == right`."""
    first, second = ("left", "right") if labeled else ("expected", "actual")
    data: dict[str, Json] = {"path": format_path(c.path), "op": c.op}
    if c.expected is not None:
        data[first] = to_json(c.expected)
    if c.actual is not None:
        data[second] = to_json(c.actual)
    if (lines := line_diff(c)) is not None:
        data["lines"] = lines
    return data


def accept_json(
    result: AcceptResult, changed: Mapping[str, ChangedCode] | None = None
) -> dict[str, Json]:
    """`changed` adds `changed_code` to the outcome of each group it names, by id."""
    found = changed or {}
    outcomes = [outcome_json(o, found.get(o.group.id or "")) for o in result.outcomes]
    return {"outcomes": outcomes, "changed": list(result.changed)}


def outcome_json(o: Outcome | RejectOutcome, changed: ChangedCode | None = None) -> dict[str, Json]:
    data: dict[str, Json] = {
        "id": o.group.id,
        "site": site_json(o.group.site),
        "status": o.status,
        "reason": o.reason,
    }
    if changed is not None:
        data["changed_code"] = changed_json(changed)
    return data
