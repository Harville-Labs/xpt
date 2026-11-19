from __future__ import annotations

import json
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _pytest import timing

from xpt import record
from xpt.accept import AcceptResult
from xpt.failure import Failure, Text, Where, streams, where_from_json
from xpt.summary import Session, limits, report_json, report_lines

CREATED = pytest.StashKey[AcceptResult]()

_SUMMARY_EXIT_CODES = (
    pytest.ExitCode.OK,
    pytest.ExitCode.TESTS_FAILED,
    pytest.ExitCode.INTERRUPTED,
    pytest.ExitCode.USAGE_ERROR,
    pytest.ExitCode.NO_TESTS_COLLECTED,
)


class AgentReporter(pytest.TerminalReporter):  # ty: ignore[subclass-of-final-class]
    """pytest's terminal reporter with the progress, header, and summary sections replaced
    by one compact block per failure and a one-line footer.

    It takes over the registered reporter object instead of replacing it, so plugins that
    already hold it, such as xdist, keep writing through it. pytest marks the class final,
    so a pytest upgrade can break this; the golden tests in test_agent_report.py catch it.
    """

    json_path: str | None

    @classmethod
    def install(cls, config: pytest.Config, json_path: str | None) -> None:
        manager = config.pluginmanager
        reporter = manager.get_plugin("terminalreporter")
        if not isinstance(reporter, pytest.TerminalReporter):
            return
        manager.unregister(reporter)
        reporter.__class__ = cls
        assert isinstance(reporter, AgentReporter)
        reporter.json_path = json_path
        manager.register(reporter, "terminalreporter")

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self._session = session
        self._session_start = timing.Instant()

    @pytest.hookimpl(tryfirst=True, optionalhook=True)
    def pytest_xdist_setupnodes(self) -> None:
        dist = self.config.pluginmanager.get_plugin("terminaldistreporter")
        if dist is not None:
            # xdist writes worker status as whole lines only when it believes it is not on a
            # tty; on a tty it rewrites one line through rewrite(), which prints nothing here.
            dist._isatty = True

    def pytest_collection(self) -> None:
        pass

    def report_collect(self, final: bool = False) -> None:
        pass

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        pass

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._tests_ran = True
        category, _, _ = self.config.hook.pytest_report_teststatus(
            report=report, config=self.config
        )
        self._add_stats(category, [report])

    @pytest.hookimpl(wrapper=True)
    def pytest_runtestloop(self) -> Generator[None, object, object]:
        return (yield)

    def rewrite(self, line: str, **markup: bool) -> None:
        pass

    def write_line(self, line: str | bytes, **markup: bool) -> None:
        if line:
            super().write_line(line, **markup)

    def write_sep(
        self, sep: str, title: str | None = None, fullwidth: int | None = None, **markup: bool
    ) -> None:
        if title:
            self.write_line(title, **markup)

    @pytest.hookimpl(wrapper=True)
    def pytest_terminal_summary(self) -> Generator[None]:
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: int | pytest.ExitCode
    ) -> Generator[None]:
        result = yield
        if exitstatus in _SUMMARY_EXIT_CODES:
            self.config.hook.pytest_terminal_summary(
                terminalreporter=self, exitstatus=exitstatus, config=self.config
            )
        self._check_max_warnings(session)
        if session.shouldfail:
            self.write_line(str(session.shouldfail))
        if exitstatus == pytest.ExitCode.INTERRUPTED and self._keyboardinterrupt_memo is not None:
            self._report_keyboardinterrupt()
            self._keyboardinterrupt_memo = None
        elif session.shouldstop:
            self.write_line(str(session.shouldstop))
        finished = self._finished(session)
        for line in report_lines(finished, limits(self.config.get_verbosity())):
            self.write_line(line)
        if self.json_path is not None:
            text = json.dumps(report_json(finished), ensure_ascii=False, indent=2)
            Path(self.json_path).write_text(text + "\n", encoding="utf-8")
        return result

    def _check_max_warnings(self, session: pytest.Session) -> None:
        maximum = getattr(self, "_get_max_warnings", lambda: None)()
        if maximum is None or session.exitstatus != pytest.ExitCode.OK:
            return
        count = len(self.stats.get("warnings", []))
        if count > maximum:
            session.exitstatus = pytest.ExitCode.MAX_WARNINGS_ERROR
            self.write_line(
                f"Tests pass, but maximum allowed warnings exceeded: {count} > {maximum}"
            )

    def _finished(self, session: pytest.Session) -> Session:
        reports = [r for key in ("failed", "error") for r in self.stats.get(key, []) if r.failed]
        reports.sort(key=_order)
        parts, _ = self.build_summary_stats_line()
        run = record.recorder.run if record.recorder.enabled else None
        return Session(
            failures=tuple(_failure(r) for r in reports),
            counts=tuple(text for text, _ in parts),
            seconds=self._session_start.elapsed().seconds,
            exit_code=int(session.exitstatus),
            run=run,
            created=self.config.stash.get(CREATED, None),
        )


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
