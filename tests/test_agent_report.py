import json
import re

import pytest

DURATION = re.compile(r" in \d+\.\d\ds")

MIXED = """\
import json

import pytest
from xpt import expect


def parse(text):
    return json.loads(text)


def test_expect_missing():
    expect({"user": "ada"})


def test_expect_mismatch():
    expect({"user": "ada", "age": 36}, {"user": "ada", "age": 37})


def test_assert_dict():
    assert {"a": 1, "b": [1, 2, 3]} == {"a": 1, "b": [1, 2, 4]}


def test_helper_raises():
    parse("{bad}")


@pytest.mark.parametrize("x", range(5))
def test_param(x):
    print(f"debug line {x}")
    if x != 2:
        assert x * 2 == x + 2


def test_ok():
    pass
"""

MIXED_REPORT = [
    "FAILED test_mixed.py::test_expect_missing  test_mixed.py:12",
    "  8db2  missing",
    '  actual: {"user": "ada"}',
    "FAILED test_mixed.py::test_expect_mismatch  test_mixed.py:16",
    "  b4e4  mismatch",
    '  value["age"]: 37 -> 36',
    "  1 changed, 1 unchanged",
    "FAILED test_mixed.py::test_assert_dict  test_mixed.py:20",
    "  assert left == right:",
    '  value["b"][2]: left 3, right 4',
    "  1 changed, 3 unchanged",
    "FAILED test_mixed.py::test_helper_raises  test_mixed.py:8 in parse",
    "  json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: "
    "line 1 column 2 (char 1)",
    "FAILED test_mixed.py::test_param[0]  test_mixed.py:31",
    "  also fails: [1] [3] [4]",
    "  AssertionError: assert (0 * 2) == (0 + 2)",
    "  stdout: debug line 0",
    "8 failed, 2 passed in 0.00s. Next: xpt accept 8db2 b4e4",
]


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


def run(pytester: pytest.Pytester, *args: str, **files: str) -> pytest.RunResult:
    pytester.makepyfile(**files)
    pytester.makeini("[pytest]\n")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def lines(result: pytest.RunResult) -> list[str]:
    return [DURATION.sub(" in 0.00s", line) for line in result.outlines]


def test_a_passing_run_prints_one_line(pytester: pytest.Pytester):
    result = run(pytester, test_ok="def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    assert lines(result) == ["2 passed in 0.00s."]
    assert result.ret == 0


def test_the_mixed_suite_prints_one_block_per_failure(pytester: pytest.Pytester):
    result = run(pytester, test_mixed=MIXED)
    assert lines(result) == MIXED_REPORT
    assert result.ret == 1


def test_the_report_is_at_most_a_quarter_of_pytest_short_output(pytester: pytest.Pytester):
    xpt = run(pytester, test_mixed=MIXED)
    stock = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--xpt-report=pytest", "-q", "--tb=short"
    )
    xpt_bytes = len(xpt.stdout.str().encode("utf-8"))
    stock_bytes = len(stock.stdout.str().encode("utf-8"))
    assert xpt_bytes * 4 <= stock_bytes, (xpt_bytes, stock_bytes)


def test_xdist_workers_produce_the_same_single_report(pytester: pytest.Pytester):
    result = run(pytester, "-n", "2", test_mixed=MIXED)
    assert lines(result) == MIXED_REPORT
    assert result.ret == 1


def test_verbose_raises_the_limits(pytester: pytest.Pytester):
    source = "def test_long():\n    raise ValueError('x' * 300)\n"
    quiet = run(pytester, test_long=source)
    verbose = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-v")
    assert lines(quiet)[1] == f"  ValueError: {'x' * 188}... (312 chars)"
    assert lines(verbose)[1] == f"  ValueError: {'x' * 300}"


def test_very_verbose_adds_the_traceback(pytester: pytest.Pytester):
    result = run(pytester, "-vv", test_tb="def test_tb():\n    raise ValueError('boom')\n")
    output = lines(result)
    assert output[:3] == [
        "FAILED test_tb.py::test_tb  test_tb.py:2",
        "  ValueError: boom",
        "  traceback:",
    ]
    assert "    E       ValueError: boom" in output


