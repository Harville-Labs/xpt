from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from xpt import record


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("xpt").addoption(
        "--expect-no-record",
        action="store_true",
        help="Do not record failing expect() calls under .xpt/.",
    )


def pytest_configure(config: pytest.Config) -> None:
    workerinput = getattr(config, "workerinput", None)
    run = workerinput["xpt_run"] if workerinput else record.new_run_id()
    worker = workerinput["workerid"] if workerinput else "main"
    record.recorder.configure(
        config.rootpath, run, worker, record=not config.getoption("expect_no_record")
    )


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


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not hasattr(session.config, "workerinput"):
        record.recorder.finish()
