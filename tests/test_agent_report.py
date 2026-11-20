import json
import re

import pytest

from xpt.cli import main

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
    '  > expect({"user": "ada"})',
    "  8db2  missing",
    '  actual: {"user": "ada"}',
    "  accept: xpt accept 8db2",
    "FAILED test_mixed.py::test_expect_mismatch  test_mixed.py:16",
    '  > expect({"user": "ada", "age": 36}, {"user": "ada", "age": 37})',
    "  b4e4  mismatch",
    '  value["age"]: 37 -> 36',
    "  1 changed, 1 unchanged",
    "  accept: xpt accept b4e4",
    "FAILED test_mixed.py::test_assert_dict  test_mixed.py:20",
    '  > assert {"a": 1, "b": [1, 2, 3]} == {"a": 1, "b": [1, 2, 4]}',
    '  value["b"][2]: left 3, right 4',
    "  1 changed, 3 unchanged",
    "FAILED test_mixed.py::test_helper_raises  test_mixed.py:24 > test_mixed.py:8 parse",
    "  > return json.loads(text)",
    "  json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: "
    "line 1 column 2 (char 1)",
    "FAILED test_mixed.py::test_param[0]  test_mixed.py:31",
    "  > assert x * 2 == x + 2",
    "  AssertionError: assert (0 * 2) == (0 + 2)",
    "  stdout: debug line 0",
    "  also fails: test_mixed.py::test_param[1]  assert (1 * 2) == (1 + 2)",
    "  also fails: test_mixed.py::test_param[3]  assert (3 * 2) == (3 + 2)",
    "  also fails: test_mixed.py::test_param[4]  assert (4 * 2) == (4 + 2)",
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
    return [DURATION.sub(" in 0.00s", line) for line in result.outlines if line]


def test_a_passing_run_prints_one_line(pytester: pytest.Pytester):
    result = run(pytester, test_ok="def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    assert lines(result) == ["2 passed in 0.00s."]
    assert result.ret == 0


def test_the_mixed_suite_prints_one_block_per_failure(pytester: pytest.Pytester):
    result = run(pytester, test_mixed=MIXED)
    assert lines(result) == MIXED_REPORT
    assert result.ret == 1


def test_the_report_is_at_most_a_third_of_pytest_short_output(pytester: pytest.Pytester):
    xpt = run(pytester, test_mixed=MIXED)
    stock = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--xpt-report=pytest", "-q", "--tb=short"
    )
    xpt_bytes = len(xpt.stdout.str().encode("utf-8"))
    stock_bytes = len(stock.stdout.str().encode("utf-8"))
    assert xpt_bytes * 3 <= stock_bytes, (xpt_bytes, stock_bytes)


def test_xdist_workers_produce_the_same_single_report(pytester: pytest.Pytester):
    result = run(pytester, "-n", "2", test_mixed=MIXED)
    output = lines(result)
    assert output[:2] == ["created: 2/2 workers", "2 workers [10 items]"]
    assert output[2:] == MIXED_REPORT
    assert result.ret == 1


def test_verbose_raises_the_limits(pytester: pytest.Pytester):
    source = "def test_long():\n    raise ValueError('x' * 300)\n"
    quiet = run(pytester, test_long=source)
    verbose = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-v")
    assert lines(quiet)[2] == f"  ValueError: {'x' * 188}... (312 chars)"
    assert f"  ValueError: {'x' * 300}" in lines(verbose)


def test_verbose_keeps_pytest_line_per_test(pytester: pytest.Pytester):
    result = run(pytester, "-v", test_ok="def test_a():\n    pass\n")
    result.stdout.fnmatch_lines(["test_ok.py::test_a PASSED*", "1 passed in *s."])


def test_a_failure_in_a_nested_helper_shows_the_project_call_chain(pytester: pytest.Pytester):
    source = """\
import json

def load(text):
    return json.loads(text)

def parse(text):
    return load(text)

def test_parse():
    parse("{bad}")
"""
    result = run(pytester, test_chain=source)
    assert lines(result)[:2] == [
        "FAILED test_chain.py::test_parse  "
        "test_chain.py:10 > test_chain.py:7 parse > test_chain.py:4 load",
        "  > return json.loads(text)",
    ]


