import re
import sys

import pytest

SUITE = """\
import logging
import warnings

import pytest


def test_pass():
    logging.getLogger("app").info("live line")


def test_fail():
    print("captured out")
    assert 1 == 2


def test_skip():
    pytest.skip("not today")


@pytest.mark.xfail(reason="known bug")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="maybe fixed")
def test_xpass():
    pass


def test_warns():
    warnings.warn("old api", DeprecationWarning)
"""

PLUGIN = """\
def pytest_report_header(config):
    return "plugin header line"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    tr = terminalreporter
    tr.write_sep("-", "plugin summary")
    passed, failed = len(tr.stats.get("passed", [])), len(tr.stats.get("failed", []))
    tr.write_line(f"plugin saw {passed} passed and {failed} failed")
    tr.section("plugin section")
    tr.ensure_newline()
    config.get_terminal_writer().line("plugin writer line")
"""

SEP = re.compile(r"^([=_!-])\1+ (?P<title>.+?) \1+$")
REPLACED_SECTIONS = {"test session starts", "FAILURES", "ERRORS", "short test summary info"}
HEADER = re.compile(r"^(platform |rootdir: |configfile: |plugins: |testpaths: |collect)")
PROGRESS = re.compile(r"^(\S+\.py ?)?[.FEsxX]+ *(\[ *\d+%\])?$")
COUNTS = re.compile(
    r"^(=+ )?(\d+ (passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected)"
    r"|no tests ran).* in [\d.]+s"
)
TIMES = re.compile(r"\d+\.\d+s\b")


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


def pytest_run(pytester: pytest.Pytester, *args: str, stdin: bytes = b"") -> pytest.RunResult:
    return pytester.run(sys.executable, "-m", "pytest", "-p", "no:sugar", *args, stdin=stdin)


def kept_stock_lines(output: list[str], reportchars_requested: bool) -> list[str]:
    """The lines of pytest's report that the xpt report does not replace."""
    kept, section = [], None
    for line in output:
        sep = SEP.match(line)
        if sep and line.startswith("="):
            section = sep["title"]
        replaced = section in REPLACED_SECTIONS and not (
            reportchars_requested and section == "short test summary info"
        )
        if line and not (
            replaced or HEADER.match(line) or PROGRESS.match(line) or COUNTS.match(line)
        ):
            kept.append(TIMES.sub("0.00s", line))
    return kept


OPTIONS = [
    (),
    ("-q",),
    ("-qq",),
    ("-v",),
    ("-vv",),
    ("-x",),
    ("--maxfail=1",),
    ("-ra",),
    ("-rA",),
    ("-rN",),
    ("--tb=long",),
    ("--tb=short",),
    ("--tb=line",),
    ("--tb=no",),
    ("-l",),
    ("--durations=2",),
    ("--setup-show",),
    ("-s",),
    ("--log-cli-level=INFO",),
    ("-W", "error::DeprecationWarning"),
    ("--disable-warnings",),
    ("--no-header",),
    ("--no-summary",),
    ("--junitxml=junit.xml",),
    ("--co",),
    ("--fixtures",),
    ("--markers",),
    ("--setup-plan",),
    ("--help",),
    ("--version",),
    ("-p", "no:terminal"),
    ("-n", "2"),
]


@pytest.mark.parametrize("args", OPTIONS, ids=" ".join)
def test_every_pytest_section_the_report_does_not_replace_still_prints(
    pytester: pytest.Pytester, args: tuple[str, ...]
):
    pytester.makepyfile(test_suite=SUITE)
    pytester.makeconftest(PLUGIN)
    assert_only_replaced_sections_differ(pytester, args)


@pytest.mark.parametrize(
    ("source", "args"),
    [
        ("def test_stop():\n    raise KeyboardInterrupt\n", ()),
        ("import warnings\n\ndef test_w():\n    warnings.warn('x')\n", ("--max-warnings=0",)),
    ],
    ids=["keyboard interrupt", "max warnings"],
)
def test_interrupts_and_warning_limits_match_pytest(
    pytester: pytest.Pytester, source: str, args: tuple[str, ...]
):
    pytester.makepyfile(test_stop=source)
    assert_only_replaced_sections_differ(pytester, args)


def assert_only_replaced_sections_differ(pytester: pytest.Pytester, args: tuple[str, ...]) -> None:
    stock = pytest_run(pytester, "--xpt-report=pytest", *args)
    xpt = pytest_run(pytester, *args)
    assert xpt.ret == stock.ret
    requested = any(a.startswith("-r") and a not in ("-rN", "-rfE") for a in args)
    missing = [
        line
        for line in kept_stock_lines(stock.outlines, requested)
        if line not in kept_stock_lines(xpt.outlines, requested)
    ]
    assert missing == []
    assert stock.errlines == xpt.errlines


def test_last_failed_reruns_say_so(pytester: pytest.Pytester):
    pytester.makepyfile(test_suite=SUITE)
    pytest_run(pytester)
    result = pytest_run(pytester, "--lf")
    result.stdout.fnmatch_lines(["run-last-failure: rerun previous 1 failure*"])
    assert result.outlines[-1].startswith("1 failed in ")


