from __future__ import annotations

import json
import re
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from xpt import decisions, record
from xpt.accept import AcceptResult
from xpt.failure import Failure, Text, Where, streams, where_from_json
from xpt.summary import Session, limits, report_json, report_lines

try:
    from pytest import TerminalReporter
except ImportError:  # pytest exports it from 8.4; older releases only have the private path.
    from _pytest.terminal import TerminalReporter

CREATED = pytest.StashKey[AcceptResult]()

ISSUES = "https://github.com/Harville-Labs/xpt/issues"
_NOT_A_TEST_RUN = (
    "collectonly",
    "showfixtures",
    "show_fixtures_per_test",
    "markers",
    "setuponly",
    "setupplan",
    "help",
    "version",
)
_DEFAULT_REPORTCHARS = "fE"
OLDEST_PYTEST = (8, 0)


def stock_reason(config: pytest.Config) -> str | None:
    """Why this run keeps pytest's own report, or None when the xpt report replaces it.

    The xpt report replaces pytest's failure sections, so an option that asks pytest for a
    particular form of them keeps pytest's report for the run instead of being ignored.
    """
    option = config.option
    for name in _NOT_A_TEST_RUN:
        if getattr(option, name, False):
            return f"{name} does not run tests"
    if getattr(option, "tbstyle", "auto") != "auto":
        return "--tb chooses pytest's traceback style"
    if getattr(option, "showlocals", False):
        return "--showlocals asks for pytest's tracebacks"
    if getattr(option, "fulltrace", False):
        return "--full-trace asks for pytest's tracebacks"
    return None


def unsupported_pytest() -> str | None:
    if tuple(getattr(pytest, "version_tuple", (0, 0))[:2]) >= OLDEST_PYTEST:
        return None
    return (
        f"xpt: the xpt report needs pytest {OLDEST_PYTEST[0]}.{OLDEST_PYTEST[1]} or later and "
        f"this is pytest {pytest.__version__}, so this is pytest's report. "
        "--xpt-report=pytest hides this line."
    )


def notice(error: BaseException) -> str:
    return (
        f"xpt: the xpt report failed ({type(error).__name__}: {error}), so this is pytest's "
        f"report. Please report it at {ISSUES}. --xpt-report=pytest hides this line."
    )


