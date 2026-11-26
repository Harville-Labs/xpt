"""The design's xdist claims: workers record and never edit, the controller reads every
worker's file as one run, and the report matches a run without xdist."""

import json
import re
from pathlib import Path

import pytest

from xpt.group import group
from xpt.record import load_run

pytest.importorskip("xdist")

DURATION = re.compile(r" in \d+\.\d\ds")

TESTS = """\
from pathlib import Path

import pytest
from xpt import expect

SOURCE = Path(__file__).read_text(encoding="utf-8")


def test_missing():
    expect([1, 2])


def test_mismatch():
    expect("new", "old")


@pytest.mark.parametrize("n", range(6))
def test_shared(n):
    expect(n % 1, 1)


@pytest.mark.parametrize(
    "n",
    [
        pytest.param(1, marks=pytest.mark.xdist_group("one")),
        pytest.param(2, marks=pytest.mark.xdist_group("two")),
    ],
)
def test_conflict(n):
    expect(n, 0)


@pytest.mark.parametrize("n", range(4))
def test_source_is_unchanged_while_the_run_lasts(n):
    assert Path(__file__).read_text(encoding="utf-8") == SOURCE
"""

FILLED = TESTS.replace("expect([1, 2])\n", "expect([1, 2], [1, 2])\n")
ACCEPTED = TESTS.replace('expect("new", "old")\n', 'expect("new", "new")\n')
SPLIT = ("-n", "2", "--dist", "loadgroup")


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    (pytester.path / "test_sample.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    return pytester


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_sample.py").read_text(encoding="utf-8")


def only_run(pytester: pytest.Pytester) -> Path:
    [run_dir] = (pytester.path / ".xpt" / "runs").iterdir()
    return run_dir


def observed_by_worker(pytester: pytest.Pytester) -> dict[str, set[str]]:
    return {
        f.stem: {json.loads(line)["test"] for line in f.read_text(encoding="utf-8").splitlines()}
        for f in only_run(pytester).iterdir()
    }


def ids(result: pytest.RunResult) -> dict[str, str]:
    """Each failing test function's call site id and status, read from the report."""
    found, test = {}, ""
    for line in result.outlines:
        if header := re.match(r"FAILED test_sample\.py::(\w+)", line):
            test = header[1]
        elif status := re.fullmatch(r"  (\w+)  (mismatch|missing|conflict)", line):
            found[test] = status[1]
    return found


def lines(result: pytest.RunResult) -> list[str]:
    """The xpt report: every line from the first failure block on, which leaves out xdist's
    worker status lines and the progress lines pytest prints per test with -v."""
    out = result.outlines
    start = next(i for i, line in enumerate(out) if line.startswith("FAILED test_sample.py::"))
    return [DURATION.sub(" in 0.00s", line) for line in out[start:]]


def test_every_workers_observations_land_in_one_run_and_group_as_without_xdist(
    project: pytest.Pytester,
):
    run(project).assert_outcomes(failed=10, passed=4)
    plain = group(load_run(project.path))
    (project.path / ".xpt").rename(project.path / "plain")

    run(project, *SPLIT).assert_outcomes(failed=10, passed=4)
    workers = observed_by_worker(project)
    assert len(workers) == 2
    assert all(name.startswith("gw") for name in workers)
    observations = load_run(project.path)
    assert {o.run for o in observations} == {only_run(project).name}
    assert len(observations) == 10
    assert group(observations) == plain


def test_conflicting_values_from_different_workers_are_one_conflict_that_accept_refuses(
    project: pytest.Pytester,
):
    first = run(project, *SPLIT)
    conflict = ids(first)["test_conflict"]
    workers = observed_by_worker(project)
    [one] = [w for w, tests in workers.items() if "test_sample.py::test_conflict[1]" in tests]
    [two] = [w for w, tests in workers.items() if "test_sample.py::test_conflict[2]" in tests]
    assert one != two
    [g] = [g for g in group(load_run(project.path)) if g.id == conflict]
    assert g.status == "conflict"

    result = run(project, *SPLIT, f"--expect-accept={conflict}")
    assert f"{conflict}  test_sample.py:30  skipped" in result.outlines
    assert source(project) == TESTS


def test_expect_create_fills_once_from_the_controller_after_the_workers(
    project: pytest.Pytester,
):
    result = run(project, *SPLIT, "--expect-create")
    result.assert_outcomes(failed=10, passed=4)
    assert source(project) == FILLED
    assert "Filled 1 missing expectation: test_sample.py:10." in lines(result)[-1]


def test_expect_accept_writes_once_from_the_controller_after_the_workers(
    project: pytest.Pytester,
):
    mismatch = ids(run(project, *SPLIT))["test_mismatch"]
    result = run(project, *SPLIT, f"--expect-accept={mismatch}")
    result.assert_outcomes(failed=10, passed=4)
    assert source(project) == ACCEPTED
    assert "Accepted 1 expectation: test_sample.py:14." in lines(result)[-1]


@pytest.mark.parametrize(
    "args", [(), ("-vv",), ("--expect-create",)], ids=["report", "traceback", "create"]
)
def test_the_report_and_json_match_a_run_without_xdist(
    project: pytest.Pytester, args: tuple[str, ...]
):
    plain = run(project, *args, "--xpt-json=plain.json")
    (project.path / "test_sample.py").write_text(TESTS, encoding="utf-8")
    split = run(project, *SPLIT, *args, "--xpt-json=split.json")
    assert plain.ret == split.ret == 1
    assert lines(split) == lines(plain)
    reports = [
        json.loads((project.path / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("plain", "split")
    ]
    for report in reports:
        report.pop("run")
        report.pop("duration")
        report["summary"] = DURATION.sub(" in 0.00s", report["summary"])
        for failure in report["failures"]:
            failure["detail"]["observation"].pop("run")
    assert reports[0] == reports[1]


def test_no_record_under_xdist_writes_nothing(project: pytest.Pytester):
    run(project, *SPLIT, "--expect-no-record").assert_outcomes(failed=10, passed=4)
    assert not (project.path / ".xpt").exists()
    assert source(project) == TESTS


@pytest.mark.parametrize("flag", ["--expect-create", "--expect-accept=abcd"])
def test_no_record_under_xdist_still_refuses_to_write(project: pytest.Pytester, flag: str):
    result = run(project, *SPLIT, "--expect-no-record", flag)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert f"{flag.split('=')[0]} needs recording. Drop --expect-no-record." in result.stderr.str()
    assert not (project.path / ".xpt").exists()
