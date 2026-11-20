import pytest
from cli_support import xpt

SOURCE = """\
from xpt import expect

def test_missing():
    expect(1 + 1)

def test_mismatch():
    expect("ab".upper(), "ab")

def test_then_fails():
    expect(3)
    assert False, "fails after the expectation"

def test_unrelated():
    assert False, "never accepted, so never rerun"
"""


@pytest.fixture(autouse=True)
def utf_8_child_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_NO_RECORD", raising=False)


@pytest.fixture
def ids(pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=4)
    groups = xpt(capsys, "failures", "--json").json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def latest(pytester: pytest.Pytester) -> str:
    return (pytester.path / ".xpt" / "latest").read_text(encoding="utf-8")


def test_verify_reruns_only_the_accepted_tests_and_reports_that_they_pass(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--verify", ids["test_missing"], ids["test_mismatch"])
    assert ran.code == 0, ran
    assert ran.out.splitlines()[-2:] == [
        "Changed test_it.py (2 accepted).",
        "Verified: the 2 accepted tests pass.",
    ]


def test_verify_json_lists_the_rerun_tests_and_pytest_exit_code(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--verify", "--json", ids["test_missing"], ids["test_mismatch"])
    assert ran.code == 0
    verify = ran.json["verify"]
    assert (verify["tests"], verify["exit_code"], verify["passed"]) == (
        ["test_it.py::test_mismatch", "test_it.py::test_missing"],
        0,
        True,
    )
    assert "2 passed" in verify["output"]


def test_a_failing_rerun_prints_pytest_output_and_exits_one(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--verify", ids["test_then_fails"])
    assert ran.code == 1
    assert "fails after the expectation" in ran.out
    assert "never accepted, so never rerun" not in ran.out
    assert ran.out.splitlines()[-1] == "Verify failed: pytest exited 1 on the 1 accepted test."


def test_a_failing_rerun_is_recorded_as_the_latest_run(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    before = latest(pytester)
    ran = xpt(capsys, "accept", "--verify", "--json", ids["test_then_fails"])
    assert (ran.code, ran.json["verify"]["exit_code"], ran.json["verify"]["passed"]) == (
        1,
        1,
        False,
    )
    assert latest(pytester) != before


def test_nothing_accepted_means_nothing_is_rerun(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    (pytester.path / "test_it.py").write_text(SOURCE.replace('"ab")', '"cd")'), encoding="utf-8")
    before = latest(pytester)
    ran = xpt(capsys, "accept", "--verify", "--json", ids["test_mismatch"])
    assert ran.code == 1
    assert ran.json["verify"] == {"tests": [], "exit_code": None, "passed": None, "output": ""}
    assert latest(pytester) == before
    text = xpt(capsys, "accept", "--verify", ids["test_mismatch"]).out
    assert text.splitlines()[-1] == "Nothing to rerun."


def test_verify_with_dry_run_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--verify", "--dry-run", ids["test_missing"])
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        "xpt: error: --verify reruns the tests after writing, so it can't be used with --dry-run.\n"
    )
