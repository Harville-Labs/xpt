"""Record which project lines each test ran, and store them with the run.

`Tracer` records the lines of the project's own `.py` files (see `sources.project_file`) into
the current context, which `begin` and `end` switch. On Python 3.12 and later it uses
`sys.monitoring`: a PY_START event turns on LINE events for each project code object, and
each line event returns DISABLE, so a line costs one callback per context. `begin` calls
`sys.monitoring.restart_events()` to turn the lines back on. On 3.11 it uses `sys.settrace`
and traces only frames of project code.

Lines of code that is not a function, a module or class body, run once at import, often
outside any test. They are also kept as definitions, and a test counts the definitions of
every file it ran a line in, so a changed constant or signature still shows.

With `session=True` the tracer also keeps every line it saw in any context, for `xpt coverage`.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType, FrameType
from typing import Any

from xpt import sources
from xpt.record import (
    DIRECTORY,
    SCHEMA_VERSION,
    RecordError,
    json_object,
    run_id,
    state_directory,
    versioned,
)

Lines = Mapping[str, frozenset[int]]
"""1-based line numbers by project-relative path."""

SUFFIX = ".trace.json"
_WORKER = re.compile(r"[A-Za-z0-9_-]+")
_Hits = set[tuple[str, int]]
_MONITORING: Any = getattr(sys, "monitoring", None)


class TraceError(Exception):
    pass


@dataclass(slots=True)
class Tracer:
    """Records the project lines that run, by context, while started."""

    root: Path
    session: bool = False
    _session: _Hits = field(default_factory=set)
    _imported: frozenset[str] = frozenset()
    _context: _Hits = field(default_factory=set)
    _outside: _Hits = field(default_factory=set)
    _definitions: _Hits = field(default_factory=set)
    _project: dict[str, str | None] = field(default_factory=dict)
    _codes: set[CodeType] = field(default_factory=set)
    _bodies: set[CodeType] = field(default_factory=set)
    _tool: int | None = None
    _started: bool = False

    def start(self) -> None:
        """Start recording into the outside context. Raises TraceError when another tool
        holds the tracing hook it needs."""
        if self._started:
            return
        if _MONITORING is not None:
            self._start_monitoring()
        else:
            if sys.gettrace() is not None:
                raise TraceError(
                    "another tracer, such as coverage or a debugger, is running, and Python "
                    "3.11 allows one. Use Python 3.12 or later, or run without it."
                )
            sys.settrace(self._call)
            threading.settrace(self._call)
        if self.session:
            self._imported = self._loaded()
        self._context = self._outside
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if _MONITORING is not None:
            self._stop_monitoring()
        else:
            sys.settrace(None)
            threading.settrace(None)

    def begin(self) -> None:
        """Record into a new context, such as one test."""
        self._context = set()
        if self._tool is not None:
            _MONITORING.restart_events()

    def end(self, *, keep: bool = True) -> dict[str, frozenset[int]]:
        """The lines the context ran, or nothing without `keep`. Lines from now on go to the
        outside context."""
        hits, self._context = self._context, self._outside
        return self._lines(hits) if keep else {}

    def definitions(self) -> dict[str, frozenset[int]]:
        """The lines module and class bodies ran, in any context."""
        return self._lines(self._definitions)

    def everything(self) -> dict[str, frozenset[int]]:
        """Every line that ran while started, with `session`, in any context."""
        return self._lines(self._session)

    def imported(self) -> frozenset[str]:
        """With `session`, the project files whose modules were imported before `start`, so
        their module and class bodies ran untraced."""
        return self._imported

    def _loaded(self) -> frozenset[str]:
        found = set()
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if isinstance(filename, str) and (path := self._path(filename)) is not None:
                found.add(path)
        return frozenset(found)

    def _lines(self, hits: _Hits) -> dict[str, frozenset[int]]:
        found: dict[str, set[int]] = defaultdict(set)
        for filename, line in hits:
            if (path := self._path(filename)) is not None:
                found[path].add(line)
        return {path: frozenset(lines) for path, lines in sorted(found.items())}

    def _path(self, filename: str) -> str | None:
        if filename not in self._project:
            self._project[filename] = sources.project_file(self.root, filename)
        return self._project[filename]

    def _start_monitoring(self) -> None:
        monitoring = _MONITORING
        free = [t for t in (monitoring.COVERAGE_ID, 4, 3) if monitoring.get_tool(t) is None]
        if not free:
            raise TraceError("every sys.monitoring tool id xpt can use is taken.")
        self._tool = tool = free[0]
        monitoring.use_tool_id(tool, "xpt")
        monitoring.register_callback(tool, monitoring.events.PY_START, self._py_start)
        monitoring.register_callback(tool, monitoring.events.LINE, self._line)
        monitoring.set_events(tool, monitoring.events.PY_START)

    def _stop_monitoring(self) -> None:
        monitoring, tool = _MONITORING, self._tool
        if tool is None:
            return
        self._tool = None
        monitoring.set_events(tool, 0)
        for code in self._codes:
            monitoring.set_local_events(tool, code, 0)
        monitoring.register_callback(tool, monitoring.events.PY_START, None)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.free_tool_id(tool)
        self._codes.clear()
        self._bodies.clear()

    def _py_start(self, code: CodeType, offset: int) -> Any:
        if (
            code not in self._codes
            and self._tool is not None
            and self._path(code.co_filename) is not None
        ):
            self._codes.add(code)
            if not code.co_flags & inspect.CO_OPTIMIZED:
                self._bodies.add(code)
            _MONITORING.set_local_events(self._tool, code, _MONITORING.events.LINE)
        return _MONITORING.DISABLE

    def _line(self, code: CodeType, line: int) -> Any:
        self._context.add((code.co_filename, line))
        if self.session:
            self._session.add((code.co_filename, line))
        if code in self._bodies:
            self._definitions.add((code.co_filename, line))
        return _MONITORING.DISABLE

    def _call(self, frame: FrameType, event: str, arg: object) -> Any:
        code = frame.f_code
        if event != "call" or self._path(code.co_filename) is None:
            return None
        return self._body_line if not code.co_flags & inspect.CO_OPTIMIZED else self._local_line

    def _local_line(self, frame: FrameType, event: str, arg: object) -> Any:
        if event == "line":
            hit = (frame.f_code.co_filename, frame.f_lineno)
            self._context.add(hit)
            if self.session:
                self._session.add(hit)
        return self._local_line

    def _body_line(self, frame: FrameType, event: str, arg: object) -> Any:
        if event == "line":
            hit = (frame.f_code.co_filename, frame.f_lineno)
            self._context.add(hit)
            self._definitions.add(hit)
            if self.session:
                self._session.add(hit)
        return self._body_line


@dataclass(frozen=True, slots=True)
class Trace:
    """The lines each test of a run ran. `files` holds the sha256 of each file a test ran
    when the run ended, so a reader can tell whether the line numbers still hold."""

    files: Mapping[str, str]
    tests: Mapping[str, Lines]

    def ran(self, tests: Iterable[str]) -> Lines | None:
        """Every line any of `tests` ran, None when the trace has none of them."""
        found: dict[str, set[int]] = defaultdict(set)
        known = False
        for test in tests:
            if test in self.tests:
                known = True
                for path, lines in self.tests[test].items():
                    found[path] |= lines
        return {p: frozenset(ls) for p, ls in found.items()} if known else None


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write(
    root: Path, run: str, worker: str, tests: Mapping[str, Lines], definitions: Lines
) -> None:
    """Store the lines of `tests` in `.xpt/runs/<run>/<worker>.trace.json`, each test with
    the definitions of the files it ran."""
    paths = sorted({p for lines in tests.values() for p in lines})
    files = {p: sha for p in paths if (sha := file_sha(root / p)) is not None}
    data = {
        "schema_version": SCHEMA_VERSION,
        "files": files,
        "tests": {
            test: {p: sorted(lines | definitions.get(p, frozenset())) for p, lines in ran.items()}
            for test, ran in sorted(tests.items())
        },
    }
    run_dir = state_directory(root) / "runs" / run_id(run)
    run_dir.mkdir(parents=True, exist_ok=True)
    name = worker if _WORKER.fullmatch(worker) else "main"
    temp = run_dir / f"{name}{SUFFIX}.tmp"
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(run_dir / f"{name}{SUFFIX}")


def load(root: Path, run: str) -> Trace | None:
    """The run's trace from every worker, None when no process stored one."""
    run_dir = root / DIRECTORY / "runs" / run_id(run)
    stored = sorted(run_dir.glob(f"*{SUFFIX}"))
    if not stored:
        return None
    files: dict[str, str] = {}
    tests: dict[str, Lines] = {}
    for path in stored:
        shown = path.relative_to(root).as_posix()
        try:
            data = versioned(json.loads(path.read_text(encoding="utf-8")))
            files |= _files(json_object(data.get("files"), "files"))
            tests |= _tests(json_object(data.get("tests"), "tests"))
        except (OSError, ValueError) as error:
            raise RecordError(f"{shown}: {error}") from error
    return Trace(files, tests)


def _files(data: dict[str, object]) -> dict[str, str]:
    for path, sha in data.items():
        if not isinstance(sha, str):
            raise ValueError(f"files[{path!r}] must be a string, got {sha!r}.")
    return {p: str(s) for p, s in data.items()}


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
