import json
import re

import pytest

DURATION = re.compile(r" in \d+\.\d\ds")

TESTS = """\
from pathlib import Path

import pytest
from xpt import expect


def test_first():
    expect("new", "old")


def test_second():
    expect(2, 1)


def test_missing():
    expect([1])


@pytest.mark.parametrize("n", [1, 2])
def test_conflict(n):
    expect(n, 0)


def test_source_is_unchanged_while_the_run_lasts():
    accepted = 'expect("new", ' + '"new")'
    assert accepted not in Path(__file__).read_text(encoding="utf-8")
"""

FIRST_ACCEPTED = TESTS.replace('expect("new", "old")\n', 'expect("new", "new")\n')


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


@pytest.fixture
def ids(project: pytest.Pytester) -> dict[str, str]:
    """Each failing expect test's call site id, read from a first run's report."""
    found, test = {}, ""
    for line in run(project).outlines:
        if header := re.match(r"FAILED test_sample\.py::(\w+)", line):
            test = header[1]
        elif status := re.fullmatch(r"  (\w+)  (mismatch|missing|conflict)", line):
            found[test] = status[1]
    assert set(found) == {"test_first", "test_second", "test_missing", "test_conflict"}
    return found


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_sample.py").read_text(encoding="utf-8")


def footer(result: pytest.RunResult) -> str:
    return DURATION.sub(" in 0.00s", result.outlines[-1])


def next_ids(ids: dict[str, str], *tests: str) -> str:
    return " ".join(sorted(ids[t] for t in tests))


def test_a_named_call_site_is_accepted_after_the_run_and_the_rest_are_left(
    project: pytest.Pytester, ids: dict[str, str]
):
    result = run(project, f"--expect-accept={ids['test_first']}")
    result.assert_outcomes(failed=5, passed=1)
    assert source(project) == FIRST_ACCEPTED
    assert footer(result) == (
        "5 failed, 1 passed in 0.00s. Accepted 1 expectation: test_sample.py:8. "
        f"Next: xpt accept {next_ids(ids, 'test_second', 'test_missing')}"
    )


def test_the_run_after_accepting_passes_the_accepted_test(
    project: pytest.Pytester, ids: dict[str, str]
):
    run(project, f"--expect-accept={ids['test_first']}")
    rerun = run(project)
    rerun.assert_outcomes(failed=5, passed=1)
    assert not any(line.startswith("FAILED test_sample.py::test_first") for line in rerun.outlines)


@pytest.mark.parametrize("form", ["commas", "repeated"])
def test_ids_are_given_with_commas_or_by_repeating_the_option(
    project: pytest.Pytester, ids: dict[str, str], form: str
):
    first, second = ids["test_first"], ids["test_second"]
    if form == "commas":
        args = [f"--expect-accept={first}, {second}"]
    else:
        args = [f"--expect-accept={first}", "--expect-accept", second]
    result = run(project, *args)
    assert source(project) == FIRST_ACCEPTED.replace("expect(2, 1)", "expect(2, 2)")
    assert footer(result) == (
        "5 failed, 1 passed in 0.00s. Accepted 2 expectations: test_sample.py:8 "
        f"test_sample.py:12. Next: xpt accept {ids['test_missing']}"
    )


def test_an_unknown_id_is_reported_and_the_known_ones_are_still_accepted(
    project: pytest.Pytester, ids: dict[str, str]
):
    result = run(project, f"--expect-accept=zzzz,{ids['test_first']}")
    assert source(project) == FIRST_ACCEPTED
    assert result.outlines[-3:-1] == [
        "zzzz  unknown",
        "  No expect failure in this run has this id. A test that passes now has none.",
    ]
    assert footer(result) == (
        "5 failed, 1 passed in 0.00s. Accepted 1 expectation: test_sample.py:8. "
        "Not accepted: zzzz unknown. "
        f"Next: xpt accept {next_ids(ids, 'test_second', 'test_missing')}"
    )


def test_a_call_site_that_cannot_be_accepted_is_skipped_with_its_reason(
    project: pytest.Pytester, ids: dict[str, str]
):
    conflict = ids["test_conflict"]
    result = run(project, f"--expect-accept={conflict}")
    assert source(project) == TESTS
    assert result.outlines[-3] == f"{conflict}  test_sample.py:21  skipped"
    assert result.outlines[-2].startswith("  calls disagree on the actual value.")
    assert footer(result) == (
        f"5 failed, 1 passed in 0.00s. Not accepted: {conflict} skipped. "
        f"Next: xpt accept {next_ids(ids, 'test_first', 'test_second', 'test_missing')}"
    )


