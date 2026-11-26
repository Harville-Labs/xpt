"""The changed code a failing test ran: the run's trace intersected with `git diff`."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xpt.changes import Changes, GitError, Hunk, function_at, parse_source
from xpt.trace import Trace, file_sha

Status = Literal["ran", "none_ran", "no_trace", "no_git"]

NONE_RAN = "no changed code ran; the expectation or the environment changed"
NO_TRACE = (
    "no trace of these tests; run them with pytest --xpt-trace to see the changed code they ran"
)


@dataclass(frozen=True, slots=True)
class Function:
    """The changed hunks in one function, or at module level when `name` is None."""

    path: str
    name: str | None
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True, slots=True)
class ChangedCode:
    """What `xpt show` and `xpt accept` print under a group. `since` is the ref the diff is
    against, None when there is no diff. `stale` lists files the tests ran that changed since
    the run, whose line numbers no longer hold, so they are left out. `note` is the git error
    for `no_git`."""

    status: Status
    since: str | None
    functions: tuple[Function, ...] = ()
    stale: tuple[str, ...] = ()
    note: str = ""


Diff = Changes | GitError


def changed_code(root: Path, trace: Trace | None, tests: Iterable[str], diff: Diff) -> ChangedCode:
    ran = None if trace is None else trace.ran(tests)
    if ran is None or trace is None:
        return ChangedCode("no_trace", None)
    if isinstance(diff, GitError):
        return ChangedCode("no_git", None, note=str(diff))
    stale = tuple(sorted(p for p in ran if trace.files.get(p) != file_sha(root / p)))
    hit = [h for h in diff.hunks if h.path in ran and h.path not in stale and h.ran(ran[h.path])]
    functions = _by_function(root, hit)
    return ChangedCode("ran" if functions else "none_ran", diff.since, functions, stale)


def _by_function(root: Path, hunks: list[Hunk]) -> tuple[Function, ...]:
    trees: dict[str, ast.Module | None] = {}
    grouped: dict[tuple[str, str | None], list[Hunk]] = {}
    for h in hunks:
        if h.path not in trees:
            trees[h.path] = parse_source(root / h.path)
        tree = trees[h.path]
        name = None if tree is None else function_at(tree, max(h.start, 1))
        grouped.setdefault((h.path, name), []).append(h)
    return tuple(Function(path, name, tuple(found)) for (path, name), found in grouped.items())
