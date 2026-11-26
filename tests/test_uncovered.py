"""`xpt coverage` lists the changed lines no test ran, grouped by function, from a pytest run
it traces or from coverage.py's data file, in a fixture repository."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cli_support import Ran, xpt

from xpt import uncovered

LOAD = """\
def read_rows(rows):
    out = []
    for row in rows:
        out.append(row["count"])
    return out


def total(rows):
    return sum(read_rows(rows))
"""

CHANGED = """\
def read_rows(rows):
    out = []
    for row in rows:
        # a row needs a count

        if row.get("count") is None:
            raise ValueError("missing count")
        out.append(row["count"])
    return out


def total(rows):
    return sum(read_rows(rows))


def unused():
    return 0
"""

TESTS = """\
from load import read_rows


def test_read_rows():
    assert read_rows([{"count": 1}]) == [1]
"""

PYTEST = (sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q")


@pytest.fixture(autouse=True)
def child_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("XPT_REPORT", raising=False)
    monkeypatch.delenv(uncovered.ENV, raising=False)
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    """A repository with LOAD and TESTS committed on main, and LOAD changed to CHANGED."""
    (pytester.path / "load.py").write_text(LOAD, encoding="utf-8")
    (pytester.path / "test_load.py").write_text(TESTS, encoding="utf-8")
    pytester.makeini("[pytest]\npythonpath = .\n")
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(pytester.path, "init", "-q", "-b", "main")
    git(pytester.path, "add", ".")
    git(pytester.path, "commit", "-q", "-m", "start")
    (pytester.path / "load.py").write_text(CHANGED, encoding="utf-8")
    return pytester


def coverage(capsys: pytest.CaptureFixture[str], *args: str) -> Ran:
    return xpt(capsys, "coverage", *args, "--", *PYTEST)


def test_lists_the_changed_lines_no_test_ran_by_function(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    ran = coverage(capsys)
    assert ran.code == 1
    assert "1 passed" in ran.err
    assert ran.out.splitlines() == [
        "load.py read_rows: 1 of 2 changed lines not run",
        '  7 |             raise ValueError("missing count")',
        "load.py unused: 1 of 2 changed lines not run",
        "  17 |     return 0",
        "2 of 4 changed lines not run, in 2 functions (since main).",
    ]


def test_json_carries_every_changed_function(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    data = coverage(capsys, "--json").json
    assert data == {
        "schema_version": 1,
        "command": "coverage",
        "since": "main",
        "source": "xpt",
        "tests": 0,
        "changed": 4,
        "not_run": 2,
        "functions": [
            {
                "path": "load.py",
                "function": "read_rows",
                "changed": [6, 7],
                "not_run": [{"line": 7, "text": '            raise ValueError("missing count")'}],
            },
            {
                "path": "load.py",
                "function": "unused",
                "changed": [16, 17],
                "not_run": [{"line": 17, "text": "    return 0"}],
            },
        ],
        "stale": [],
        "unmeasured": [],
    }


def test_a_change_the_tests_run_fully_exits_0(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "load.py").write_text(LOAD.replace("out = []", "out = list()"))
    ran = coverage(capsys)
    assert (ran.code, ran.out) == (0, "All changed lines ran: 1 line (since main).\n")


def test_only_changed_lines_count_and_the_diff_follows_since(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "load.py").write_text(LOAD + "\n\n# a comment\n")
    ran = coverage(capsys)
    assert (ran.code, ran.out, ran.err) == (0, "No changed lines with code (since main).\n", "")
    (project.path / "load.py").write_text(CHANGED, encoding="utf-8")
    git(project.path, "checkout", "-q", "-b", "work")
    git(project.path, "commit", "-q", "-am", "change")
    assert coverage(capsys).code == 1
    ran = coverage(capsys, "--since", "HEAD")
    assert (ran.code, ran.out) == (0, "No changed lines with code (since HEAD).\n")


def test_an_untracked_file_counts_whole_and_its_import_ran_its_body(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "load.py").write_text(LOAD, encoding="utf-8")
    (project.path / "rates.py").write_text("RATE = 1\n\n\ndef rate():\n    return RATE\n")
    (project.path / "never.py").write_text("X = 1\n")
    (project.path / "test_rates.py").write_text("import rates\n\n\ndef test_rate():\n    pass\n")
    assert coverage(capsys).out.splitlines() == [
        "never.py <module>: 1 of 1 changed line not run",
        "  1 | X = 1",
        "rates.py rate: 1 of 2 changed lines not run",
        "  5 |     return RATE",
        "2 of 7 changed lines not run, in 2 functions (since main).",
    ]


def test_a_module_imported_before_tracing_counts_its_body(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "load.py").write_text(LOAD.replace("def total(rows)", "def total(rows, n=0)"))
    ran = xpt(capsys, "coverage", "--", *PYTEST, "-p", "load")
    assert (ran.code, ran.out) == (0, "All changed lines ran: 1 line (since main).\n")


def test_failing_tests_exit_1_and_say_so(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "load.py").write_text(LOAD.replace("out = []", "out = list()"))
    (project.path / "test_load.py").write_text(TESTS.replace("== [1]", "== [2]"))
    ran = coverage(capsys)
    assert ran.code == 1
    assert ran.out.splitlines() == [
        "pytest exited 1: tests failed, and a failing test may stop before its lines.",
        "All changed lines ran: 2 lines (since main).",
    ]


def test_under_xdist_every_worker_counts(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    pytest.importorskip("xdist")
    (project.path / "test_more.py").write_text(
        "from load import total\n\n\ndef test_total():\n    assert total([]) == 0\n"
    )
    (project.path / "load.py").write_text(
        LOAD.replace("out = []", "out = list()").replace("sum(", "0 + sum(")
    )
    ran = xpt(capsys, "coverage", "--json", "--", *PYTEST, "-n", "2")
    assert "2 passed" in ran.err
    assert (ran.code, ran.json["changed"], ran.json["not_run"]) == (0, 5, 0)


def test_lines_past_the_limit_are_counted(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    body = "".join(f"    x{n} = {n}\n" for n in range(12))
    (project.path / "load.py").write_text(f"{LOAD}\n\ndef big():\n{body}")
    lines = coverage(capsys).out.splitlines()
    assert lines[0] == "load.py big: 12 of 13 changed lines not run"
    assert lines[11:13] == [
        "  ... 2 more lines",
        "12 of 13 changed lines not run, in 1 function (since main).",
    ]
    assert len(coverage(capsys, "--full").out.splitlines()) == 14


def test_outside_a_repository_is_a_usage_error(
    pytester: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (pytester.path / "load.py").write_text(LOAD, encoding="utf-8")
    ran = coverage(capsys)
    assert (ran.code, ran.out) == (2, "")
    assert ran.err == (
        "xpt: error: xpt coverage reports changed lines from git, and not a git repository. "
        "Run it inside a git repository with git installed.\n"
    )


def test_without_git_is_a_usage_error(
    project: pytest.Pytester,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    ran = coverage(capsys)
    assert ran.code == 2
    assert "and git is not installed." in ran.err


def test_a_command_that_is_not_pytest_is_a_usage_error(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    ran = xpt(capsys, "coverage", "--", sys.executable, "-c", "pass")
    assert ran.code == 2
    assert "exited 0 and traced no lines. xpt coverage needs pytest" in ran.err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "xpt coverage needs a pytest command"),
        (("--from-coverage", "--", "pytest"), "--from-coverage reads coverage.py's data"),
    ],
)
def test_it_needs_one_source_of_lines(
    project: pytest.Pytester,
    capsys: pytest.CaptureFixture[str],
    args: tuple[str, ...],
    message: str,
):
    ran = xpt(capsys, "coverage", *args)
    assert ran.code == 2
    assert message in ran.err


def numbits(lines: set[int]) -> bytes:
    data = bytearray(max(lines) // 8 + 1)
    for n in lines:
        data[n // 8] |= 1 << (n % 8)
    return bytes(data)


def coverage_db(path: Path, files: dict[str, set[int]], *, arcs: bool = False) -> None:
    """A minimal coverage.py data file: the tables and columns xpt reads."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key text, value text)")
    db.execute("CREATE TABLE file (id integer primary key, path text)")
    db.execute("CREATE TABLE line_bits (file_id integer, context_id integer, numbits blob)")
    db.execute(
        "CREATE TABLE arc (file_id integer, context_id integer, fromno integer, tono integer)"
    )
    for id, (name, lines) in enumerate(files.items(), 1):
        db.execute("INSERT INTO file VALUES (?, ?)", (id, name))
        if arcs:
            ordered = sorted(lines)
            pairs = zip([-1, *ordered], [*ordered, -1], strict=True)
            db.executemany("INSERT INTO arc VALUES (?, 1, ?, ?)", [(id, a, b) for a, b in pairs])
        else:
            db.execute("INSERT INTO line_bits VALUES (?, 1, ?)", (id, numbits(lines)))
    db.commit()
    db.close()


