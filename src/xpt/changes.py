"""The project's changed Python lines against a git ref, read from `git diff`, and the function
each change sits in. git is optional: without it, or outside a repository, `changes` raises
GitError with a one-line reason."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DEFAULT_BRANCHES = ("main", "master", "origin/main", "origin/master")
TIMEOUT = 30

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Hunk:
    """One run of changed lines in a file. `start` and `count` are the new side's lines in the
    working tree. A hunk that only removes lines has `count` 0 and `start` the line before
    the removal."""

    path: str
    start: int
    count: int
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def lines(self) -> range:
        return range(self.start, self.start + self.count)

    def ran(self, lines: frozenset[int]) -> bool:
        """Whether a run of `lines` executed this change: one of its new lines, or for a
        removal a line on either side of it."""
        if self.count == 0:
            return self.start in lines or self.start + 1 in lines
        return any(n in lines for n in self.lines)


@dataclass(frozen=True, slots=True)
class Changes:
    """The changed lines against `since`, the ref as the user knows it, such as `main`."""

    since: str
    hunks: tuple[Hunk, ...]


def changes(root: Path, since: str | None = None) -> Changes:
    """Every changed `.py` line under `root`, staged, unstaged, and untracked, against
    `since`, or by default the merge base of HEAD with the default branch, or HEAD.

    Raises GitError when git is not installed, `root` is not in a repository, or the ref is
    unknown.
    """
    if since is not None and since.startswith("-"):
        raise GitError(f"{since!r} is not a git ref.")
    _git(root, "rev-parse", "--show-toplevel", reason="not a git repository")
    base, label = _base(root, since)
    diff = _git(
        root,
        "-c",
        "core.quotePath=false",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--unified=0",
        "--no-prefix",
        "--relative",
        base,
        "--",
        "*.py",
        reason=f"git diff {label} failed",
    )
    untracked = _git(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--", "*.py", reason=""
    )
    hunks = parse(diff) + _untracked(root, [p for p in untracked.split("\0") if p])
    return Changes(label, tuple(sorted(hunks, key=lambda h: (h.path, h.start))))


def _base(root: Path, since: str | None) -> tuple[str, str]:
    if since is not None:
        _git(
            root,
            "rev-parse",
            "--verify",
            "--quiet",
            f"{since}^{{commit}}",
            reason=f"{since} is not a commit",
        )
        return since, since
    if _try(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:
        return EMPTY_TREE, "an empty repository"
    for branch in _default_branches(root):
        if (base := _try(root, "merge-base", "HEAD", branch)) is not None:
            return base.strip(), branch
    return "HEAD", "HEAD"


def _default_branches(root: Path) -> list[str]:
    found = []
    head = _try(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if head and head.strip():
        found.append(head.strip())
    for name in DEFAULT_BRANCHES:
        if name not in found and _try(root, "rev-parse", "--verify", "--quiet", name) is not None:
            found.append(name)
    return found


def _try(root: Path, *args: str) -> str | None:
    try:
        return _git(root, *args, reason="")
    except GitError:
        return None


def _git(root: Path, *args: str, reason: str) -> str:
    env = os.environ | {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            env=env,
            timeout=TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git failed: {error}") from None
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(reason or (message[-1] if message else f"git {args[0]} failed"))
    return done.stdout.decode("utf-8", "replace")


def parse(diff: str) -> list[Hunk]:
    """The hunks of a `git diff --unified=0 --no-prefix` output."""
    hunks: list[Hunk] = []
    path: str | None = None
    current: tuple[int, int, list[str], list[str]] | None = None

    def close() -> None:
        if current is not None and path is not None:
            start, count, removed, added = current
            hunks.append(Hunk(path, start, count, tuple(removed), tuple(added)))

    for line in diff.splitlines():
        if line.startswith("+++ "):
            close()
            current = None
            name = line[4:].rstrip("\t")
            path = None if name == "/dev/null" else name
        elif match := _HUNK.match(line):
            close()
            count = 1 if match[2] is None else int(match[2])
            current = (int(match[1]), count, [], [])
        elif current is not None and line.startswith("-"):
            current[2].append(line[1:])
        elif current is not None and line.startswith("+"):
            current[3].append(line[1:])
        elif line.startswith("diff --git"):
            close()
            current = path = None
    close()
    return hunks


def _untracked(root: Path, paths: Sequence[str]) -> list[Hunk]:
    hunks = []
    for path in paths:
        try:
            text = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        added = tuple(text.splitlines())
        if added:
            hunks.append(Hunk(path, 1, len(added), (), added))
    return hunks


def function_at(tree: ast.Module, line: int) -> str | None:
    """The qualified name of the innermost function or class around `line`, decorators
    included, None at module level."""
    around = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            if first <= line <= (node.end_lineno or node.lineno):
                around.append((first, node.name))
    return ".".join(name for _, name in sorted(around)) or None


def parse_source(path: Path) -> ast.Module | None:
    """The file's syntax tree, None when it can't be read or parsed."""
    try:
        return ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return None