def test_the_debugger_prompt_comes_before_the_report(pytester: pytest.Pytester):
    pytester.makepyfile(test_suite=SUITE)
    result = pytest_run(pytester, "--pdb", "-p", "no:cacheprovider", stdin=b"c\n")
    output = result.outlines
    prompt = next(i for i, line in enumerate(output) if "(Pdb)" in line)
    report = output.index("FAILED test_suite.py::test_fail  test_suite.py:13")
    assert any("PDB post_mortem" in line for line in output[:prompt])
    assert prompt < report
    assert result.ret == 1


def test_xdist_status_and_plugin_summaries_reach_the_terminal(pytester: pytest.Pytester):
    pytester.makepyfile(test_suite=SUITE)
    pytester.makeconftest(PLUGIN)
    result = pytest_run(pytester, "-n", "2", "-p", "no:cacheprovider")
    result.stdout.fnmatch_lines(
        [
            "plugin header line",
            "2 workers [6 items]",
            "*- plugin summary -*",
            "plugin saw 2 passed and 1 failed",
            "*= plugin section =*",
            "plugin writer line",
            "FAILED test_suite.py::test_fail  test_suite.py:13",
        ]
    )


def test_pytest_sugar_keeps_the_terminal_when_it_is_on(pytester: pytest.Pytester):
    pytest.importorskip("pytest_sugar")
    pytester.makepyfile(test_suite=SUITE)
    result = pytester.run(sys.executable, "-m", "pytest", "--force-sugar", "-p", "no:cacheprovider")
    result.stdout.fnmatch_lines(["*Results (*"])
    assert result.ret == 1


INSTALL_FAILS = """\
import xpt.terminal


def broken_swap(reporter, cls, json_path):
    reporter.config.pluginmanager.unregister(reporter)
    raise AttributeError("'TerminalReporter' object has no attribute '_session'")


xpt.terminal.swap = broken_swap
"""

RENDER_FAILS = """\
import xpt.terminal


def broken_report(session, lim):
    raise RuntimeError("cannot render")


xpt.terminal.report_lines = broken_report
"""


@pytest.mark.parametrize(
    ("conftest", "error"),
    [
        (INSTALL_FAILS, "AttributeError: 'TerminalReporter' object has no attribute '_session'"),
        (RENDER_FAILS, "RuntimeError: cannot render"),
    ],
    ids=["install", "render"],
)
def test_a_broken_report_falls_back_to_pytest_with_one_line(
    pytester: pytest.Pytester, conftest: str, error: str
):
    pytester.makepyfile(test_suite=SUITE)
    pytester.makeconftest(conftest)
    result = pytest_run(pytester, "-p", "no:cacheprovider")
    result.stdout.fnmatch_lines(
        [
            "*= FAILURES =*",
            "*= short test summary info =*",
            "FAILED test_suite.py::test_fail - assert 1 == 2",
            "*= 1 failed, 2 passed, 1 skipped, 1 xfailed, 1 xpassed, 1 warning in *",
        ]
    )
    notices = [line for line in result.outlines if line.startswith("xpt: ")]
    assert notices == [
        f"xpt: the xpt report failed ({error}), so this is pytest's report. Please report it "
        "at https://github.com/Harville-Labs/xpt/issues. --xpt-report=pytest hides this line."
    ]
    assert result.ret == 1


def test_a_render_failure_keeps_a_passing_exit_code(pytester: pytest.Pytester):
    pytester.makepyfile(test_ok="def test_ok():\n    pass\n")
    pytester.makeconftest(RENDER_FAILS)
    result = pytest_run(pytester, "-p", "no:cacheprovider")
    assert result.outlines[-1].startswith("xpt: the xpt report failed")
    assert result.ret == 0


def test_an_unsupported_pytest_keeps_its_report_and_says_why(pytester: pytest.Pytester):
    pytester.makepyfile(test_suite=SUITE)
    pytester.makeconftest(
        "import pytest\n\npytest.__version__ = '7.4.4'\npytest.version_tuple = (7, 4, 4)\n"
    )
    result = pytest_run(pytester, "-p", "no:cacheprovider")
    result.stdout.fnmatch_lines(["*= FAILURES =*", "*= short test summary info =*"])
    notices = [line for line in result.outlines if line.startswith("xpt: ")]
    assert notices == [
        "xpt: the xpt report needs pytest 8.0 or later and this is pytest 7.4.4, so this is "
        "pytest's report. --xpt-report=pytest hides this line."
    ]
    assert result.ret == 1


def test_json_output_with_an_option_that_keeps_pytests_report_is_a_usage_error(
    pytester: pytest.Pytester,
):
    pytester.makepyfile(test_suite=SUITE)
    result = pytest_run(pytester, "--xpt-json=out.json", "--tb=short")
    result.stderr.fnmatch_lines(
        ["*--xpt-json needs the xpt report, but --tb chooses pytest's traceback style.*"]
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
