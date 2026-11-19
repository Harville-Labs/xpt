from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from xpt import record, report
from xpt.diff import diff
from xpt.expect import ExpectationFailed
from xpt.failure import Compared, Detail, Expected, Raised, Text, Where, where_json
from xpt.serialize import UnsupportedValue, serialize
from xpt.summary import limits
from xpt.terminal import AgentReporter, location
from xpt.value import Value

MODES = ("xpt", "pytest")
_COMPARED = pytest.StashKey[tuple[Value, Value] | None]()
_MODE = pytest.StashKey[str]()
_OUTSIDE = {"site-packages", "dist-packages"}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xpt")
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
    return [summary, *report.changes(found, limit=lim.changes, width=lim.width)]


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
    root = item.config.rootpath
    function = None
    frame = _project_frame(excinfo, root)
    if frame is not None:
        found, line, name = frame
        path = found.relative_to(root).as_posix()
        if name != getattr(item, "originalname", item.name):
            function = name
    return Where(path, None if line is None else line + 1, function, _detail(excinfo, compared))


def _project_frame(
    excinfo: pytest.ExceptionInfo[BaseException], root: Path
) -> tuple[Path, int, str] | None:
    """The deepest frame in a file under `root` that is not an installed package."""
    for entry in reversed(excinfo.traceback.filter(excinfo)):
        path = Path(entry.path)
        inside = path.is_absolute() and path.is_relative_to(root)
        if inside and not _OUTSIDE.intersection(path.relative_to(root).parts):
            return path, entry.lineno, entry.name
    return None


def _detail(
    excinfo: pytest.ExceptionInfo[BaseException], compared: tuple[Value, Value] | None
) -> Detail:
    error = excinfo.value
    if isinstance(error, ExpectationFailed):
        return Expected(error.observation)
    if compared is not None and isinstance(error, AssertionError):
        return Compared(*compared)
    kind = type(error)
    name = kind.__qualname__
    if kind.__module__ != "builtins" and not kind.__module__.startswith("_pytest."):
        name = f"{kind.__module__}.{name}"
    message = "\n".join([str(error), *getattr(error, "__notes__", ())])
    return Raised(name, message)


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not hasattr(session.config, "workerinput"):
        record.recorder.finish()
