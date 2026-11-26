import pytest

from xpt.record import load_run
from xpt.site import CallSite

TESTS = """\
from xpt import expect

def test_passes():
    expect(1, 1)

def test_missing():
    expect([1, 2])

class TestGroup:
    def test_mismatch(self):
        expect("b", "a")
"""


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    pytester.makepyfile(test_sample=TESTS)
    pytester.makeini("[pytest]\n")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_a_session_records_failures_by_node_id_and_marks_the_run_latest(
    pytester: pytest.Pytester,
):
    result = run(pytester)
    result.assert_outcomes(passed=1, failed=2)
    observations = load_run(pytester.path)
    assert sorted((o.test, o.status) for o in observations) == [
        ("test_sample.py::TestGroup::test_mismatch", "mismatch"),
        ("test_sample.py::test_missing", "missing"),
    ]
    sites = sorted(
        (o.site.qualname, o.site.line) for o in observations if isinstance(o.site, CallSite)
    )
    assert sites == [("TestGroup.test_mismatch", 11), ("test_missing", 7)]
    [run_dir] = (pytester.path / ".xpt" / "runs").iterdir()
    assert sorted(p.name for p in run_dir.iterdir()) == ["main.jsonl", "source"]


def test_the_no_record_flag_writes_nothing(pytester: pytest.Pytester):
    run(pytester, "--expect-no-record").assert_outcomes(passed=1, failed=2)
    assert not (pytester.path / ".xpt").exists()


def test_the_no_record_environment_variable_writes_nothing(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XPT_NO_RECORD", "1")
    run(pytester).assert_outcomes(passed=1, failed=2)
    assert not (pytester.path / ".xpt").exists()


def test_xdist_workers_share_the_controller_run_and_write_their_own_files(
    pytester: pytest.Pytester,
):
    run(pytester, "-n", "2").assert_outcomes(passed=1, failed=2)
    observations = load_run(pytester.path)
    assert len(observations) == 2
    [run_dir] = (pytester.path / ".xpt" / "runs").iterdir()
    assert {o.run for o in observations} == {run_dir.name}
    assert all(p.name.startswith("gw") for p in run_dir.glob("*.jsonl"))
    assert (run_dir / "source").is_file()
    latest = (pytester.path / ".xpt" / "latest").read_text(encoding="utf-8")
    assert latest == run_dir.name


def test_a_fully_passing_session_creates_no_directory(pytester: pytest.Pytester):
    pytester.makepyfile(test_ok="from xpt import expect\n\ndef test_ok():\n    expect(1, 1)\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
    assert not (pytester.path / ".xpt").exists()


def test_existing_runs_are_pruned_to_five_at_session_end(pytester: pytest.Pytester):
    runs = pytester.path / ".xpt" / "runs"
    for day in range(1, 7):
        (runs / f"2026010{day}T000000-aaaa").mkdir(parents=True)
    run(pytester).assert_outcomes(passed=1, failed=2)
    kept = sorted(p.name for p in runs.iterdir())
    assert len(kept) == 5
    assert "20260101T000000-aaaa" not in kept
    assert (pytester.path / ".xpt" / "latest").read_text(encoding="utf-8") == kept[-1]
