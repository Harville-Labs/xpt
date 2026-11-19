from __future__ import annotations

import linecache
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from xpt import record, report
from xpt.accept import create
from xpt.diff import diff
from xpt.expect import ExpectationFailed
from xpt.failure import Compared, Detail, Expected, Frame, Raised, Text, Where, where_json
from xpt.serialize import UnsupportedValue, serialize
from xpt.summary import filled_line, limits
from xpt.terminal import CREATED, AgentReporter, location
from xpt.value import Value

MODES = ("xpt", "pytest")
_COMPARED = pytest.StashKey[tuple[Value, Value] | None]()
_MODE = pytest.StashKey[str]()
_OUTSIDE = {"site-packages", "dist-packages"}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xpt")
    group.addoption(
        "--expect-create",
        action="store_true",
        help="After the session, write each missing expectation's recorded value into the "
        "test source, as xpt accept --missing does.",
    )
    group.addoption(
        "--expect-no-record",
        action="store_true",
        help="Do not record failing expect() calls under .xpt/.",
    )
    group.addoption(
        "--xpt-report",
        choices=MODES,
        default=None,
        help="xpt (the default) prints the compact xpt report, pytest prints pytest's own. "
        "XPT_REPORT sets it too.",
    )
    group.addoption(
        "--xpt-json",
        metavar="PATH",
        default=None,
        help="Also write the untruncated xpt report to PATH as JSON.",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    _check_create(config)
    workerinput = getattr(config, "workerinput", None)
    run = workerinput["xpt_run"] if workerinput else record.new_run_id()
    worker = workerinput["workerid"] if workerinput else "main"
    record.recorder.configure(
        config.rootpath, run, worker, record=not config.getoption("expect_no_record")
    )
    mode = _mode(config)
    config.stash[_MODE] = mode
    config.stash[_COMPARED] = None
    if mode == "xpt" and workerinput is None:
        AgentReporter.install(config, config.getoption("xpt_json"))


def _check_create(config: pytest.Config) -> None:
    if not config.getoption("expect_create"):
        return
    if config.getoption("expect_no_record"):
        raise pytest.UsageError("--expect-create needs recording. Drop --expect-no-record.")
    if not record.environment().record:
        raise pytest.UsageError("--expect-create needs recording. Unset XPT_NO_RECORD.")


def _mode(config: pytest.Config) -> str:
    mode = config.getoption("xpt_report") or record.environment().report or "xpt"
    if mode not in MODES:
        raise pytest.UsageError(f"XPT_REPORT must be xpt or pytest, got {mode!r}.")
    if mode == "pytest" and config.getoption("xpt_json"):
        raise pytest.UsageError("--xpt-json needs the xpt report. Drop --xpt-report=pytest.")
    return mode


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    node.workerinput["xpt_run"] = record.recorder.run


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    record.recorder.test = item.nodeid
    try:
        return (yield)
    finally:
        record.recorder.test = None


@pytest.hookimpl(tryfirst=True)
def pytest_assertrepr_compare(
    config: pytest.Config, op: str, left: object, right: object
) -> list[str] | None:
    if op != "==" or config.stash.get(_MODE, None) != "xpt":
        return None
    try:
        a, b = serialize(left), serialize(right)
    except UnsupportedValue:
        return None
    found = diff(a, b)
    if all(not c.path for c in found.changes):
        return None
    config.stash[_COMPARED] = (a, b)
    lim = limits(config.get_verbosity())
    summary = f"{report.show(a, 40)} == {report.show(b, 40)}"
    return [summary, *report.changes(found, limit=lim.changes, width=lim.width, labeled=True)]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    rep = yield
    compared = item.config.stash.get(_COMPARED, None)
    item.config.stash[_COMPARED] = None
    if rep.failed and item.config.stash.get(_MODE, None) == "xpt":
        rep.xpt = where_json(_where(item, call, rep, compared))  # ty: ignore[missing-attribute]
    return rep


def _where(
    item: pytest.Item,
    call: pytest.CallInfo[None],
    rep: pytest.TestReport,
    compared: tuple[Value, Value] | None,
) -> Where:
    path, line = location(rep)
    excinfo = call.excinfo
    if excinfo is None:
        return Where(path, None if line is None else line + 1, None, Text(rep.longreprtext))
    detail = _detail(excinfo, compared)
    cause = _cause(excinfo.value)
    frames = _project_frames(excinfo, item.config.rootpath)
    if not frames:
        return Where(path, None if line is None else line + 1, None, detail, cause=cause)
    test = getattr(item, "originalname", item.name)
    *callers, (path, lineno, name, source) = frames
    chain = tuple(Frame(p, n, None if f == test else f) for p, n, f, _ in callers)
    function = None if name == test else name
    return Where(path, lineno, function, detail, chain, source or None, cause)


def _project_frames(
    excinfo: pytest.ExceptionInfo[BaseException], root: Path
) -> list[tuple[str, int, str, str]]:
    """Frames in files under `root` that are not installed packages, outermost first, as
    (relative path, 1-based line, function, stripped source line)."""
    frames = []
    for entry in excinfo.traceback.filter(excinfo):
        path = Path(entry.path)
        inside = path.is_absolute() and path.is_relative_to(root)
        if inside and not _OUTSIDE.intersection(path.relative_to(root).parts):
            line = entry.lineno + 1
            source = linecache.getline(str(path), line).strip()
            frames.append((path.relative_to(root).as_posix(), line, entry.name, source))
    return frames


def _detail(
    excinfo: pytest.ExceptionInfo[BaseException], compared: tuple[Value, Value] | None
) -> Detail:
    error = excinfo.value
    if isinstance(error, ExpectationFailed):
        return Expected(error.observation)
    if compared is not None and isinstance(error, AssertionError):
        return Compared(*compared)
    message = "\n".join([str(error), *getattr(error, "__notes__", ())])
    return Raised(_name(error), message)


def _cause(error: BaseException) -> str | None:
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if cause is None:
        return None
    message = " ".join(str(cause).split())
    return f"{_name(cause)}: {message}" if message else _name(cause)


def _name(error: BaseException) -> str:
    kind = type(error)
    if kind.__module__ == "builtins" or kind.__module__.startswith("_pytest."):
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if hasattr(config, "workerinput"):
        return
    record.recorder.finish()
    if config.getoption("expect_create"):
        config.stash[CREATED] = create(config.rootpath, record.recorder.run)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    created = config.stash.get(CREATED, None)
    line = None if created is None else filled_line(created)
    if line and config.stash.get(_MODE, None) == "pytest":
        terminalreporter.write_line(line)