def test_each_acceptable_failure_ends_its_diff_with_the_command_to_accept_it(
    project: pytest.Pytester, ids: dict[str, str]
):
    lines = run(project).outlines
    first, second, missing = ids["test_first"], ids["test_second"], ids["test_missing"]
    at = lines.index(f"  {second}  mismatch")
    assert lines[at : at + 4] == [
        f"  {second}  mismatch",
        "  value: 1 -> 2",
        "  1 changed, 0 unchanged",
        f"  accept: xpt accept {second}",
    ]
    assert f"  accept: xpt accept {first}" in lines
    assert f"  accept: xpt accept {missing}" in lines
    assert not any(line.endswith(f"xpt accept {ids['test_conflict']}") for line in lines)


def test_an_accepted_failure_has_no_accept_command(project: pytest.Pytester, ids: dict[str, str]):
    lines = run(project, f"--expect-accept={ids['test_first']}").outlines
    assert f"  accept: xpt accept {ids['test_first']}" not in lines
    assert f"  accept: xpt accept {ids['test_second']}" in lines


def test_with_expect_create_a_named_site_is_accepted_and_missing_ones_filled(
    project: pytest.Pytester, ids: dict[str, str]
):
    result = run(project, "--expect-create", f"--expect-accept={ids['test_first']}")
    assert source(project) == FIRST_ACCEPTED.replace("expect([1])", "expect([1], [1])")
    assert footer(result) == (
        "5 failed, 1 passed in 0.00s. Filled 1 missing expectation: test_sample.py:16. "
        "Accepted 1 expectation: test_sample.py:8. "
        f"Next: xpt accept {ids['test_second']}"
    )


def test_under_xdist_the_controller_accepts_once_after_the_workers(
    project: pytest.Pytester, ids: dict[str, str]
):
    result = run(project, "-n", "2", f"--expect-accept={ids['test_first']}")
    result.assert_outcomes(failed=5, passed=1)
    assert source(project) == FIRST_ACCEPTED


def test_the_json_report_lists_the_outcomes_and_unknown_ids(
    project: pytest.Pytester, ids: dict[str, str]
):
    run(project, f"--expect-accept={ids['test_first']},zzzz", "--xpt-json", "report.json")
    report = json.loads((project.path / "report.json").read_text(encoding="utf-8"))
    accepted = report["accepted"]
    assert accepted["changed"] == ["test_sample.py"]
    assert [(o["id"], o["status"]) for o in accepted["outcomes"]] == [
        (ids["test_first"], "accepted")
    ]
    assert accepted["unknown"] == ["zzzz"]


def test_the_json_report_has_no_accepted_outcomes_without_the_flag(project: pytest.Pytester):
    run(project, "--xpt-json", "report.json")
    report = json.loads((project.path / "report.json").read_text(encoding="utf-8"))
    assert report["accepted"] is None


@pytest.mark.parametrize("report", [["--xpt-report=pytest"], ["--tb=short"]])
def test_pytest_output_also_says_what_was_accepted(
    project: pytest.Pytester, ids: dict[str, str], report: list[str]
):
    result = run(project, f"--expect-accept={ids['test_first']},zzzz", *report)
    assert source(project) == FIRST_ACCEPTED
    result.stdout.fnmatch_lines(
        [
            "zzzz  unknown",
            "  No expect failure in this run has this id. A test that passes now has none.",
            "Accepted 1 expectation: test_sample.py:8. Not accepted: zzzz unknown.",
        ]
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--expect-accept=abcd", "--expect-no-record"), "--expect-accept needs recording."),
        (("--expect-accept=abcd",), "--expect-accept needs recording. Unset XPT_NO_RECORD."),
        (("--expect-accept= , ",), "--expect-accept needs call site ids"),
    ],
    ids=["no-record-flag", "no-record-environment", "no-ids"],
)
def test_accept_that_cannot_work_is_a_usage_error(
    project: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    message: str,
):
    if "XPT_NO_RECORD" in message:
        monkeypatch.setenv("XPT_NO_RECORD", "1")
    result = run(project, *args)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])
    assert source(project) == TESTS