def test_captured_output_keeps_the_last_lines_of_each_stream(pytester: pytest.Pytester):
    source = """\
import logging
import sys

def test_noisy():
    for i in range(8):
        print(f"out {i}")
    print("err", file=sys.stderr)
    logging.getLogger("app").warning("careful")
    assert 1 > 2
"""
    result = run(pytester, test_noisy=source)
    assert lines(result) == [
        "FAILED test_noisy.py::test_noisy  test_noisy.py:9",
        "  AssertionError: assert 1 > 2",
        "  stdout: out 3",
        "  stdout: out 4",
        "  stdout: out 5",
        "  stdout: out 6",
        "  stdout: out 7",
        "  stderr: err",
        "  log: WARNING  app:test_noisy.py:8 careful",
        "1 failed in 0.00s.",
    ]


def test_a_setup_error_names_the_phase_and_the_fixture(pytester: pytest.Pytester):
    source = """\
import pytest

@pytest.fixture
def db():
    raise RuntimeError("no database")

def test_query(db):
    pass
"""
    result = run(pytester, test_setup=source)
    assert lines(result) == [
        "ERROR test_setup.py::test_query (setup)  test_setup.py:5 in db",
        "  RuntimeError: no database",
        "1 error in 0.00s.",
    ]
    assert result.ret == 1


def test_a_collection_error_is_shown(pytester: pytest.Pytester):
    result = run(pytester, test_broken="import nosuchmodule\n")
    assert lines(result) == [
        "Interrupted: 1 error during collection",
        "ERROR test_broken.py (collect)",
        "  E   ModuleNotFoundError: No module named 'nosuchmodule'",
        "1 error in 0.00s.",
    ]
    assert result.ret == 2


@pytest.mark.parametrize(
    ("args", "env"), [(("--xpt-report=pytest",), None), ((), "pytest")], ids=["flag", "env"]
)
def test_opting_out_prints_pytest_output(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    env: str | None,
):
    if env is not None:
        monkeypatch.setenv("XPT_REPORT", env)
    result = run(pytester, *args, test_fail="def test_fail():\n    assert 1 == 2\n")
    result.stdout.fnmatch_lines(["*= test session starts =*", "*= FAILURES =*", "*1 failed in*"])
    assert result.ret == 1


def test_an_unknown_report_mode_is_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XPT_REPORT", "loud")
    result = run(pytester, test_ok="def test_ok():\n    pass\n")
    result.stderr.fnmatch_lines(["*XPT_REPORT must be xpt or pytest, got 'loud'.*"])
    assert result.ret == 4


@pytest.mark.parametrize(
    ("source", "args", "code"),
    [
        ("def test_ok():\n    pass\n", (), 0),
        ("def test_bad():\n    assert False\n", (), 1),
        ("def helper():\n    pass\n", (), 5),
        ("def test_ok():\n    pass\n", ("--no-such-option",), 4),
    ],
    ids=["pass", "fail", "no tests", "usage error"],
)
def test_exit_codes_match_pytest(
    pytester: pytest.Pytester, source: str, args: tuple[str, ...], code: int
):
    assert run(pytester, *args, test_codes=source).ret == code
    stock = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--xpt-report=pytest", *args)
    assert stock.ret == code


def test_xpt_json_writes_the_untruncated_report(pytester: pytest.Pytester):
    source = """\
from xpt import expect

def test_mismatch():
    expect({"a": 1, "b": "x" * 300}, {"a": 2, "b": "y"})

def test_ok():
    pass
"""
    result = run(pytester, "--xpt-json=report.json", test_json=source)
    assert result.ret == 1
    data = json.loads((pytester.path / "report.json").read_text(encoding="utf-8"))
    run_id = data.pop("run")
    assert re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{4}", run_id)
    assert isinstance(data.pop("duration"), float)
    [failure] = data.pop("failures")
    assert data == {
        "schema_version": 1,
        "exit_code": 1,
        "summary": data["summary"],
        "counts": ["1 failed", "1 passed"],
    }
    assert DURATION.sub(" in 0.00s", data["summary"]) == (
        "1 failed, 1 passed in 0.00s. Next: xpt accept " + failure["group"]["id"]
    )
    group = failure.pop("group")
    assert failure.pop("detail")["kind"] == "expect"
    assert "expect() mismatch" in failure.pop("traceback")
    assert failure == {
        "test": "test_json.py::test_mismatch",
        "when": "call",
        "path": "test_json.py",
        "line": 4,
        "function": None,
        "output": {},
    }
    assert group["status"] == "mismatch"
    assert group["changes"] == [
        {"path": 'value["a"]', "op": "replace", "expected": 2, "actual": 1},
        {"path": 'value["b"]', "op": "replace", "expected": "y", "actual": "x" * 300},
    ]
