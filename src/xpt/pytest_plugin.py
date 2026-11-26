from __future__ import annotations

import linecache
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from xpt import affected, changes, decisions, flaky, frames, record, report, trace, uncovered
from xpt.accept import Accepted, after_session
from xpt.deterministic import Pinned, deterministic
from xpt.diff import diff
from xpt.expect import ExpectationFailed
from xpt.failure import Compared, Detail, Expected, Frame, Raised, Text, Where, where_json
from xpt.group import Group, group, missing, pick
from xpt.serialize import UnsupportedValue, serialize
from xpt.summary import after_lines, limits
from xpt.terminal import ACCEPTED, CREATED, AgentReporter, location, stock_reason
from xpt.value import Value

MODES = ("xpt", "pytest")
_COMPARED = pytest.StashKey[tuple[Value, Value] | None]()
_MODE = pytest.StashKey[str]()
_TRACER = pytest.StashKey[trace.Tracer]()
_TRACED = pytest.StashKey[dict[str, trace.Lines]]()
_COVERAGE = pytest.StashKey[Path]()
_SESSION: list[trace.Tracer] = []
"""The tracer `xpt coverage` started in this process, so a pytest run inside it, such as an
inline pytester run, does not start a second one."""
_MAPPED = pytest.StashKey[dict[str, trace.Lines]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("xpt")
    group.addoption(
        "--expect-create",
        action="store_true",
        help="After the session, write each missing expectation's recorded value into the "
        "test source, as xpt accept --missing does.",
    )
    group.addoption(
        "--expect-accept",
        action="append",
        metavar="ID[,ID...]",
        help="After the session, write the actual values of these call sites into the test "
        "source, as xpt accept ID does. Repeat it or separate ids with commas.",
    )
    group.addoption(
        "--expect-no-record",
        action="store_true",
        help="Do not record failing expect() calls under .xpt/.",
    )
    group.addoption(
        "--xpt-trace",
        action="store_true",
        help="Record the project lines each failing test ran, so xpt show and xpt accept can "
        "print the changed code it ran.",
    )
    group.addoption(
        "--xpt-map",
        action="store_true",
        help="Record the project lines every test ran into .xpt/map.json, so xpt affected can "
        "select the tests that ran changed code. Build it from a full run.",
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
    group.addoption(
        "--xpt-deterministic",
        action="store_true",
        help="Pin the clock, random, and uuid for every test, as the xpt_deterministic "
        "fixture does.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Under `xpt coverage`, start tracing every project line before conftest files import
    the project, so module bodies count."""
    directory = os.environ.get(uncovered.ENV)
    if not directory or _SESSION:
        return
    tracer = trace.Tracer(early_config.rootpath, session=True)
    try:
        tracer.start()
    except trace.TraceError as error:
        raise pytest.UsageError(f"xpt coverage can't trace: {error}") from None
    _SESSION.append(tracer)
    early_config.stash[_TRACER] = tracer
    early_config.stash[_COVERAGE] = Path(directory)
    early_config.add_cleanup(_SESSION.clear)
    early_config.add_cleanup(tracer.stop)


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    _check_writes(config)
    workerinput = getattr(config, "workerinput", None)
    run = workerinput["xpt_run"] if workerinput else record.new_run_id()
    worker = workerinput["workerid"] if workerinput else "main"
    record.recorder.configure(
        config.rootpath, run, worker, record=not config.getoption("expect_no_record")
    )
    if config.getoption("xpt_trace") or config.getoption("xpt_map"):
        _start_trace(config)
    if config.getoption("xpt_map"):
        config.stash[_MAPPED] = {}
    mode = _mode(config)
    config.stash[_MODE] = mode
    config.stash[_COMPARED] = None
    if mode != "xpt" or workerinput is not None:
        return
    reason = stock_reason(config)
    if reason is None:
        AgentReporter.install(config, config.getoption("xpt_json"))
    elif config.getoption("xpt_json"):
        raise pytest.UsageError(f"--xpt-json needs the xpt report, but {reason}.")


def _start_trace(config: pytest.Config) -> None:
    config.stash[_TRACED] = {}
    if _TRACER in config.stash:
        return
    tracer = trace.Tracer(config.rootpath)
    try:
        tracer.start()
    except trace.TraceError as error:
        flag = "--xpt-trace" if config.getoption("xpt_trace") else "--xpt-map"
        raise pytest.UsageError(f"{flag} can't start: {error}") from None
    config.stash[_TRACER] = tracer
    config.add_cleanup(tracer.stop)


def _finish_trace(config: pytest.Config, exitstatus: int) -> None:
    """Stop tracing, store the lines of each test that recorded an observation, under
    `xpt coverage` every line the session ran, and with --xpt-map every test's lines. The
    controller joins every process's map once its workers are done, and keeps it only when
    the session ran to the end."""
    tracer = config.stash.get(_TRACER, None)
    if tracer is None:
        return
    tracer.stop()
    traced = config.stash.get(_TRACED, None)
    rec = record.recorder
    if traced and rec.enabled:
        trace.write(rec.root, rec.run, rec.worker, traced, tracer.definitions())
    directory = config.stash.get(_COVERAGE, None)
    if directory is not None:
        uncovered.write(directory, config.rootpath, tracer.everything(), tracer.imported())
    mapped = config.stash.get(_MAPPED, None)
    if mapped is None:
        return
    root = config.rootpath
    affected.write_part(root, rec.run, rec.worker, mapped, tracer.definitions())
    if not hasattr(config, "workerinput"):
        complete = exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED)
        affected.merge(root, rec.run, changes.head(root), keep=complete)


def _groups(root: Path, run: str) -> tuple[Group, ...]:
    if run not in record.run_ids(root):
        return ()
    try:
        rejected = decisions.load_rejections(root)
    except record.RecordError:
        rejected = frozenset()
    groups = decisions.mark(group(record.load_run(root, run)), rejected)
    return flaky.mark(groups, flaky.find(flaky.load_history(root), run))


def _accept_ids(config: pytest.Config) -> tuple[str, ...]:
    given = config.getoption("expect_accept") or []
    return tuple(i for value in given for part in value.split(",") if (i := part.strip()))


def _check_writes(config: pytest.Config) -> None:
    accepting = config.getoption("expect_accept") is not None
    if accepting and not _accept_ids(config):
        raise pytest.UsageError(
            "--expect-accept needs call site ids, as in --expect-accept=b211,ee17. "
            "The report prints each id beside its diff."
        )
    asked = {
        "--expect-create": config.getoption("expect_create"),
        "--expect-accept": accepting,
        "--xpt-trace": config.getoption("xpt_trace"),
    }
    for flag in (f for f, on in asked.items() if on):
        if config.getoption("expect_no_record"):
            raise pytest.UsageError(f"{flag} needs recording. Drop --expect-no-record.")
        if not record.environment().record:
            raise pytest.UsageError(f"{flag} needs recording. Unset XPT_NO_RECORD.")


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
    test = record.base_nodeid(item.nodeid)
    record.recorder.test = test
    tracer = item.config.stash.get(_TRACER, None)
    if _TRACED not in item.config.stash:
        tracer = None
    if tracer is not None:
        tracer.begin()
    try:
        return (yield)
    finally:
        record.recorder.test = None
        if tracer is not None:
            recorded = test in record.recorder.tests
            mapped = item.config.stash.get(_MAPPED, None)
            lines = tracer.end(keep=recorded or mapped is not None)
            if recorded:
                item.config.stash[_TRACED][test] = lines
            if mapped is not None:
                mapped[test] = lines


@pytest.fixture
def xpt_deterministic(request: pytest.FixtureRequest) -> Generator[Pinned, None, None]:
    """Pin time, random, and uuid for this test. Call `.advance(seconds)` to move the clock.

    The patches go through the test's `monkeypatch`, so they unwind in order with the test's
    own patches whichever fixture comes first."""
    monkeypatch = request.getfixturevalue("monkeypatch")
    root = request.config.rootpath
    with deterministic(request.node.nodeid, root=root, patcher=monkeypatch) as pinned:
        yield pinned


@pytest.fixture(autouse=True)
def _xpt_deterministic_everywhere(request: pytest.FixtureRequest) -> None:
    if request.config.getoption("xpt_deterministic"):
        request.getfixturevalue("xpt_deterministic")


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
    if all(not c.path and report.line_diff(c) is None for c in found.changes):
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
    cause = frames.cause(excinfo.value)
    found = _project_frames(excinfo, item.config.rootpath)
    if not found:
        return Where(path, None if line is None else line + 1, None, detail, cause=cause)
    test = getattr(item, "originalname", item.name)
    *callers, (path, lineno, name, source) = found
    chain = tuple(Frame(p, n, None if f == test else f) for p, n, f, _ in callers)
    function = None if name == test else name
    return Where(path, lineno, function, detail, chain, source or None, cause)


def _project_frames(
    excinfo: pytest.ExceptionInfo[BaseException], root: Path
) -> list[tuple[str, int, str, str]]:
    """Frames in project files under `root`, outermost first, as (relative path, 1-based
    line, function, stripped source line)."""
    found = []
    for entry in excinfo.traceback.filter(excinfo):
        path = Path(entry.path)
        relative = frames.project_path(path, root)
        if relative is not None:
            line = entry.lineno + 1
            source = linecache.getline(str(path), line).strip()
            found.append((relative, line, entry.name, source))
    return found


def _detail(
    excinfo: pytest.ExceptionInfo[BaseException], compared: tuple[Value, Value] | None
) -> Detail:
    error = excinfo.value
    if isinstance(error, ExpectationFailed):
        return Expected(error.observation)
    if compared is not None and isinstance(error, AssertionError):
        return Compared(*compared)
    return Raised(frames.name(error), frames.message(error))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    _finish_trace(config, exitstatus)
    if hasattr(config, "workerinput"):
        return
    record.recorder.finish(source=True)
    ids = _accept_ids(config)
    create = config.getoption("expect_create")
    if not ids and not create:
        return
    root = config.rootpath
    groups = _groups(root, record.recorder.run)
    if ids:
        chosen, unknown = pick(groups, ids)
        config.stash[ACCEPTED] = Accepted(after_session(root, chosen), unknown)
    if create:
        unnamed = [g for g in missing(groups) if g.id not in ids]
        config.stash[CREATED] = after_session(root, unnamed)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    if isinstance(terminalreporter, AgentReporter):
        return
    created = config.stash.get(CREATED, None)
    accepted = config.stash.get(ACCEPTED, None)
    for line in after_lines(created, accepted):
        terminalreporter.write_line(line)
