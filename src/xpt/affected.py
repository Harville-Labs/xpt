"""Which tests ran the changed code: a map of the project lines every test ran, from
`pytest --xpt-map`, intersected with `git diff`.

The map is `.xpt/map.json`, one file for the project that each map run replaces whole. Each
pytest process, controller or xdist worker, writes its tests to `.xpt/map/<run>/<worker>.json`
at session end, and the controller joins them into the map and deletes them.

Line numbers in the map belong to the files as they were when it was built, whose hashes it
keeps, so a changed file is compared through the old side of the diff. That holds only when the
diff's base is the map's version of the file; otherwise the map is stale and every test runs.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from xpt import atomic, changes, sources
from xpt.changes import GitError, Hunk
from xpt.record import (
    DIRECTORY,
    SCHEMA_VERSION,
    RecordError,
    json_object,
    run_id,
    state_directory,
    versioned,
)
from xpt.trace import Lines
from xpt.value import Json

MAP = "map.json"
PARTS = "map"
CONFIGURATION = frozenset({"conftest.py", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini"})
TEST_FILES = ("test_*.py", "*_test.py")
_WORKER = re.compile(r"[A-Za-z0-9_-]+")
NO_MAP = "no map of the tests; build it with pytest --xpt-map"


@dataclass(frozen=True, slots=True)
class Map:
    """The lines each test ran when the map was built. `files` holds the sha256 of every
    project file the run imported or ran, with line endings as `\\n`, and `commit` the commit
    checked out then, None without git."""

    commit: str | None
    files: Mapping[str, str]
    tests: Mapping[str, Lines]


@dataclass(frozen=True, slots=True)
class Pick:
    """One test to run, a node id or a whole test file, and why."""

    test: str
    reason: str


@dataclass(frozen=True, slots=True)
class Affected:
    """The tests the changes since `since` select. `everything` is why the selection can't be
    trusted and every test has to run, None when `tests` holds."""

    since: str | None
    tests: tuple[Pick, ...] = ()
    everything: str | None = None


def digest(data: bytes) -> str:
    """The sha256 of a file's bytes with `\\r\\n` as `\\n`, so a checkout that converts line
    endings hashes the same as the commit."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _file_digest(path: Path) -> str | None:
    try:
        return digest(path.read_bytes())
    except OSError:
        return None


def write_part(
    root: Path, run: str, worker: str, tests: Mapping[str, Lines], imported: Iterable[str]
) -> None:
    """Store one process's tests in `.xpt/map/<run>/<worker>.json`. `imported` names the
    project files whose module body ran, so a file no test ran a line of is still known."""
    paths = sorted({*imported, *(p for lines in tests.values() for p in lines)})
    if not paths and not tests:
        return
    data = {
        "schema_version": SCHEMA_VERSION,
        "files": paths,
        "tests": {t: {p: sorted(ls) for p, ls in ran.items()} for t, ran in sorted(tests.items())},
    }
    directory = state_directory(root) / PARTS / run_id(run)
    directory.mkdir(parents=True, exist_ok=True)
    name = worker if _WORKER.fullmatch(worker) else "main"
    atomic.replace(directory / f"{name}.json", json.dumps(data, ensure_ascii=False).encode())


def merge(root: Path, run: str, commit: str | None, *, keep: bool) -> None:
    """Join every process's part of `run` into `.xpt/map.json`, when `keep`, and delete the
    parts. A run with no parts leaves the map as it was."""
    directory = root / DIRECTORY / PARTS / run_id(run)
    try:
        parts = sorted(directory.glob("*.json"))
        if keep and parts:
            files: set[str] = set()
            tests: dict[str, Lines] = {}
            for part in parts:
                data = _read(root, part)
                files |= set(_strings(data.get("files"), "files"))
                tests |= _tests(json_object(data.get("tests"), "tests"))
            hashes = {p: sha for p in sorted(files) if (sha := _file_digest(root / p)) is not None}
            stored = {
                "schema_version": SCHEMA_VERSION,
                "commit": commit,
                "files": hashes,
                "tests": {
                    t: {p: sorted(ls) for p, ls in ran.items()} for t, ran in sorted(tests.items())
                },
            }
            text = json.dumps(stored, ensure_ascii=False)
            atomic.replace(state_directory(root) / MAP, text.encode())
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        if directory.parent.is_dir() and not any(directory.parent.iterdir()):
            directory.parent.rmdir()