class AgentReporter(TerminalReporter):  # ty: ignore[subclass-of-final-class]
    """pytest's own terminal reporter with its progress, header, failure sections, short
    summary, and final line replaced by the xpt report.

    Every other hook and attribute is pytest's, on the same object pytest registered, so
    plugins that hold the reporter or call it keep working. The overrides are methods pytest
    names publicly; a run where rendering raises prints pytest's sections instead.
    """

    xpt_json: str | None
    xpt_quiet: bool
    xpt_session: pytest.Session | None
    xpt_started: float

    @classmethod
    def install(cls, config: pytest.Config, json_path: str | None) -> None:
        manager = config.pluginmanager
        reporter = manager.get_plugin("terminalreporter")
        if type(reporter) is not TerminalReporter:
            return
        if (unsupported := unsupported_pytest()) is not None:
            reporter.write_line(unsupported)
            return
        try:
            swap(reporter, cls, json_path)
        except Exception as error:
            reporter.__class__ = TerminalReporter
            if not manager.is_registered(reporter):
                manager.register(reporter, "terminalreporter")
            reporter.write_line(notice(error))

    @property
    def showheader(self) -> bool:
        return False

    def pytest_report_header(self, config: pytest.Config) -> list[str]:
        return []

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.xpt_session = session
        self.xpt_started = time.perf_counter()
        super().pytest_sessionstart(session)
        if self.verbosity < 0 or self.no_header:
            return
        hooked = self.config.hook.pytest_report_header(
            config=self.config, start_path=self.startpath
        )
        for lines in reversed(hooked):
            for line in [lines] if isinstance(lines, str) else lines:
                self.write_line(line)

    def pytest_collection(self) -> None:
        pass

    def report_collect(self, final: bool = False) -> None:
        pass

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        if self.verbosity > 0:
            super().pytest_runtest_logstart(nodeid, location)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.xpt_quiet = self.verbosity <= 0
        try:
            super().pytest_runtest_logreport(report)
        finally:
            self.xpt_quiet = False

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        stock = getattr(super(), "pytest_runtest_logfinish", None)
        if stock is not None and self.verbosity > 0:
            stock(nodeid)

    @pytest.hookimpl(wrapper=True)
    def pytest_report_teststatus(self) -> Generator[None, Any, Any]:
        result = yield
        if self.xpt_quiet and result:
            return (result[0], "", "")
        return result

    def summary_errors(self) -> None:
        pass

    def summary_failures(self) -> None:
        pass

    def short_test_summary(self) -> None:
        if self.reportchars_requested():
            super().short_test_summary()

    def reportchars_requested(self) -> bool:
        return self.config.option.reportchars not in (_DEFAULT_REPORTCHARS, "")

    def summary_stats(self) -> None:
        try:
            finished = self._finished()
            lines = report_lines(finished, limits(self.config.get_verbosity()))
        except Exception as error:
            self._fall_back(error)
            return
        for line in lines[-1:] if self.no_summary else lines:
            self.write_line(line)
        if self.xpt_json is not None:
            text = json.dumps(report_json(finished), ensure_ascii=False, indent=2)
            Path(self.xpt_json).write_text(text + "\n", encoding="utf-8")

    def _fall_back(self, error: Exception) -> None:
        if not self.no_summary:
            super().summary_errors()
            super().summary_failures()
            if not self.reportchars_requested():
                super().short_test_summary()
        super().summary_stats()
        self.write_line(notice(error))

    def _finished(self) -> Session:
        reports = [r for key in ("failed", "error") for r in self.stats.get(key, []) if r.failed]
        reports.sort(key=_order)
        parts, _ = self.build_summary_stats_line()
        run = record.recorder.run if record.recorder.enabled else None
        session = self.xpt_session
        return Session(
            failures=tuple(_failure(r) for r in reports),
            counts=tuple(text for text, _ in parts),
            seconds=time.perf_counter() - self.xpt_started,
            exit_code=int(session.exitstatus) if session is not None else 0,
            run=run,
            rejected=self._rejected() if run else frozenset(),
            created=self.config.stash.get(CREATED, None),
        )

    def _rejected(self) -> frozenset[decisions.Rejection]:
        try:
            return decisions.load_rejections(record.recorder.root)
        except record.RecordError as error:
            self.write_line(f"xpt: {error}")
            return frozenset()


def swap(reporter: Any, cls: type[AgentReporter], json_path: str | None) -> None:
    """Turn pytest's registered reporter into `cls` in place and register it again, so the
    new hook methods take effect and plugins already holding the object keep it."""
    manager = reporter.config.pluginmanager
    manager.unregister(reporter)
    reporter.__class__ = cls
    reporter.xpt_json = json_path
    reporter.xpt_quiet = False
    reporter.xpt_session = None
    reporter.xpt_started = time.perf_counter()
    manager.register(reporter, "terminalreporter")


def _failure(rep: Any) -> Failure:
    when = getattr(rep, "when", None) or "collect"
    payload = getattr(rep, "xpt", None)
    if payload is not None:
        where = where_from_json(payload)
    else:
        path, line = location(rep)
        where = Where(path, None if line is None else line + 1, None, Text(rep.longreprtext))
    return Failure(rep.nodeid, when, where, streams(rep.sections), rep.longreprtext)


def location(rep: Any) -> tuple[str, int | None]:
    path, line, _ = getattr(rep, "location", None) or (rep.nodeid, None, "")
    return path, line


def _order(rep: Any) -> tuple[object, ...]:
    path, line = location(rep)
    natural = [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", rep.nodeid)]
    return (path, -1 if line is None else line, natural)
