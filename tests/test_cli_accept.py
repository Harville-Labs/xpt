import os
import subprocess
import sys
import textwrap

import pytest
from cli_support import NOT_TRACED, Ran, xpt

SOURCE = """\
import pytest
from xpt import expect

def test_missing():
    expect(1 + 1)

def test_mismatch():
    expect("ab".upper(), "ab")

@pytest.mark.parametrize("n", [1, 2])
def test_conflict(n):
    expect(n)
"""


@pytest.fixture(autouse=True)
def utf_8_child_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


def run_tests(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider")


@pytest.fixture
def ids(pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    run_tests(pytester).assert_outcomes(failed=4)
    ran = xpt(capsys, "failures", "--json")
    assert ran.code == 1, ran
    groups = ran.json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def source(pytester: pytest.Pytester) -> str:
    return (pytester.path / "test_it.py").read_text(encoding="utf-8")


def test_accept_by_id_rewrites_the_source_and_the_test_then_passes(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", ids["test_missing"], ids["test_mismatch"])
    assert ran == Ran(
        0,
        f"{ids['test_missing']}  test_it.py:5  accepted\n"
        f"{ids['test_mismatch']}  test_it.py:8  accepted\n"
        "Changed test_it.py (2 accepted). Next: run the tests.\n",
        "",
    )
    assert "    expect(1 + 1, 2)\n" in source(pytester)
    assert '    expect("ab".upper(), "AB")\n' in source(pytester)
    run_tests(pytester).assert_outcomes(passed=2, failed=2)


def test_accept_json_lists_each_outcome_and_the_changed_files(
    capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", ids["test_missing"], "--json")
    assert ran.code == 0
    assert ran.json == {
        "schema_version": 1,
        "command": "accept",
        "run": ran.json["run"],
        "dry_run": False,
        "outcomes": [
            {
                "id": ids["test_missing"],
                "site": {"path": "test_it.py", "line": 5, "qualname": "test_missing"},
                "status": "accepted",
                "reason": "",
                "changed_code": NOT_TRACED,
            }
        ],
        "changed": ["test_it.py"],
    }


def test_accept_all_writes_every_acceptable_group_and_reports_the_rest(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--all")
    assert ran.code == 1
    assert ran.out.splitlines() == [
        f"{ids['test_missing']}  test_it.py:5  accepted",
        f"{ids['test_mismatch']}  test_it.py:8  accepted",
        f"{ids['test_conflict']}  test_it.py:12  skipped",
        "  calls disagree on the actual value. test_it.py::test_conflict[1] got 1. "
        "test_it.py::test_conflict[2] got 2. Give each case its own expect(), "
        "or pass key=case so one dict holds every case.",
        "Changed test_it.py (2 accepted, 1 skipped). Next: run the tests.",
    ]
    run_tests(pytester).assert_outcomes(passed=2, failed=2)


def test_accept_missing_fills_only_empty_expectations(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--missing")
    assert ran.out.splitlines() == [
        f"{ids['test_missing']}  test_it.py:5  accepted",
        "Changed test_it.py (1 accepted). Next: run the tests.",
    ]
    assert '    expect("ab".upper(), "ab")\n' in source(pytester)


def test_dry_run_reports_what_would_change_and_writes_nothing(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", "--dry-run", ids["test_mismatch"])
    assert ran == Ran(
        0,
        f"{ids['test_mismatch']}  test_it.py:8  accepted\n"
        "Dry run, would change test_it.py (1 accepted). "
        f"Next: xpt accept {ids['test_mismatch']}\n",
        "",
    )
    assert source(pytester) == SOURCE


def test_an_expectation_edited_since_the_run_is_stale_and_exits_one(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    edited = SOURCE.replace('"ab")', '"cd")')
    (pytester.path / "test_it.py").write_text(edited, encoding="utf-8")
    ran = xpt(capsys, "accept", ids["test_mismatch"])
    assert ran.code == 1
    lines = ran.out.splitlines()
    assert lines[0] == f"{ids['test_mismatch']}  test_it.py:8  stale"
    assert lines[-1] == "No files changed (1 stale). Next: run the tests, then xpt failures."
    assert source(pytester) == edited


def test_a_held_lock_is_a_usage_error_and_nothing_is_written(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    (pytester.path / ".xpt" / "accept.lock").write_text("1", encoding="utf-8")
    ran = xpt(capsys, "accept", ids["test_missing"])
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        "xpt: error: .xpt/accept.lock exists, so another xpt command is writing to .xpt/. "
        "If none is, delete .xpt/accept.lock and run the command again.\n"
    )
    assert source(pytester) == SOURCE


def test_an_unknown_id_writes_nothing(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    ran = xpt(capsys, "accept", ids["test_missing"], "beef")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err.startswith("xpt: error: no group beef in run ")
    assert source(pytester) == SOURCE


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["accept"],
            "xpt accept needs ids, --missing, or --all. Nothing was accepted. "
            "Run xpt failures to list the ids.",
        ),
        (["accept", "--all", "abcd"], "give ids, --missing, or --all, not more than one."),
        (["accept", "--all", "--missing"], "give ids, --missing, or --all, not more than one."),
    ],
)
def test_usage_errors_exit_two_with_one_line(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
):
    ran = xpt(capsys, *argv)
    assert (ran.code, ran.out) == (2, "")
    assert ran.err.startswith("xpt")
    assert ran.err.endswith(f"error: {message}\n")
    assert ran.err.count("\n") == 1


def test_an_unknown_command_names_itself_and_points_at_help(capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "frobnicate")
    assert (ran.code, ran.out) == (2, "")
    assert ran.err.startswith("xpt: error: argument command: invalid choice: 'frobnicate'")
    assert ran.err.endswith(". Run xpt --help for usage.\n")


def test_clean_removes_the_state_directory(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    assert xpt(capsys, "clean") == Ran(0, "Removed .xpt/.\n", "")
    assert not (pytester.path / ".xpt").exists()
    assert xpt(capsys, "clean", "--json").json == {
        "schema_version": 1,
        "command": "clean",
        "removed": False,
    }


def test_clean_refuses_while_accept_holds_the_lock(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str], ids: dict[str, str]
):
    (pytester.path / ".xpt" / "accept.lock").write_text("1", encoding="utf-8")
    ran = xpt(capsys, "clean")
    assert ran.code == 2
    assert ran.err.startswith("xpt: error: .xpt/accept.lock exists")
    assert (pytester.path / ".xpt" / "latest").exists()


def test_python_m_xpt_prints_utf_8_and_exits_with_the_command_code(
    pytester: pytest.Pytester, ids: dict[str, str]
):
    (pytester.path / "test_it.py").write_text(
        textwrap.dedent(
            """\
            from xpt import expect

            def test_it():
                expect("é")
            """
        ),
        encoding="utf-8",
    )
    run_tests(pytester).assert_outcomes(failed=1)
    child = subprocess.run(
        [sys.executable, "-m", "xpt", "failures"],
        cwd=pytester.path,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        check=False,
    )
    assert (child.returncode, child.stderr) == (1, "")
    assert '  actual: "é"\n' in child.stdout
