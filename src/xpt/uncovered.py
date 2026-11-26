"""Changed lines that no test ran, for `xpt coverage`.

The changed lines come from `changes.changes`. A changed line counts only when it holds code:
the lines of every code object compiled from the file's source, so blank lines, comments, and
an `else:` never show as not run. Compiling does not import or run anything.

The executed lines come from one of two places:

- `run`: the given pytest command, with `XPT_COVERAGE` naming a temporary directory. The
  pytest plugin then traces every project line of the session with `trace.Tracer` and each
  process, the controller and every xdist worker, writes one `<pid>-<random>.json` file there.
- `read_coverage_py`: a `.coverage` data file that coverage.py wrote, read with `sqlite3`.
  coverage.py is not imported, so it need not be installed.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import types
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xpt import sources
from xpt.changes import Changes, function_at
from xpt.record import SCHEMA_VERSION, json_object, versioned
from xpt.report import MAX_CHANGES, MAX_CHARS, cut
from xpt.value import Json

ENV = "XPT_COVERAGE"
Source = Literal["xpt", "coverage.py"]


class CoverageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Executed:
    """The lines that ran, by project-relative path. `imported` holds files whose modules
    were imported before tracing started, so their module and class bodies count as run.
    `measured` is the files the data covers, None when it covers every project file."""

    lines: Mapping[str, frozenset[int]]
    imported: frozenset[str] = frozenset()
    measured: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """A changed file's changed lines that hold code, with what grouping needs."""

    path: str
    lines: frozenset[int]
    bodies: frozenset[int]
    text: tuple[str, ...]
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class Function:
    """The changed lines with code in one function, None at module level."""

    path: str
    name: str | None
    changed: tuple[int, ...]
    not_run: tuple[int, ...]
    source: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class Coverage:
    """What `xpt coverage` reports. `tests` is pytest's exit code, None when no tests ran.
    `stale` lists changed files edited after the coverage data was written, `unmeasured` the
    changed files the coverage data does not cover; both are left out of `functions`."""

    since: str
    source: Source
    functions: tuple[Function, ...]
    tests: int | None = None
    stale: tuple[str, ...] = ()
    unmeasured: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        return sum(len(f.changed) for f in self.functions)

    @property
    def not_run(self) -> int:
        return sum(len(f.not_run) for f in self.functions)


def changed_files(root: Path, diff: Changes) -> list[ChangedFile]:
    """The changed lines of each file in `diff` that hold code. Files that can't be read or
    compiled are left out."""
    by_path: dict[str, set[int]] = {}
    for h in diff.hunks:
        by_path.setdefault(h.path, set()).update(h.lines)
    found = []
    for path, changed in sorted(by_path.items()):
        try:
            text = (root / path).read_text(encoding="utf-8")
            tree = ast.parse(text)
            code = compile(tree, path, "exec", dont_inherit=True)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        lines, bodies = executable(code)
        if mine := frozenset(changed & lines):
            found.append(ChangedFile(path, mine, bodies, tuple(text.split("\n")), tree))
    return found


def executable(code: types.CodeType) -> tuple[frozenset[int], frozenset[int]]:
    """The lines with bytecode in `code` and the code compiled inside it, and among them the
    lines of module and class bodies, which run at import."""
    lines: set[int] = set()
    bodies: set[int] = set()
    stack = [code]
    while stack:
        current = stack.pop()
        found = {n for start, end, n in current.co_lines() if n and start != end}
        lines |= found
        if not current.co_flags & inspect.CO_OPTIMIZED:
            bodies |= found
        stack += [c for c in current.co_consts if isinstance(c, types.CodeType)]
    return frozenset(lines), frozenset(bodies)