def load(root: Path) -> Map | None:
    """The map, None when none was built. Raises RecordError for a malformed one."""
    path = root / DIRECTORY / MAP
    if not path.is_file():
        return None
    data = _read(root, path)
    try:
        commit = data.get("commit")
        if commit is not None and not isinstance(commit, str):
            raise ValueError(f"commit must be a string or null, got {commit!r}.")
        files = json_object(data.get("files"), "files")
        for name, sha in files.items():
            if not isinstance(sha, str):
                raise ValueError(f"files[{name!r}] must be a string, got {sha!r}.")
        tests = _tests(json_object(data.get("tests"), "tests"))
    except ValueError as error:
        raise RecordError(f"{DIRECTORY}/{MAP}: {error}") from error
    return Map(commit, {p: str(s) for p, s in files.items()}, tests)


def _read(root: Path, path: Path) -> dict[str, object]:
    try:
        return versioned(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise RecordError(f"{path.relative_to(root).as_posix()}: {error}") from error


def _strings(data: object, name: str) -> list[str]:
    if not isinstance(data, list) or not all(isinstance(p, str) for p in data):
        raise ValueError(f"{name} must be a list of paths.")
    return [str(p) for p in data]


def _tests(data: dict[str, object]) -> dict[str, Lines]:
    tests: dict[str, Lines] = {}
    for test, ran in data.items():
        found: dict[str, frozenset[int]] = {}
        for path, lines in json_object(ran, f"tests[{test!r}]").items():
            valid = isinstance(lines, list) and all(
                isinstance(n, int) and not isinstance(n, bool) for n in lines
            )
            if not valid:
                raise ValueError(f"tests[{test!r}][{path!r}] must be a list of line numbers.")
            found[path] = frozenset(lines)
        tests[test] = found
    return tests


def affected(root: Path, since: str | None = None) -> Affected:
    """The tests that ran code changed since `since`, by default the map's commit. Raises
    RecordError for a malformed map."""
    found = load(root)
    if found is None:
        return Affected(None, everything=NO_MAP)
    base = since or found.commit
    if base is None:
        return Affected(None, everything="the map records no git commit; pass --since REF")
    label = since or f"{base[:12]}, the map's commit"
    try:
        diff = changes.changes(root, base)
        changed = changes.paths(root, base)
        return _select(root, found, base, label, diff.hunks, changed)
    except GitError as error:
        return Affected(None, everything=str(error))


def _select(
    root: Path, found: Map, base: str, label: str, hunks: Iterable[Hunk], changed: list[str]
) -> Affected:
    changed = [p for p in changed if _searched(root, p)]
    for path in changed:
        name = PurePosixPath(path).name
        if name in CONFIGURATION:
            return Affected(label, everything=f"{path} changed, which can change every test")
        if not name.endswith(".py"):
            return Affected(label, everything=f"{path} changed, and it is not Python")
    listed = set(changed)
    current = {p: _file_digest(root / p) for p in found.files}
    moved = sorted(p for p, sha in found.files.items() if current[p] != sha)
    bases: dict[str, bytes | None] = {}
    stale = [p for p in moved if p not in listed]
    for path in (p for p in moved if p in listed):
        bases[path] = changes.blob(root, base, path)
        old = bases[path]
        if old is None or digest(old) != found.files[path]:
            stale.append(path)
    if stale:
        shown = ", ".join(sorted(stale)[:3]) + (", ..." if len(stale) > 3 else "")
        return Affected(
            label,
            everything=f"map is stale: {len(stale)} {_files(len(stale))} changed since it was "
            f"built that the diff against {label} doesn't show ({shown}); rebuild it with "
            "pytest --xpt-map",
        )
    return _picks(root, found, label, moved, bases, hunks, changed)


def _picks(
    root: Path,
    found: Map,
    label: str,
    moved: list[str],
    bases: Mapping[str, bytes | None],
    hunks: Iterable[Hunk],
    changed: list[str],
) -> Affected:
    runners: dict[str, list[str]] = defaultdict(list)
    for test, ran in sorted(found.tests.items()):
        for path in ran:
            runners[path].append(test)
    homes = {_home(t) for t in found.tests}
    by_path: dict[str, list[Hunk]] = defaultdict(list)
    for h in hunks:
        by_path[h.path].append(h)
    picks: dict[str, str] = {}

    def pick(test: str, reason: str) -> None:
        if test not in picks and (root / _home(test)).is_file():
            picks[test] = reason

    for path in moved:
        if not (root / path).is_file():
            for test in runners[path]:
                pick(test, f"ran {path}, which was deleted")
            continue
        if path in homes:
            pick(path, "test file changed")
        old_tree, new_tree = _parse(bases.get(path)), changes.parse_source(root / path)
        before = _functions(old_tree) if old_tree is not None else {}
        for h in by_path[path]:
            if not any(_is_code(text) for text in (*h.removed, *h.added)):
                continue
            names = _changed_functions(h, old_tree, new_tree)
            if names is None:
                if not runners[path]:
                    return Affected(
                        label,
                        everything=f"{path}:{h.start} changed code that runs at import, and no "
                        f"test in the map ran a line of {path}",
                    )
                for test in runners[path]:
                    pick(test, f"ran {path}, whose import-time code changed at line {h.start}")
                continue
            for name in names:
                start, end = before.get(name, (0, -1))
                ran = frozenset(range(start, end + 1)) if name in before else _old_lines(h)
                for test in runners[path]:
                    if found.tests[test][path] & ran:
                        pick(test, f"ran {path}:{name}, which changed")
    for path in changed:
        if _is_test_file(path) and path not in homes and (root / path).is_file():
            pick(path, "new test file")
    files = {t for t in picks if "::" not in t}
    kept = (Pick(t, r) for t, r in sorted(picks.items()) if "::" not in t or _home(t) not in files)
    return Affected(label, tuple(kept))


def _searched(root: Path, path: str) -> bool:
    """Whether `path` is outside hidden directories, caches, and virtual environments, which
    hold no project source."""
    parts = PurePosixPath(path).parts[:-1]
    return all(sources.searched(root.joinpath(*parts[: n + 1])) for n in range(len(parts)))


def _home(test: str) -> str:
    return test.split("::", 1)[0]


def _files(count: int) -> str:
    return "file" if count == 1 else "files"


def _is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch(name, pattern) for pattern in TEST_FILES)