@pytest.mark.parametrize("arcs", [False, True])
def test_from_coverage_reads_lines_and_arcs(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str], arcs: bool
):
    ran_lines = {1, 2, 3, 6, 8, 9, 12, 16}
    coverage_db(project.path / ".coverage", {str(project.path / "load.py"): ran_lines}, arcs=arcs)
    os.utime(project.path / "load.py", (1, 1))
    ran = xpt(capsys, "coverage", "--from-coverage")
    assert ran.code == 1
    assert ran.out.splitlines() == [
        "load.py read_rows: 1 of 2 changed lines not run",
        '  7 |             raise ValueError("missing count")',
        "load.py unused: 1 of 2 changed lines not run",
        "  17 |     return 0",
        "2 of 4 changed lines not run, in 2 functions (since main).",
    ]
    assert xpt(capsys, "coverage", "--from-coverage", "--json").json["source"] == "coverage.py"


def test_from_coverage_leaves_out_unmeasured_and_edited_files(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / "extra.py").write_text("X = 1\n")
    data = project.path / "cov.db"
    coverage_db(data, {"load.py": {6, 7, 16, 17}})
    os.utime(data, (1, 1))
    ran = xpt(capsys, "coverage", "--from-coverage", "cov.db")
    assert (ran.code, ran.out.splitlines()) == (
        0,
        [
            "changed since the coverage data, left out: load.py",
            "not in the coverage data, left out: extra.py",
            "No changed lines with code (since main).",
        ],
    )


def test_from_coverage_with_a_file_that_is_not_coverage_data_is_a_usage_error(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    (project.path / ".coverage").write_text("not sqlite")
    ran = xpt(capsys, "coverage", "--from-coverage")
    assert ran.code == 2
    assert "is not a coverage.py data file" in ran.err
    (project.path / ".coverage").unlink()
    assert (
        "does not exist. Run coverage run -m pytest first."
        in xpt(capsys, "coverage", "--from-coverage").err
    )


def test_from_coverage_reads_real_coverage_data(
    project: pytest.Pytester, capsys: pytest.CaptureFixture[str]
):
    pytest.importorskip("coverage")
    for branch in ([], ["--branch"]):
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", *branch, "-m", "pytest", "-p", "no:xpt"],
            cwd=project.path,
            check=True,
            capture_output=True,
        )
        assert xpt(capsys, "coverage", "--from-coverage", "--json").json["not_run"] == 2


def test_executable_lines_leave_out_blank_lines_comments_and_else():
    code = compile("if x:\n    # c\n\n    y = 1\nelse:\n    y = 2\n", "a.py", "exec")
    assert uncovered.executable(code)[0] == {1, 4, 6}