def report(
    files: Sequence[ChangedFile],
    executed: Executed,
    since: str,
    source: Source,
    *,
    tests: int | None = None,
    stale: Iterable[str] = (),
) -> Coverage:
    left_out = frozenset(stale)
    unmeasured = []
    functions: list[Function] = []
    for f in files:
        if executed.measured is not None and f.path not in executed.measured:
            unmeasured.append(f.path)
            continue
        if f.path in left_out:
            continue
        ran = executed.lines.get(f.path, frozenset())
        if f.path in executed.imported:
            ran |= f.bodies
        grouped: dict[str | None, list[int]] = {}
        for line in sorted(f.lines):
            grouped.setdefault(function_at(f.tree, line), []).append(line)
        for name, changed in grouped.items():
            missed = tuple(n for n in changed if n not in ran)
            text = {n: f.text[n - 1] for n in missed if n <= len(f.text)}
            functions.append(Function(f.path, name, tuple(changed), missed, text))
    return Coverage(
        since,
        source,
        tuple(functions),
        tests,
        tuple(sorted(left_out - set(unmeasured))),
        tuple(unmeasured),
    )


def write(
    directory: Path, root: Path, lines: Mapping[str, frozenset[int]], imported: Iterable[str]
) -> None:
    """Store one process's executed lines in `directory` for `load`."""
    data = {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "lines": {path: sorted(found) for path, found in sorted(lines.items())},
        "imported": sorted(imported),
    }
    name = f"{os.getpid()}-{secrets.token_hex(4)}"
    temp = directory / f"{name}.tmp"
    temp.write_text(json.dumps(data), encoding="utf-8")
    temp.replace(directory / f"{name}.json")


def load(directory: Path, root: Path) -> Executed | None:
    """Every process's executed lines from `directory`, as paths relative to `root`, None
    when no process wrote any. Lines outside `root` are left out."""
    stored = sorted(directory.glob("*.json"))
    if not stored:
        return None
    lines: dict[str, set[int]] = {}
    imported: set[str] = set()
    for path in stored:
        try:
            data = versioned(json.loads(path.read_text(encoding="utf-8")))
            base = Path(_string(data.get("root"), "root"))
            for name, found in json_object(data.get("lines"), "lines").items():
                if (relative := sources.project_file(root, str(base / name))) is not None:
                    lines.setdefault(relative, set()).update(_numbers(found, name))
            for name in _strings(data.get("imported"), "imported"):
                if (relative := sources.project_file(root, str(base / name))) is not None:
                    imported.add(relative)
        except (OSError, ValueError) as error:
            raise CoverageError(f"the traced lines in {path.name} are malformed: {error}") from None
    return Executed({p: frozenset(ls) for p, ls in lines.items()}, frozenset(imported))


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {value!r}.")
    return value


def _strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of strings.")
    return [str(v) for v in value]


def _numbers(value: object, name: str) -> list[int]:
    if not isinstance(value, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) for n in value
    ):
        raise ValueError(f"lines[{name!r}] must be a list of line numbers.")
    return [int(n) for n in value]