@pytest.mark.parametrize(
    ("raise_line", "cause"),
    [
        ('raise ValueError("no id") from error', "  caused by: KeyError: 'id'"),
        ('raise ValueError("no id")', "  caused by: KeyError: 'id'"),
        ('raise ValueError("no id") from None', None),
    ],
    ids=["explicit", "implicit", "suppressed"],
)
def test_a_chained_exception_shows_its_cause(
    pytester: pytest.Pytester, raise_line: str, cause: str | None
):
    source = f"""\
def load(data):
    try:
        return data["id"]
    except KeyError as error:
        {raise_line}

def test_load():
    load({{}})
"""
    result = run(pytester, test_cause=source)
    expected = [
        "FAILED test_cause.py::test_load  test_cause.py:8 > test_cause.py:5 load",
        f"  > {raise_line}",
        "  ValueError: no id",
        *([cause] if cause else []),
        "1 failed in 0.00s.",
    ]
    assert lines(result) == expected


def test_the_footer_counts_warnings(pytester: pytest.Pytester):
    source = "import warnings\n\ndef test_warns():\n    warnings.warn('old', DeprecationWarning)\n"
    result = run(pytester, test_warns=source)
    result.stdout.fnmatch_lines(["*= warnings summary =*", "*DeprecationWarning*"])
    assert lines(result)[-1] == "1 passed, 1 warning in 0.00s."


def test_very_verbose_adds_the_traceback(pytester: pytest.Pytester):
    result = run(pytester, "-vv", test_tb="def test_tb():\n    raise ValueError('boom')\n")
    output = lines(result)
    start = output.index("FAILED test_tb.py::test_tb  test_tb.py:2")
    assert output[start : start + 4] == [
        "FAILED test_tb.py::test_tb  test_tb.py:2",
        "  > raise ValueError('boom')",
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
        "  > assert 1 > 2",
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
        '  > raise RuntimeError("no database")',
        "  RuntimeError: no database",
        "1 error in 0.00s.",
    ]
    assert result.ret == 1


def test_a_collection_error_is_shown(pytester: pytest.Pytester):
    result = run(pytester, test_broken="import nosuchmodule\n")
    output = lines(result)
    assert re.fullmatch(r"!+ Interrupted: 1 error during collection !+", output[0])
    assert output[1:] == [
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
        "created": None,
        "accepted": None,
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
        "callers": [],
        "source": 'expect({"a": 1, "b": "x" * 300}, {"a": 2, "b": "y"})',
        "cause": None,
        "output": {},
    }
    assert group["status"] == "mismatch"
    assert group["changes"] == [
        {"path": 'value["a"]', "op": "replace", "expected": 2, "actual": 1},
        {"path": 'value["b"]', "op": "replace", "expected": "y", "actual": "x" * 300},
    ]


REJECTABLE = """\
from xpt import expect

def test_a():
    expect(1 + 1, 3)

def test_b():
    expect(2 + 2, 5)
"""


def test_a_rejected_group_is_marked_and_the_footer_stops_offering_it(pytester: pytest.Pytester):
    first = lines(run(pytester, test_rejectable=REJECTABLE))
    a, b = first[2].split()[0], first[8].split()[0]
    assert first[-1] == f"2 failed in 0.00s. Next: xpt accept {' '.join(sorted([a, b]))}"
    assert main(["reject", a, "--root", str(pytester.path)]) == 0
    second = lines(run(pytester, test_rejectable=REJECTABLE))
    assert second[2] == f"  {a}  mismatch  rejected"
    assert f"  accept: xpt accept {a}" not in second
    assert f"  accept: xpt accept {b}" in second
    assert second[-1] == f"2 failed in 0.00s. Next: xpt accept {b}"


def test_a_malformed_decisions_file_is_named_and_the_report_still_prints(
    pytester: pytest.Pytester,
):
    run(pytester, test_rejectable=REJECTABLE)
    (pytester.path / ".xpt" / "decisions.jsonl").write_text("{\n", encoding="utf-8")
    result = lines(run(pytester, test_rejectable=REJECTABLE))
    assert result[0].startswith("xpt: .xpt/decisions.jsonl:1: not JSON")
    assert result[-1].startswith("2 failed in 0.00s. Next: xpt accept ")