def _parse(data: bytes | None) -> ast.Module | None:
    if data is None:
        return None
    try:
        return ast.parse(data)
    except (SyntaxError, ValueError):
        return None


def _old_lines(h: Hunk) -> frozenset[int]:
    """The base's lines a test ran when it ran this change, for a function the base doesn't
    have: the removed lines, or for an addition the lines on either side of it."""
    if h.removed:
        return frozenset(range(h.old_start, h.old_start + len(h.removed)))
    return frozenset({h.old_start, h.old_start + 1})


def _changed_functions(
    h: Hunk, old_tree: ast.Module | None, new_tree: ast.Module | None
) -> list[str] | None:
    """The qualified names of the functions whose bodies hold the lines of code the change
    removed or added, None when one of them sits outside every function body, so it runs when
    the module or class is defined: a constant, an import, a signature, or a decorator. Blank
    lines and comments never count. A file that doesn't parse counts as import-time code."""
    sides = (
        (old_tree, range(h.old_start, h.old_start + len(h.removed)), h.removed),
        (new_tree, h.lines, h.added),
    )
    names: list[str] = []
    for tree, numbers, texts in sides:
        code = [n for n, text in zip(numbers, texts, strict=False) if _is_code(text)]
        if not code:
            continue
        if tree is None:
            return None
        functions = _functions(tree)
        for n in code:
            around = [
                (start, name) for name, (start, end) in functions.items() if start <= n <= end
            ]
            if not around:
                return None
            name = max(around)[1]
            if name not in names:
                names.append(name)
    return names


def _functions(tree: ast.Module) -> dict[str, tuple[int, int]]:
    """The body of every function by qualified name, as its first and last line."""
    found: dict[str, tuple[int, int]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.body:
                name = f"{prefix}{child.name}"
                found.setdefault(name, (child.body[0].lineno, child.end_lineno or child.lineno))
                visit(child, f"{name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _is_code(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("#")


def lines(result: Affected) -> list[str]:
    if result.everything is not None:
        return [f"Run every test: {result.everything}."]
    if not result.tests:
        return [f"No test ran the code changed since {result.since}."]
    count = len(result.tests)
    width = max(len(p.test) for p in result.tests)
    return [
        f"{count} {'test' if count == 1 else 'tests'} ran the code changed since {result.since}:",
        *(f"  {p.test.ljust(width)}  {p.reason}" for p in result.tests),
    ]


def to_json(result: Affected) -> dict[str, Json]:
    return {
        "since": result.since,
        "all": result.everything is not None,
        "reason": result.everything,
        "tests": [{"id": p.test, "reason": p.reason} for p in result.tests],
    }