def run(command: Sequence[str], root: Path) -> tuple[int, Executed]:
    """Run `command` from `root` with the session traced, its output on stderr. Returns its
    exit code and the lines it ran."""
    with tempfile.TemporaryDirectory(prefix="xpt-coverage-") as directory:
        env = os.environ | {ENV: directory}
        try:
            child = subprocess.Popen(
                list(command), cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
        except OSError as error:
            raise CoverageError(f"can't run {command[0]}: {error.strerror or error}.") from None
        assert child.stdout is not None
        for line in child.stdout:
            sys.stderr.write(line.decode("utf-8", "replace"))
            sys.stderr.flush()
        code = child.wait()
        executed = load(Path(directory), root)
    if executed is None:
        raise CoverageError(
            f"{command[0]} exited {code} and traced no lines. xpt coverage needs pytest with "
            "the xpt plugin, as in xpt coverage -- pytest, and PYTEST_DISABLE_PLUGIN_AUTOLOAD "
            "unset."
        )
    return code, executed


def read_coverage_py(path: Path, root: Path) -> Executed:
    """The executed lines in a coverage.py data file, from its line bits or, when it measured
    branches, from its arcs. Contexts are merged."""
    if not path.is_file():
        raise CoverageError(f"{path} does not exist. Run coverage run -m pytest first.")
    lines: dict[int, set[int]] = {}
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            files = dict(db.execute("SELECT id, path FROM file").fetchall())
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
            if "line_bits" in tables:
                for file_id, bits in db.execute("SELECT file_id, numbits FROM line_bits"):
                    lines.setdefault(file_id, set()).update(_numbits(bits))
            if "arc" in tables:
                for file_id, start, end in db.execute("SELECT file_id, fromno, tono FROM arc"):
                    lines.setdefault(file_id, set()).update(n for n in (start, end) if n > 0)
        finally:
            db.close()
    except sqlite3.Error as error:
        raise CoverageError(f"{path} is not a coverage.py data file: {error}.") from None
    found: dict[str, frozenset[int]] = {}
    measured = set()
    for file_id, name in files.items():
        absolute = Path(name) if Path(name).is_absolute() else root / name
        if (relative := sources.project_file(root, str(absolute))) is not None:
            measured.add(relative)
            found[relative] = frozenset(lines.get(file_id, ()))
    return Executed(found, measured=frozenset(measured))


def _numbits(bits: bytes) -> Iterable[int]:
    """The line numbers in coverage.py's numbits: bit `i` of byte `b` is line `8 * b + i`."""
    for index, byte in enumerate(bits):
        for bit in range(8):
            if byte & (1 << bit):
                yield index * 8 + bit


def edited_after(root: Path, files: Iterable[ChangedFile], written: float) -> list[str]:
    """The files modified after `written`, a timestamp, whose line numbers may have moved."""
    stale = []
    for f in files:
        try:
            if (root / f.path).stat().st_mtime > written:
                stale.append(f.path)
        except OSError:
            continue
    return stale


def lines(c: Coverage, *, full: bool = False) -> list[str]:
    """Each function with changed lines that did not run, then a summary. Each function
    shows MAX_CHANGES lines of MAX_CHARS unless `full`."""
    limit, width = (None, None) if full else (MAX_CHANGES, MAX_CHARS)
    out = []
    for f in c.functions:
        if not f.not_run:
            continue
        out.append(
            f"{f.path} {f.name or '<module>'}: {len(f.not_run)} of "
            f"{_count(len(f.changed), 'changed line')} not run"
        )
        shown = f.not_run if limit is None else f.not_run[:limit]
        digits = len(str(max(f.not_run)))
        out += [f"  {n:>{digits}} | {cut(f.source.get(n, '').rstrip(), width)}" for n in shown]
        if hidden := len(f.not_run) - len(shown):
            out.append(f"  ... {_count(hidden, 'more line')}")
    if c.stale:
        out.append(f"changed since the coverage data, left out: {', '.join(c.stale)}")
    if c.unmeasured:
        out.append(f"not in the coverage data, left out: {', '.join(c.unmeasured)}")
    if c.tests == 1:
        out.append("pytest exited 1: tests failed, and a failing test may stop before its lines.")
    elif c.tests:
        out.append(f"pytest exited {c.tests}.")
    return [*out, _summary(c)]


def _summary(c: Coverage) -> str:
    since = f"(since {c.since})"
    if not c.changed:
        return f"No changed lines with code {since}."
    if not c.not_run:
        return f"All changed lines ran: {_count(c.changed, 'line')} {since}."
    functions = len([f for f in c.functions if f.not_run])
    return (
        f"{c.not_run} of {_count(c.changed, 'changed line')} not run, in "
        f"{_count(functions, 'function')} {since}."
    )


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def to_json(c: Coverage) -> dict[str, Json]:
    return {
        "since": c.since,
        "source": c.source,
        "tests": c.tests,
        "changed": c.changed,
        "not_run": c.not_run,
        "functions": [
            {
                "path": f.path,
                "function": f.name,
                "changed": list(f.changed),
                "not_run": [{"line": n, "text": f.source.get(n, "")} for n in f.not_run],
            }
            for f in c.functions
        ],
        "stale": list(c.stale),
        "unmeasured": list(c.unmeasured),
    }
