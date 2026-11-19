import json
import re

import pytest

DURATION = re.compile(r" in \d+\.\d\ds")

TESTS = """\
from xpt import expect

def test_missing():
    expect([1, 2])

def test_also_missing():
    expect("text")

def test_mismatch():
    expect("b", "a")
"""

FILLED = """\
from xpt import expect

def test_missing():
    expect([1, 2], [1, 2])

def test_also_missing():
    expect("text", "text")

def test_mismatch():
    expect("b", "a")
"""


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    (pytester.path / "test_sample.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    return pytester


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_sample.py").read_text(encoding="utf-8")


def footer(result: pytest.RunResult) -> str:
    return DURATION.sub(" in 0.00s", result.outlines[-1])


def mismatch_id(result: pytest.RunResult) -> str:
    [id] = [m[1] for line in result.outlines if (m := re.fullmatch(r"  (\w+)  mismatch", line))]
    return id


def test_missing_expectations_are_filled_and_a_mismatch_is_left_alone(project: pytest.Pytester):
    result = run(project, "--expect-create")
    result.assert_outcomes(failed=3)
    assert source(project) == FILLED
    assert footer(result) == (
        "3 failed in 0.00s. Filled 2 missing expectations: test_sample.py:4 test_sample.py:7. "
        f"Next: xpt accept {mismatch_id(result)}"
    )


def test_the_run_after_filling_passes_the_filled_expectations(project: pytest.Pytester):
    run(project, "--expect-create").assert_outcomes(failed=3)
    rerun = run(project, "--expect-create")
    rerun.assert_outcomes(passed=2, failed=1)
    assert source(project) == FILLED
    assert footer(rerun) == f"1 failed, 2 passed in 0.00s. Next: xpt accept {mismatch_id(rerun)}"


def test_without_the_flag_the_source_is_unchanged(project: pytest.Pytester):
    run(project).assert_outcomes(failed=3)
    assert source(project) == TESTS


def test_filling_every_failure_suggests_running_the_tests(pytester: pytest.Pytester):
    pytester.makepyfile(test_sample="from xpt import expect\n\ndef test_a():\n    expect(3)\n")
    result = run(pytester, "--expect-create")
    assert footer(result) == (
        "1 failed in 0.00s. Filled 1 missing expectation: test_sample.py:4. Next: run the tests."
    )
    assert result.ret == 1


def test_under_xdist_the_controller_fills_once_after_the_workers(project: pytest.Pytester):
    result = run(project, "-n", "2", "--expect-create")
    result.assert_outcomes(failed=3)
    assert source(project) == FILLED
    assert footer(result) == (
        "3 failed in 0.00s. Filled 2 missing expectations: test_sample.py:4 test_sample.py:7. "
        f"Next: xpt accept {mismatch_id(result)}"
    )


def test_the_json_report_lists_the_outcomes(project: pytest.Pytester):
    run(project, "--expect-create", "--xpt-json", "report.json")
    report = json.loads((project.path / "report.json").read_text(encoding="utf-8"))
    created = report["created"]
    assert created["changed"] == ["test_sample.py"]
    assert [(o["site"]["line"], o["status"]) for o in created["outcomes"]] == [
        (4, "accepted"),
        (7, "accepted"),
    ]


def test_the_json_report_has_no_outcomes_without_the_flag(project: pytest.Pytester):
    run(project, "--xpt-json", "report.json")
    report = json.loads((project.path / "report.json").read_text(encoding="utf-8"))
    assert report["created"] is None


def test_pytest_output_also_says_what_was_filled(project: pytest.Pytester):
    result = run(project, "--expect-create", "--xpt-report=pytest")
    assert source(project) == FILLED
    result.stdout.fnmatch_lines(
        ["Filled 2 missing expectations: test_sample.py:4 test_sample.py:7."]
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--expect-no-record",), "--expect-create needs recording. Drop --expect-no-record."),
        ((), "--expect-create needs recording. Unset XPT_NO_RECORD."),
    ],
    ids=["flag", "environment"],
)
def test_create_without_recording_is_a_usage_error(
    project: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    message: str,
):
    if not args:
        monkeypatch.setenv("XPT_NO_RECORD", "1")
    result = run(project, "--expect-create", *args)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}"])
    assert source(project) == TESTS


def test_a_held_accept_lock_leaves_the_source_and_suggests_every_accept(
    project: pytest.Pytester,
):
    (project.path / ".xpt").mkdir()
    (project.path / ".xpt" / "accept.lock").write_text("1", encoding="utf-8")
    result = run(project, "--expect-create", "--xpt-json", "report.json")
    assert source(project) == TESTS
    ids = [m[1] for line in result.outlines if (m := re.fullmatch(r"  (\w+)  \w+", line))]
    assert footer(result) == f"3 failed in 0.00s. Next: xpt accept {' '.join(sorted(ids))}"
    report = json.loads((project.path / "report.json").read_text(encoding="utf-8"))
    reasons = {o["status"]: o["reason"] for o in report["created"]["outcomes"]}
    assert list(reasons) == ["skipped"]
    assert "another xpt command is writing to .xpt/" in reasons["skipped"]


def test_a_rejected_missing_value_is_not_filled(project: pytest.Pytester):
    first = run(project)
    [id] = [m[1] for line in first.outlines if (m := re.fullmatch(r"  (\w+)  missing", line))][:1]
    rejected = project.run("xpt", "reject", id)
    assert rejected.ret == 0, rejected.outlines
    run(project, "--expect-create").assert_outcomes(failed=3)
    assert source(project) == FILLED.replace("expect([1, 2], [1, 2])", "expect([1, 2])")
